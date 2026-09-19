"""FastAPI app: a static fast path and a live retasking path.

    .venv/bin/uvicorn backend.main:app --port 8000

GET /api/passes     precomputed Blacksburg file, read once at import (~5 ms)
GET /api/calculate  live SGP4 + Open-Meteo + ML for any lat/lon (~1-3 s)

The GP catalog is parsed from disk once at startup and the configured
satellites are kept in memory, so a click never re-reads the 35 MB file.
Nothing here ever calls the live Space-Track API.
"""

import json

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from . import catalog, config, generate

app = FastAPI(title="Pass Predictor", version="1.1")

# The frontend runs on a different origin; without this the browser blocks
# every request and the failure looks like a backend outage.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
    # Custom headers are invisible to browser JS unless exposed explicitly.
    expose_headers=["X-Weather-Source", "X-Climatology-Source"],
)

_PASSES = None
_LOAD_ERROR = None

_TARGETS = None
_TARGETS_ERROR = None


def _load():
    global _PASSES, _LOAD_ERROR
    try:
        with open(config.PASSES_PATH) as fh:
            _PASSES = json.load(fh)
        _LOAD_ERROR = None
    except (OSError, json.JSONDecodeError) as exc:
        _PASSES = None
        _LOAD_ERROR = str(exc)


def _load_targets():
    """Parse the catalog once and keep only the satellites we compute for.

    Holding all 32,483 records would cost ~130 MB for 8 that are ever read.
    A failure here disables /api/calculate only; the static path still works.
    """
    global _TARGETS, _TARGETS_ERROR
    try:
        _TARGETS = catalog.select_targets(catalog.load_catalog_records())
        _TARGETS_ERROR = None
    except (OSError, ValueError, SystemExit) as exc:
        _TARGETS = None
        _TARGETS_ERROR = str(exc)


_load()
_load_targets()


@app.get("/api/passes")
def get_passes():
    """The full contract document: targets, each with its scored passes."""
    if _PASSES is None:
        raise HTTPException(
            status_code=503,
            detail=f"passes.json unavailable ({_LOAD_ERROR}). "
            f"Run: python -m backend.generate",
        )
    return _PASSES


@app.get("/api/calculate")
def calculate(
    response: Response,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    name: str | None = Query(None, max_length=80),
):
    """Live retasking: compute and score passes over any point on Earth.

    Same schema as /api/passes. If Open-Meteo is slow or down, clouds fall
    back to climatology instead of failing. Headers report what was used:
    X-Weather-Source ("live" / "climatology-fallback") and
    X-Climatology-Source ("table" / "archive" / "latitude-band").
    """
    if _TARGETS is None:
        raise HTTPException(
            status_code=503,
            detail=f"satellite catalog unavailable ({_TARGETS_ERROR})",
        )
    stats = {}
    doc = generate.compute_passes_for_target(lat, lon, name, targets=_TARGETS, stats=stats)
    response.headers["X-Weather-Source"] = stats["weather"]
    response.headers["X-Climatology-Source"] = stats["climatology"]
    return doc


@app.get("/api/health")
def health():
    live = "ok" if _TARGETS is not None else f"unavailable ({_TARGETS_ERROR})"
    if _PASSES is None:
        return {"status": "degraded", "error": _LOAD_ERROR, "calculate": live}
    target = _PASSES["targets"][0]
    return {
        "status": "ok",
        "generated_at": _PASSES["generated_at"],
        "horizon_hours": _PASSES["horizon_hours"],
        "targets": len(_PASSES["targets"]),
        "passes": len(target["passes"]),
        "calculate": live,
        "satellites_loaded": len(_TARGETS) if _TARGETS else 0,
    }


@app.post("/api/reload")
def reload_passes():
    """Re-read passes.json without restarting, after re-running generate."""
    _load()
    if _PASSES is None:
        raise HTTPException(status_code=503, detail=_LOAD_ERROR)
    return {"status": "reloaded", "generated_at": _PASSES["generated_at"]}
