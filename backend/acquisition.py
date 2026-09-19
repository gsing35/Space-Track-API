"""Time-to-image: when is the first usable image of this target likely?

Operators care less about how good each pass is than about how soon they get
a clear image. This turns the scored pass list into that answer.

Passes close together in time see the same clouds, so they are not separate
chances: Terra and Landsat 20 minutes apart are both clear or both cloudy.
Imaging passes are therefore grouped into weather windows (peaks within
WEATHER_WINDOW_MIN of the window's first pass), and a window succeeds with the
probability of its best pass. Windows are then treated as independent, which
is roughly right a day apart and optimistic for windows a few hours apart.

Pure function over the contract's pass dicts; no I/O.
"""

from datetime import datetime, timezone

from . import config

REASON_GOOD = "earliest good pass"
REASON_BEST = "best available, no good pass in window"
REASON_NONE = "no usable pass in window"
NEAR_BEST = 0.05  # scores this close are a tie; the earlier pass wins
# Windows are treated as independent, which overstates the odds when cloud
# persists for days, so never claim certainty.
P_CAP = 0.99


def _parse(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _is_imaging(p):
    """Counts toward time-to-image: an imaging satellite that, for an area
    target, images at least AOI_GOOD_COVERAGE of it. A sliver is not the image."""
    if not config.SATELLITE_META.get(p["satellite"], {}).get("imaging", True):
        return False
    cov = p.get("aoi_coverage_pct")
    return cov is None or cov >= config.AOI_GOOD_COVERAGE * 100


def _windows(passes):
    """Group imaging passes (already time-sorted) into shared-sky windows."""
    windows = []
    limit_s = config.WEATHER_WINDOW_MIN * 60
    for p in passes:
        peak = _parse(p["peak_utc"])
        if windows and (peak - windows[-1]["first_peak"]).total_seconds() <= limit_s:
            windows[-1]["passes"].append(p)
        else:
            windows.append({"first_peak": peak, "passes": [p]})
    for w in windows:
        w["p"] = max(p["scores"]["model"] for p in w["passes"])
        # The window's time is its first pass that is about as good as its
        # best: optimizing for time, 78% now beats 78.4% ninety minutes later.
        w["best"] = next(
            p for p in w["passes"] if p["scores"]["model"] >= w["p"] - NEAR_BEST
        )
    return windows


def plan(passes, now):
    """The acquisition block for one target. Never raises on an empty list."""
    imaging = sorted(
        (p for p in passes if _is_imaging(p)), key=lambda p: p["peak_utc"]
    )

    def hours_to(p):
        return round((_parse(p["peak_utc"]) - now).total_seconds() / 3600.0, 1)

    good = [p for p in imaging if p["verdict"] == "good"]
    if good:
        rec, reason = good[0], REASON_GOOD
    else:
        # The earliest pass that is about as good as the best one available.
        usable = [p for p in imaging if p["scores"]["model"] > 0]
        rec = None
        if usable:
            top = max(p["scores"]["model"] for p in usable)
            rec = next(p for p in usable if p["scores"]["model"] >= top - NEAR_BEST)
        reason = REASON_BEST if rec else REASON_NONE

    cumulative = []
    p_none = 1.0
    median = None
    for w in _windows(imaging):
        p_none *= 1.0 - w["p"]
        p_by = round(min(1.0 - p_none, P_CAP), 4)
        best = w["best"]
        cumulative.append(
            {
                "pass_id": best["pass_id"],
                "peak_utc": best["peak_utc"],
                "hours": hours_to(best),
                "p_image_by": p_by,
            }
        )
        if median is None and p_by >= 0.5:
            median = hours_to(best)

    def p_within(hours):
        reached = [c["p_image_by"] for c in cumulative if c["hours"] <= hours]
        return reached[-1] if reached else 0.0

    return {
        "recommended_pass_id": rec["pass_id"] if rec else None,
        "recommended_reason": reason,
        "hours_to_recommended": hours_to(rec) if rec else None,
        "p_image_24h": p_within(24),
        "p_image_48h": p_within(48),
        "p_image_horizon": cumulative[-1]["p_image_by"] if cumulative else 0.0,
        "median_hours_to_image": median,
        "weather_window_min": config.WEATHER_WINDOW_MIN,
        "cumulative": cumulative,
    }
