"""Model features, defined once for both training and live scoring.

train_model.py and scoring.py both call build(). Keeping a single definition
is what guarantees the model sees at inference exactly what it saw in
training -- the previous version had two copies that could drift apart.

Every feature is location-aware, so one model serves any point on Earth:
time of day is LOCAL solar time, and season is flipped for the southern
hemisphere so "summer" means summer everywhere.
"""

import numpy as np

FEATURE_NAMES = [
    "forecast_cloud",   # forecast cloud cover for the hour, 0-1
    "lead_day",         # 0-3: how many days ahead the forecast was issued
    "solar_hour_sin",   # local solar time of day
    "solar_hour_cos",
    "season_sin",       # day of year, hemisphere-adjusted
    "season_cos",
    "abs_lat",          # distance from the equator, 0-1
    "clim_clear_rate",  # historical clear-sky rate here this month; NaN if unknown
]

MAX_LEAD_DAY = 3


def lead_day(lead_hours):
    """Bucket lead time the way the training data is bucketed (whole days)."""
    return np.clip(np.floor(np.asarray(lead_hours, dtype=float) / 24.0), 0, MAX_LEAD_DAY)


def build(forecast_cloud_pct, lead_days, epoch_s, lat, lon, clim_clear_rate):
    """Feature matrix, one row per input. Scalars broadcast against arrays."""
    fc, ld, t, la, lo, cl = np.broadcast_arrays(
        np.asarray(forecast_cloud_pct, dtype=float),
        np.asarray(lead_days, dtype=float),
        np.asarray(epoch_s, dtype=float),
        np.asarray(lat, dtype=float),
        np.asarray(lon, dtype=float),
        np.asarray(clim_clear_rate, dtype=float),
    )

    utc_hour = (t / 3600.0) % 24.0
    solar_hour = (utc_hour + lo / 15.0) % 24.0
    h_ang = 2.0 * np.pi * solar_hour / 24.0

    stamps = t.astype("int64").astype("datetime64[s]")
    doy = (stamps.astype("datetime64[D]") - stamps.astype("datetime64[Y]")).astype(int) + 1
    doy = doy + np.where(la < 0, 182.625, 0.0)
    s_ang = 2.0 * np.pi * doy / 365.25

    return np.column_stack(
        [
            fc / 100.0,
            ld,
            np.sin(h_ang),
            np.cos(h_ang),
            np.sin(s_ang),
            np.cos(s_ang),
            np.abs(la) / 90.0,
            cl,
        ]
    )
