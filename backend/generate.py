"""Build data/passes.json in the exact shape of data/contract.json.

    .venv/bin/python -m backend.generate

Run this again right before demoing: lead_time_hours is measured from
generation time, so a stale file reports stale lead times.
"""

import json
from datetime import datetime, timezone

from . import catalog, config, passes, scoring, solar, weather


def _pass_id(target_id, norad_id, start_dt):
    return f"{target_id}-{norad_id}-{start_dt.strftime('%Y%m%dT%H%M')}"


def build(now=None, refresh_weather=False):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)

    targets = catalog.extract_targets()
    raw = passes.compute_passes(targets, start=now)
    clouds = weather.CloudLookup(weather.load_or_fetch(refresh=refresh_weather))

    visible_count = len(raw)
    if config.FOOTPRINT_FILTER:
        raw = [p for p in raw if p["covers_target"]]

    out_passes = []
    missing_cloud = 0
    for p in raw:
        lead_hours = (p["start_dt"] - now).total_seconds() / 3600.0
        cloud = clouds.cloud_at(p["peak_dt"])
        if cloud is None:
            # Beyond the forecast range: fall back to climatology rather than
            # inventing a forecast number.
            missing_cloud += 1
            cloud = int(round((1.0 - scoring.climatology_score(p["peak_dt"])) * 100))

        scored = scoring.score_pass(
            forecast_cloud_pct=cloud,
            lead_hours=lead_hours,
            when=p["peak_dt"],
            max_elevation_deg=p["max_elevation_deg"],
            lat=config.TARGET_LAT,
            lon=config.TARGET_LON,
        )

        out_passes.append(
            {
                "pass_id": _pass_id(config.TARGET_ID, p["norad_id"], p["start_dt"]),
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
                "id": config.TARGET_ID,
                "name": config.TARGET_NAME,
                "lat": config.TARGET_LAT,
                "lon": config.TARGET_LON,
                "passes": out_passes,
            }
        ],
    }
    return doc, {"missing_cloud": missing_cloud, "visible": visible_count}


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
    print(f"  verdicts:   {counts}")
    if stats["missing_cloud"]:
        print(
            f"  note: {stats['missing_cloud']} pass(es) beyond forecast range,"
            f" used climatology"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
