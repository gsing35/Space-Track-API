"""Compute satellite passes over a ground target using SGP4 via Skyfield.

Pure orbital mechanics: no weather, no scoring. Fully offline.
"""

import math
from datetime import timedelta

from skyfield.api import EarthSatellite, load, wgs84

from . import config

EARTH_RADIUS_KM = 6371.0

# builtin=True keeps Skyfield from downloading IERS tables on first use.
# A network call here would hang the demo on bad wifi. SGP4 needs no
# planetary ephemeris, so satellite-vs-ground geometry is fully offline.
_TS = load.timescale(builtin=True)

RISE, CULMINATE, SET = 0, 1, 2


def _utc_z(dt):
    """Format as the contract does: whole seconds, trailing Z.

    Safari's Date parser is strict; microseconds or a missing Z produce NaN.
    """
    dt = dt.replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def surface_distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two ground points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(
        math.radians(lat2)
    ) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compute_passes(targets, lat=None, lon=None, hours=None, min_elev=None, start=None):
    """Return every complete pass above min_elev, sorted by start time.

    A "complete" pass is a rise/culminate/set triple fully inside the window.
    Passes already in progress at the window edges come back from Skyfield as
    unpaired events; those are dropped rather than assumed to be triples,
    which would misalign every pass after them.
    """
    lat = config.TARGET_LAT if lat is None else lat
    lon = config.TARGET_LON if lon is None else lon
    hours = config.HORIZON_HOURS if hours is None else hours
    min_elev = config.MIN_ELEV_DEG if min_elev is None else min_elev

    ground = wgs84.latlon(lat, lon)
    t0 = _TS.now() if start is None else _TS.from_datetime(start)
    t1 = _TS.tt_jd(t0.tt + hours / 24.0)

    results = []
    for tgt in targets:
        sat = EarthSatellite(tgt["tle_line1"], tgt["tle_line2"], tgt["name"], _TS)
        times, events = sat.find_events(ground, t0, t1, altitude_degrees=min_elev)
        difference = sat - ground

        i = 0
        while i < len(events) - 2:
            if not (
                events[i] == RISE
                and events[i + 1] == CULMINATE
                and events[i + 2] == SET
            ):
                i += 1  # partial pass at a window edge; skip this event
                continue

            t_rise, t_peak, t_set = times[i], times[i + 1], times[i + 2]
            start_dt = t_rise.utc_datetime().replace(microsecond=0)
            peak_dt = t_peak.utc_datetime().replace(microsecond=0)
            end_dt = t_set.utc_datetime().replace(microsecond=0)

            alt, _az, _dist = difference.at(t_peak).altaz()

            # Where the nadir camera actually points at closest approach.
            # Peak elevation is also minimum ground distance, so this is the
            # satellite's best shot at covering the target during this pass.
            subpoint = wgs84.subpoint(sat.at(t_peak))
            ground_km = surface_distance_km(
                lat, lon, subpoint.latitude.degrees, subpoint.longitude.degrees
            )
            swath_km = config.SATELLITE_META.get(tgt["name"], {}).get("swath_km", 0)

            results.append(
                {
                    "satellite": tgt["name"],
                    "norad_id": tgt["norad_id"],
                    "footprint_dist_km": round(ground_km, 1),
                    "swath_km": swath_km,
                    "covers_target": ground_km <= swath_km / 2.0,
                    "start_dt": start_dt,
                    "peak_dt": peak_dt,
                    "end_dt": end_dt,
                    "start_utc": _utc_z(start_dt),
                    "peak_utc": _utc_z(peak_dt),
                    "end_utc": _utc_z(end_dt),
                    # Derived from the emitted strings so it always matches
                    # what the frontend parses.
                    "duration_s": int((end_dt - start_dt).total_seconds()),
                    "max_elevation_deg": round(float(alt.degrees), 1),
                }
            )
            i += 3

    results.sort(key=lambda p: p["start_dt"])
    return results


def window_bounds(hours=None, start=None):
    """The (start, end) datetimes compute_passes would use."""
    hours = config.HORIZON_HOURS if hours is None else hours
    t0 = _TS.now().utc_datetime() if start is None else start
    return t0.replace(microsecond=0), (t0 + timedelta(hours=hours)).replace(microsecond=0)
