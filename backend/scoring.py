"""Score a pass three ways and turn that into a verdict.

The three scores in contract.json are all "probability this pass yields a
usable image", so they are directly comparable:

  climatology    historical base rate of clear sky for this month and hour
  forecast_only  naive trust in the forecast: 1 - cloud/100
  model          LogisticRegression, which knows forecast skill decays with
                 lead time and slides toward climatology for distant passes

Geometry is deterministic, so elevation is kept out of the model and applied
in the verdict instead.
"""

import json
import pickle

import numpy as np

from . import config, solar

_model = None
_climatology = None
_load_error = None


def _ensure_loaded():
    global _model, _climatology, _load_error
    if _model is not None or _load_error is not None:
        return
    try:
        with open(config.MODEL_PATH, "rb") as fh:
            _model = pickle.load(fh)
        with open(config.CLIMATOLOGY_PATH) as fh:
            _climatology = json.load(fh)
    except (OSError, pickle.UnpicklingError) as exc:
        # Degrade to the heuristic blend rather than failing the whole run.
        _load_error = str(exc)


def _cyclic(value, period):
    ang = 2.0 * np.pi * float(value) / period
    return np.sin(ang), np.cos(ang)


def features_for(forecast_cloud_pct, lead_hours, when):
    """Mirror of train_model.build_features column order, minus the noise.

    At inference we have a genuine forecast, so nothing is synthesized here.
    """
    h_sin, h_cos = _cyclic(when.hour + when.minute / 60.0, 24.0)
    d_sin, d_cos = _cyclic(when.timetuple().tm_yday, 365.25)
    return np.array(
        [[forecast_cloud_pct / 100.0, lead_hours / 72.0, h_sin, h_cos, d_sin, d_cos]]
    )


def climatology_score(when):
    _ensure_loaded()
    if _climatology is None:
        return 0.5
    key = f"{when.month}-{when.hour}"
    return float(_climatology["by_month_hour"].get(key, _climatology["overall"]))


def forecast_only_score(forecast_cloud_pct):
    return round(1.0 - forecast_cloud_pct / 100.0, 4)


def model_score(forecast_cloud_pct, lead_hours, when, lat=None, lon=None):
    """P(usable image): clear-sky probability gated by available daylight.

    Only this score knows about darkness. The two baselines stay naive on
    purpose -- that gap is what the three-way comparison is there to show.
    """
    _ensure_loaded()
    if _model is None:
        # Fallback blend: trust the forecast less as lead time grows.
        trust = max(0.0, 1.0 - lead_hours / float(config.HORIZON_HOURS))
        fo = forecast_only_score(forecast_cloud_pct)
        clear = trust * fo + (1.0 - trust) * climatology_score(when)
    else:
        X = features_for(forecast_cloud_pct, lead_hours, when)
        clear = float(_model.predict_proba(X)[0][1])

    # Applied to both paths, or the night-pass bug returns whenever the
    # pickled model is missing.
    daylight = solar.daylight_factor(solar.sun_elevation_deg(when, lat, lon))
    return round(clear * daylight, 4)


def verdict_for(score, max_elevation_deg):
    """good / marginal / poor.

    A confident forecast at low elevation is only marginal: a shallow pass
    means a long atmospheric path and an oblique view.
    """
    if score >= config.VERDICT_GOOD and max_elevation_deg >= config.GOOD_ELEV_DEG:
        return "good"
    if score >= config.VERDICT_MARGINAL:
        return "marginal"
    return config.VERDICT_POOR_LABEL


def score_pass(forecast_cloud_pct, lead_hours, when, max_elevation_deg, lat=None, lon=None):
    """All three scores plus the verdict for one pass."""
    model = model_score(forecast_cloud_pct, lead_hours, when, lat, lon)
    return {
        "scores": {
            "model": model,
            "forecast_only": forecast_only_score(forecast_cloud_pct),
            "climatology": round(climatology_score(when), 4),
        },
        "verdict": verdict_for(model, max_elevation_deg),
    }


def status():
    """Whether the trained model is in use, for generate.py to report."""
    _ensure_loaded()
    if _model is None:
        return f"heuristic fallback ({_load_error})"
    return "sklearn LogisticRegression"
