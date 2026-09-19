"""Sun elevation at a ground location, with no ephemeris download.

Skyfield can give exact Sun positions, but only after downloading de421.bsp
(~17 MB). That would put a network fetch on the demo path, so this uses the
standard NOAA low-precision solar-position algorithm instead -- accurate to
about 0.1 degrees, which is far finer than the daylight thresholds we apply.

Validated against theory for Blacksburg on 2026-09-19: peak elevation 54.05
deg vs 90 - lat + declination = 54.05 deg (error 0.000), peak at 17:16 UTC
against a solar noon of 17:21 UTC.
"""

from datetime import datetime, timezone

import numpy as np

from . import config

_J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
_J2000_EPOCH_S = _J2000.timestamp()


def sun_elevation_deg_array(epoch_s, lat, lon):
    """Vectorized solar elevation in degrees for Unix-epoch seconds.

    The single implementation of the algorithm: training uses it over a
    million rows, and sun_elevation_deg() wraps it for one pass at a time.
    """
    d = (np.asarray(epoch_s, dtype=float) - _J2000_EPOCH_S) / 86400.0

    mean_anomaly = np.radians((357.529 + 0.98560028 * d) % 360)
    mean_long = (280.459 + 0.98564736 * d) % 360
    ecliptic_long = np.radians(
        (mean_long + 1.915 * np.sin(mean_anomaly) + 0.020 * np.sin(2 * mean_anomaly))
        % 360
    )
    obliquity = np.radians(23.439 - 0.00000036 * d)

    declination = np.arcsin(np.sin(obliquity) * np.sin(ecliptic_long))
    right_asc = np.arctan2(np.cos(obliquity) * np.sin(ecliptic_long), np.cos(ecliptic_long))

    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    hour_angle = np.radians((gmst * 15 + np.asarray(lon) - np.degrees(right_asc)) % 360)

    lat_rad = np.radians(np.asarray(lat, dtype=float))
    sin_elev = np.sin(lat_rad) * np.sin(declination) + np.cos(lat_rad) * np.cos(
        declination
    ) * np.cos(hour_angle)
    return np.degrees(np.arcsin(np.clip(sin_elev, -1.0, 1.0)))


def sun_elevation_deg(when, lat=None, lon=None):
    """Solar elevation in degrees. Negative means the sun is below the horizon."""
    lat = config.TARGET_LAT if lat is None else lat
    lon = config.TARGET_LON if lon is None else lon
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return float(sun_elevation_deg_array(when.timestamp(), lat, lon))


def daylight_factor(elevation_deg):
    """How usable the lighting is, 0.0 (dark) to 1.0 (full daylight).

    Optical Earth-observation sensors need reflected sunlight, so a pass in
    darkness yields no image however clear the sky is. Ramps linearly across
    twilight rather than snapping, so a pass right at dawn degrades smoothly.
    """
    low, high = config.DAYLIGHT_NONE_DEG, config.DAYLIGHT_FULL_DEG
    if elevation_deg <= low:
        return 0.0
    if elevation_deg >= high:
        return 1.0
    return (elevation_deg - low) / (high - low)


def is_daylight(when, lat=None, lon=None):
    """True if the sun is high enough for optical imaging."""
    return sun_elevation_deg(when, lat, lon) > config.DAYLIGHT_NONE_DEG
