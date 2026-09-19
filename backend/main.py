"""FastAPI app serving the precomputed passes file.

    .venv/bin/uvicorn backend.main:app --port 8000

The 35 MB catalog is never opened here. passes.json is read once at import
and held in memory, so every request is a dict serialization.
"""

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config

app = FastAPI(title="Pass Predictor", version="1.0")

# The frontend runs on a different origin; without this the browser blocks
# every request and the failure looks like a backend outage.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

_PASSES = None
_LOAD_ERROR = None


def _load():
    global _PASSES, _LOAD_ERROR
    try:
        with open(config.PASSES_PATH) as fh:
            _PASSES = json.load(fh)
        _LOAD_ERROR = None
    except (OSError, json.JSONDecodeError) as exc:
        _PASSES = None
        _LOAD_ERROR = str(exc)


_load()


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


@app.get("/api/health")
def health():
    if _PASSES is None:
        return {"status": "degraded", "error": _LOAD_ERROR}
    target = _PASSES["targets"][0]
    return {
        "status": "ok",
        "generated_at": _PASSES["generated_at"],
        "horizon_hours": _PASSES["horizon_hours"],
        "targets": len(_PASSES["targets"]),
        "passes": len(target["passes"]),
    }


@app.post("/api/reload")
def reload_passes():
    """Re-read passes.json without restarting, after re-running generate."""
    _load()
    if _PASSES is None:
        raise HTTPException(status_code=503, detail=_LOAD_ERROR)
    return {"status": "reloaded", "generated_at": _PASSES["generated_at"]}
