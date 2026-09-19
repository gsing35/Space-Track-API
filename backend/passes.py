"""Compute satellite passes over a ground target using SGP4 via Skyfield.

Pure orbital mechanics: no weather, no scoring. Fully offline.
"""

import math
from datetime import timedelta

import numpy as np
from skyfield.api import EarthSatellite, load, wgs84
from skyfield.sgp4lib import theta_GMST1982

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


# Ground speed of a low-Earth-orbit sub-satellite point is ~7 km/s, so a
# coarse step can jump clean over a narrow swath. Candidates are found with
# this much slack, then refined at FINE_STEP_S.
_COARSE_MARGIN_KM = 80.0
FINE_STEP_S = 2


def _distances_km(lat_deg, lon_deg, samples):
    """Great-circle distance from each track point (N,) to each sample (M,): (N, M)."""
    lat1 = np.radians(lat_deg)[:, None]
    lon1 = np.radians(lon_deg)[:, None]
    lat2 = np.radians([s[0] for s in samples])[None, :]
    lon2 = np.radians([s[1] for s in samples])[None, :]
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


# WGS84 ellipsoid
_WGS84_A = 6378.137
_WGS84_E2 = 6.69437999014e-3


def _track(sat, t0, offsets_s):
    """Earth-fixed position (km) and geodetic lat/lon (deg) at t0 + offsets.

    Straight from SGP4's TEME output rotated by Greenwich sidereal time, the
    way satellite.js does it on the frontend. Skyfield's full conversion adds
    nutation (~30 m of axis wobble) at ~60 ms per call, which made area
    requests take 5 s; for km-wide swaths the difference is invisible.
    """
    whole = np.full(len(offsets_s), t0.whole)
    frac = t0.ut1_fraction + np.asarray(offsets_s, dtype=float) / 86400.0
    _err, r, _v = sat.model.sgp4_array(whole, frac)
    theta, _ = theta_GMST1982(whole, frac)
    x, y, z = r[:, 0], r[:, 1], r[:, 2]
    xe = x * np.cos(theta) + y * np.sin(theta)
    ye = -x * np.sin(theta) + y * np.cos(theta)
    lon = np.degrees(np.arctan2(ye, xe))
    p = np.hypot(xe, ye)
    lat = np.arctan2(z, p * (1 - _WGS84_E2))
    for _ in range(3):  # converges to well under a meter
        n = _WGS84_A / np.sqrt(1 - _WGS84_E2 * np.sin(lat) ** 2)
        lat = np.arctan2(z + _WGS84_E2 * n * np.sin(lat), p)
    return np.stack([xe, ye, z], axis=1), np.degrees(lat), lon


def _elevation_deg(sat_ecef, lat, lon):
    """Elevation of a satellite (Earth-fixed km) seen from a ground point."""
    la, lo = math.radians(lat), math.radians(lon)
    n = _WGS84_A / math.sqrt(1 - _WGS84_E2 * math.sin(la) ** 2)
    ground = np.array(
        [n * math.cos(la) * math.cos(lo), n * math.cos(la) * math.sin(lo),
         n * (1 - _WGS84_E2) * math.sin(la)]
    )
    up = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    rel = sat_ecef - ground
    return math.degrees(math.asin(float(rel @ up) / float(np.linalg.norm(rel))))


def _runs(mask):
    """(start, end) index pairs of contiguous True stretches."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1))


def compute_area_passes(targets, samples, centroid, hours=None, start=None):
    """Passes whose sensor swath images any part of an area.

    `samples` are points standing for the area (see aoi.sample_points), with
    the centroid last. Unlike compute_passes there is no elevation cutoff:
    what matters for an area is whether the camera's strip on the ground
    touches it. Each pass reports aoi_coverage_pct, the share of sample points
    it imaged. Same dict shape as compute_passes otherwise.
    """
    hours = config.HORIZON_HOURS if hours is None else hours
    t0 = _TS.now() if start is None else _TS.from_datetime(start)
    total_s = hours * 3600
    coarse = np.arange(0, total_s + 1, config.AREA_STEP_S)
    start_dt0 = t0.utc_datetime().replace(microsecond=0)

    results = []
    for tgt in targets:
        sat = EarthSatellite(tgt["tle_line1"], tgt["tle_line2"], tgt["name"], _TS)
        swath_km = config.SATELLITE_META.get(tgt["name"], {}).get("swath_km", 0)
        radius = swath_km / 2.0

        _, lat, lon = _track(sat, t0, coarse)
        near = (_distances_km(lat, lon, samples) <= radius + _COARSE_MARGIN_KM).any(axis=1)

        for i0, i1 in _runs(near):
            lo = max(0, coarse[i0] - config.AREA_STEP_S)
            hi = min(total_s, coarse[i1] + config.AREA_STEP_S)
            fine = np.arange(lo, hi + 1, FINE_STEP_S)
            ecef, flat, flon = _track(sat, t0, fine)
            dist = _distances_km(flat, flon, samples)
            covered = dist <= radius
            hit = covered.any(axis=1)
            if not hit.any():
                continue
            for j0, j1 in _runs(hit):
                if (j0 == 0 and lo == 0) or (j1 == len(fine) - 1 and hi == total_s):
                    continue  # already under way at a window edge: partial
                seg = slice(j0, j1 + 1)
                peak_i = j0 + int(np.argmin(dist[seg, -1]))
                start_dt = start_dt0 + timedelta(seconds=int(fine[j0]))
                peak_dt = start_dt0 + timedelta(seconds=int(fine[peak_i]))
                end_dt = start_dt0 + timedelta(seconds=int(fine[j1]))
                elev = _elevation_deg(ecef[peak_i], *centroid)
                coverage = float(covered[seg].any(axis=0).mean())
                results.append(
                    {
                        "satellite": tgt["name"],
                        "norad_id": tgt["norad_id"],
                        "footprint_dist_km": round(float(dist[peak_i, -1]), 1),
                        "swath_km": swath_km,
                        "covers_target": True,
                        "aoi_coverage_pct": int(round(coverage * 100)),
                        "start_dt": start_dt,
                        "peak_dt": peak_dt,
                        "end_dt": end_dt,
                        "start_utc": _utc_z(start_dt),
                        "peak_utc": _utc_z(peak_dt),
                        "end_utc": _utc_z(end_dt),
                        "duration_s": int((end_dt - start_dt).total_seconds()),
                        "max_elevation_deg": round(elev, 1),
                    }
                )

    results.sort(key=lambda p: p["start_dt"])
    return results
