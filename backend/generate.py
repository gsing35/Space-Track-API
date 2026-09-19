"""Compute scored passes for a ground target, in the shape of data/contract.json.

Two entry points share one core (build_document), so the static file and the
live endpoint can never drift apart:

    compute_passes_for_target(lat, lon, name)   live, used by GET /api/calculate
    main()                                      static Blacksburg -> passes.json

    .venv/bin/python -m backend.generate

Re-run main() right before demoing: lead_time_hours is measured from
generation time, so a stale file reports stale lead times.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import acquisition, catalog, config, passes, scoring, solar, weather


def _pass_id(target_id, norad_id, start_dt):
    return f"{target_id}-{norad_id}-{start_dt.strftime('%Y%m%dT%H%M')}"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "target"


def build_document(lat, lon, target_name, target_id, targets, clouds, now=None):
    """The shared core: SGP4 passes, footprint filter, weather, ML scoring.

    `clouds` is a weather.CloudLookup, or None when no forecast is available;
    every pass then falls back to climatology rather than failing.
    Returns (doc, stats).
    """
    now = now or datetime.now(timezone.utc).replace(microsecond=0)

    raw = passes.compute_passes(targets, lat=lat, lon=lon, start=now)

    visible_count = len(raw)
    if config.FOOTPRINT_FILTER:
        raw = [p for p in raw if p["covers_target"]]

    out_passes = []
    missing_cloud = 0
    clim_sources = set()
    for p in raw:
        lead_hours = (p["start_dt"] - now).total_seconds() / 3600.0
        clim, clim_source = weather.point_climatology(lat, lon, p["peak_dt"].month, now=now)
        clim_sources.add(clim_source)

        cloud = clouds.cloud_at(p["peak_dt"]) if clouds is not None else None
        forecast_available = cloud is not None
        if not forecast_available:
            # No forecast for this hour: report this location's typical cloud
            # in the contract field, and score from climatology alone.
            missing_cloud += 1
            cloud = int(round((1.0 - scoring.climatology_score(clim)) * 100))

        scored = scoring.score_pass(
            forecast_cloud_pct=cloud,
            lead_hours=lead_hours,
            when=p["peak_dt"],
            max_elevation_deg=p["max_elevation_deg"],
            lat=lat,
            lon=lon,
            clim=clim,
            forecast_available=forecast_available,
        )

        out_passes.append(
            {
                "pass_id": _pass_id(target_id, p["norad_id"], p["start_dt"]),
                "satellite": p["satellite"],
                "norad_id": p["norad_id"],
                "start_utc": p["start_utc"],
                "peak_utc": p["peak_utc"],
                "end_utc": p["end_utc"],
                "duration_s": p["duration_s"],
                "max_elevation_deg": p["max_elevation_deg"],
                "lead_time_hours": round(lead_hours, 1),
                "cloud_forecast_pct": int(cloud),
                "scores": scored["scores"],
                "verdict": scored["verdict"],
                "limited_by": scored["limited_by"],
                "imaging": config.SATELLITE_META[p["satellite"]]["imaging"],
            }
        )

    # The frontend's globe iterates this array to draw each satellite, its
    # orbit path and its ground swath. Every satellite is listed even if it
    # produced no passes, so its orbit still renders.
    satellites = [
        {
            "id": t["name"],
            "swathKm": config.SATELLITE_META[t["name"]]["swath_km"],
            "colorHex": config.SATELLITE_META[t["name"]]["color_hex"],
            "tle1": t["tle_line1"],
            "tle2": t["tle_line2"],
        }
        for t in targets
    ]

    doc = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_hours": config.HORIZON_HOURS,
        "satellites": satellites,
        "targets": [
            {
                "id": target_id,
                "name": target_name,
                "lat": float(lat),
                "lon": float(lon),
                "passes": out_passes,
                "acquisition": acquisition.plan(out_passes, now),
            }
        ],
    }
    stats = {
        "missing_cloud": missing_cloud,
        "visible": visible_count,
        "climatology": ",".join(sorted(s for s in clim_sources if s)) or "none",
    }
    return doc, stats


def compute_passes_for_target(
    lat, lon, target_name=None, *, target_id=None, targets=None, now=None, stats=None
):
    """Live computation for an arbitrary point. Same schema as passes.json.

    `targets` is the in-memory satellite list the API selects once at
    startup; if omitted, the 2 KB data/targets.json is read instead. Weather
    is fetched live from Open-Meteo (cached per ~1 km for 30 minutes) and
    never written to disk. Pass a dict as `stats` to receive run details,
    including stats["weather"] = "live" or "climatology-fallback".
    """
    target_name = target_name or (
        f"{abs(lat):.4f}°{'N' if lat >= 0 else 'S'}, "
        f"{abs(lon):.4f}°{'E' if lon >= 0 else 'W'}"
    )
    if targets is None:
        targets = catalog.load_targets()

    # Fetch the forecast and this point's climatology concurrently, so a new
    # click costs one round-trip of latency rather than three. Climatology
    # results land in weather's in-memory cache for build_document to reuse.
    start = now or datetime.now(timezone.utc)
    months = {start.month, (start + timedelta(hours=config.HORIZON_HOURS)).month}
    with ThreadPoolExecutor(max_workers=1 + len(months)) as pool:
        forecast_job = pool.submit(weather.live_forecast, lat, lon)
        for m in months:
            pool.submit(weather.point_climatology, lat, lon, m, start)
        forecast = forecast_job.result()
    clouds = weather.CloudLookup(forecast) if forecast else None

    doc, run_stats = build_document(
        lat, lon, target_name, target_id or _slug(target_name), targets, clouds, now=now
    )
    if stats is not None:
        stats.update(run_stats)
        stats["weather"] = "live" if forecast else "climatology-fallback"
    return doc


def build(now=None, refresh_weather=False):
    """Static Blacksburg run, from the on-disk catalog and weather cache."""
    targets = catalog.extract_targets()
    clouds = weather.CloudLookup(weather.load_or_fetch(refresh=refresh_weather))
    return build_document(
        config.TARGET_LAT,
        config.TARGET_LON,
        config.TARGET_NAME,
        config.TARGET_ID,
        targets,
        clouds,
        now=now,
    )


def main():
    doc, stats = build()
    config.PASSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(config.PASSES_PATH, "w") as fh:
        json.dump(doc, fh, indent=2)

    p = doc["targets"][0]["passes"]
    counts = {}
    for row in p:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1

    daylight = sum(
        1
        for row in p
        if solar.is_daylight(
            datetime.strptime(row["peak_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        )
    )

    print(f"Wrote {config.PASSES_PATH}")
    print(f"  satellites: {len(doc['satellites'])}")
    print(
        f"  passes:     {stats['visible']} visible >= {config.MIN_ELEV_DEG:.0f}deg"
        f" -> {len(p)} with swath coverage"
        f"{'' if config.FOOTPRINT_FILTER else ' (filter OFF)'}"
    )
    print(f"  lighting:   {daylight} daylight / {len(p) - daylight} night")
    print(f"  scoring:    {scoring.status()}")
    print(f"  climatology: {stats['climatology']}")
    print(f"  verdicts:   {counts}")
    acq = doc["targets"][0]["acquisition"]
    print(
        f"  time-to-image: {acq['recommended_pass_id']} in {acq['hours_to_recommended']} h"
        f" ({acq['recommended_reason']}); P(image) 24h {acq['p_image_24h']:.0%},"
        f" 48h {acq['p_image_48h']:.0%}, 72h {acq['p_image_horizon']:.0%}"
    )
    if stats["missing_cloud"]:
        print(
            f"  note: {stats['missing_cloud']} pass(es) beyond forecast range,"
            f" used climatology"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
