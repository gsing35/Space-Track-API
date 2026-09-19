"""Score a pass three ways and turn that into a verdict.

The three scores in contract.json are all "probability this pass yields a
usable image", so they are directly comparable:

  climatology    historical clear-sky rate at THIS location for the month
  forecast_only  naive trust in the forecast: 1 - cloud/100
  model          global model trained on real forecasts vs observed outcomes
                 at 27 locations, validated on 8 unseen ones: a calibrated,
                 lead-time-aware reading of the forecast (see train_model.py)

Only the model knows about darkness; the baselines stay naive on purpose.
Geometry is deterministic, so elevation is applied in the verdict instead.
"""

import math
import pickle

from . import config, features, solar

_bundle = None
_load_error = None


def _ensure_loaded():
    global _bundle, _load_error
    if _bundle is not None or _load_error is not None:
        return
    try:
        with open(config.MODEL_PATH, "rb") as fh:
            loaded = pickle.load(fh)
        if not (isinstance(loaded, dict) and loaded.get("version") == 2):
            raise ValueError("model.pkl is the old format; run python -m backend.train_model")
        _bundle = loaded
    except (OSError, pickle.UnpicklingError, ValueError, EOFError) as exc:
        # Degrade to the heuristic blend rather than failing the whole run.
        _load_error = str(exc)


def climatology_score(clim):
    """The location's historical clear-sky rate, or the training base rate."""
    if clim is not None:
        return float(clim)
    _ensure_loaded()
    return float(_bundle["base_rate"]) if _bundle else 0.5


def forecast_only_score(forecast_cloud_pct):
    return round(1.0 - forecast_cloud_pct / 100.0, 4)


def model_score(forecast_cloud_pct, lead_hours, when, lat, lon, clim=None, forecast_available=True):
    """P(usable image): clear-sky probability gated by available daylight.

    With no real forecast for the hour, the honest answer is the location's
    climatology -- feeding a climatology-derived number through the forecast
    calibration would dress a guess up as a forecast.
    """
    _ensure_loaded()
    if not forecast_available:
        clear = climatology_score(clim)
    elif _bundle is None:
        # Fallback blend: trust the forecast less as lead time grows.
        trust = max(0.0, 1.0 - lead_hours / float(config.HORIZON_HOURS))
        fo = forecast_only_score(forecast_cloud_pct)
        clear = trust * fo + (1.0 - trust) * climatology_score(clim)
    else:
        X = features.build(
            forecast_cloud_pct,
            features.lead_day(lead_hours),
            when.timestamp(),
            lat,
            lon,
            math.nan if clim is None else clim,
        )[:, _bundle["columns"]]
        clear = float(_bundle["model"].predict_proba(X)[0][1])

    # Applied to both paths, or the night-pass bug returns whenever the
    # pickled model is missing.
    daylight = solar.daylight_factor(solar.sun_elevation_deg(when, lat, lon))
    return round(clear * daylight, 4)


def verdict_for(score, max_elevation_deg):
    """good / marginal / bad.

    A confident forecast at low elevation is only marginal: a shallow pass
    means a long atmospheric path and an oblique view.
    """
    if score >= config.VERDICT_GOOD and max_elevation_deg >= config.GOOD_ELEV_DEG:
        return "good"
    if score >= config.VERDICT_MARGINAL:
        return "marginal"
    return config.VERDICT_POOR_LABEL


def score_pass(
    forecast_cloud_pct, lead_hours, when, max_elevation_deg, lat, lon, clim=None,
    forecast_available=True,
):
    """All three scores plus the verdict for one pass."""
    model = model_score(
        forecast_cloud_pct, lead_hours, when, lat, lon, clim, forecast_available
    )
    return {
        "scores": {
            "model": model,
            "forecast_only": forecast_only_score(forecast_cloud_pct),
            "climatology": round(climatology_score(clim), 4),
        },
        "verdict": verdict_for(model, max_elevation_deg),
    }


def status():
    """Whether the trained model is in use, for generate.py to report."""
    _ensure_loaded()
    if _bundle is None:
        return f"heuristic fallback ({_load_error})"
    return f"global HistGradientBoosting on {_bundle['features']} (27 training locations)"
