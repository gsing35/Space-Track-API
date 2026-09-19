"""Train the usable-image classifier on real historical cloud observations.

Run once, offline:  .venv/bin/python -m backend.train_model

Data: ~3 years of hourly cloud cover for the ground target from Open-Meteo's
free archive API. Label: observed cloud cover below CLOUD_USABLE_PCT means the
pass would have yielded a usable image.

ONE DOCUMENTED SIMPLIFICATION: free historical *forecasts* are not available,
only historical observations. So the forecast feature is synthesized from the
observation by adding noise whose spread grows with lead time. That is exactly
what makes scores.model differ from scores.forecast_only -- the model learns
that a forecast 60 hours out deserves less trust than one 6 hours out, and
slides toward the climatological base rate as lead time grows.
"""

import json
import pickle
from datetime import datetime

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

from . import config
from .weather import _get_json

RNG_SEED = 20260919
# Forecast error grows with lead time: ~5% at 0h, ~37% at 72h.
NOISE_BASE = 5.0
NOISE_PER_HOUR = 0.45


def fetch_history(lat=None, lon=None, start=None, end=None):
    lat = config.TARGET_LAT if lat is None else lat
    lon = config.TARGET_LON if lon is None else lon
    data = _get_json(
        config.ARCHIVE_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": start or config.TRAIN_START,
            "end_date": end or config.TRAIN_END,
            "hourly": "cloud_cover",
            "timezone": "UTC",
        },
        timeout=90,
    )
    stamps, cover = [], []
    for s, c in zip(data["hourly"]["time"], data["hourly"]["cloud_cover"]):
        if c is None:
            continue
        stamps.append(datetime.fromisoformat(s))
        cover.append(float(c))
    return stamps, np.asarray(cover)


def cyclic(values, period):
    ang = 2.0 * np.pi * np.asarray(values, dtype=float) / period
    return np.sin(ang), np.cos(ang)


def build_features(stamps, observed, lead_hours):
    """Feature matrix. Column order is mirrored in scoring.features_for()."""
    rng = np.random.default_rng(RNG_SEED)
    sigma = NOISE_BASE + NOISE_PER_HOUR * lead_hours
    forecast = np.clip(observed + rng.normal(0.0, sigma), 0.0, 100.0)

    hour = np.array([s.hour + s.minute / 60.0 for s in stamps])
    doy = np.array([s.timetuple().tm_yday for s in stamps])
    h_sin, h_cos = cyclic(hour, 24.0)
    d_sin, d_cos = cyclic(doy, 365.25)

    return np.column_stack(
        [forecast / 100.0, lead_hours / 72.0, h_sin, h_cos, d_sin, d_cos]
    )


def build_climatology(stamps, observed):
    """Base rate of clear sky per (month, hour) -- the climatology baseline."""
    table = {}
    buckets = {}
    for stamp, cover in zip(stamps, observed):
        key = f"{stamp.month}-{stamp.hour}"
        buckets.setdefault(key, []).append(1.0 if cover < config.CLOUD_USABLE_PCT else 0.0)
    for key, vals in buckets.items():
        table[key] = round(float(np.mean(vals)), 4)
    overall = float(np.mean([1.0 if c < config.CLOUD_USABLE_PCT else 0.0 for c in observed]))
    return {"by_month_hour": table, "overall": round(overall, 4)}


def main():
    print(f"Fetching {config.TRAIN_START} -> {config.TRAIN_END} hourly cloud cover...")
    stamps, observed = fetch_history()
    print(f"  {len(stamps)} hourly observations")

    labels = (observed < config.CLOUD_USABLE_PCT).astype(int)
    print(f"  clear-sky base rate: {labels.mean() * 100:.1f}%")

    rng = np.random.default_rng(RNG_SEED)
    lead = rng.uniform(0.0, float(config.HORIZON_HOURS), size=len(stamps))
    X = build_features(stamps, observed, lead)

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, labels, test_size=0.2, random_state=RNG_SEED, stratify=labels
    )
    model = LogisticRegression(max_iter=1000)
    model.fit(X_tr, y_tr)

    acc = model.score(X_te, y_te)
    base = max(y_te.mean(), 1 - y_te.mean())
    print(f"  test accuracy: {acc * 100:.1f}%  (majority-class baseline {base * 100:.1f}%)")

    config.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(config.MODEL_PATH, "wb") as fh:
        pickle.dump(model, fh)
    print(f"  wrote {config.MODEL_PATH}")

    clim = build_climatology(stamps, observed)
    with open(config.CLIMATOLOGY_PATH, "w") as fh:
        json.dump(clim, fh, indent=2)
    print(f"  wrote {config.CLIMATOLOGY_PATH} ({len(clim['by_month_hour'])} buckets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
