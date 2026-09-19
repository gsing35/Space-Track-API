"""FastAPI app: a static fast path and a live retasking path.

    .venv/bin/uvicorn backend.main:app --port 8000

GET /api/passes     precomputed Blacksburg file, read once at import (~5 ms);
                    recomputed in memory once it is over an hour old, so lead
                    times and time-to-image stay true during a long demo
GET /api/calculate  live SGP4 + Open-Meteo + ML for any lat/lon or area (~1-3 s)
GET /api/geocode    place-name search (Open-Meteo geocoding)
GET /api/clouds     forecast cloud-cover grid around a point, for the overlay

The GP catalog is parsed from disk once at startup and the configured
satellites are kept in memory, so a click never re-reads the 35 MB file.
Nothing here ever calls the live Space-Track API.
"""

import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from . import aoi as aoi_mod, catalog, config, generate, weather

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


def _age_s(doc):
    made = datetime.strptime(doc["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
    return (datetime.now(timezone.utc) - made.replace(tzinfo=timezone.utc)).total_seconds()


def _refresh_if_stale():
    """Swap in a fresh in-memory Blacksburg document when the file has aged.

    Every lead time and "image in X hours" is relative to generated_at, so a
    file from this morning is wrong by the afternoon. Never writes to disk;
    on any failure the existing document is kept.
    """
    global _PASSES
    if _TARGETS is None or _age_s(_PASSES) < config.PASSES_MAX_AGE_S:
        return
    try:
        _PASSES = generate.compute_passes_for_target(
            config.TARGET_LAT,
            config.TARGET_LON,
            config.TARGET_NAME,
            target_id=config.TARGET_ID,
            targets=_TARGETS,
        )
    except Exception:  # noqa: BLE001 -- a stale answer beats no answer
        pass


@app.get("/api/passes")
def get_passes():
    """The full contract document: targets, each with its scored passes."""
    if _PASSES is None:
        raise HTTPException(
            status_code=503,
            detail=f"passes.json unavailable ({_LOAD_ERROR}). "
            f"Run: python -m backend.generate",
        )
    _refresh_if_stale()
    return _PASSES


def _parse_sats(text):
    """'39084,49260' -> {39084, 49260}, checked against the loaded satellites."""
    if text is None:
        return None
    try:
        ids = {int(x) for x in text.split(",") if x.strip()}
    except ValueError:
        raise HTTPException(422, detail="sats must be comma-separated NORAD IDs") from None
    known = {t["norad_id"] for t in _TARGETS}
    unknown = ids - known
    if unknown:
        raise HTTPException(
            422, detail=f"unknown NORAD ID(s) {sorted(unknown)}; loaded: {sorted(known)}"
        )
    return ids


@app.get("/api/calculate")
def calculate(
    response: Response,
    lat: float | None = Query(None, ge=-90.0, le=90.0),
    lon: float | None = Query(None, ge=-180.0, le=180.0),
    name: str | None = Query(None, max_length=80),
    sats: str | None = Query(None, max_length=200),
    aoi: str | None = Query(None, max_length=1000),
):
    """Live retasking: compute and score passes over any point or area.

    Same schema as /api/passes. If Open-Meteo is slow or down, clouds fall
    back to climatology instead of failing. Headers report what was used:
    X-Weather-Source ("live" / "climatology-fallback") and
    X-Climatology-Source ("table" / "archive" / "latitude-band").

    sats  comma-separated NORAD IDs; only these satellites produce passes
    aoi   area target "lat,lon;lat,lon;lat,lon" (replaces lat/lon)
    """
    if _TARGETS is None:
        raise HTTPException(
            status_code=503,
            detail=f"satellite catalog unavailable ({_TARGETS_ERROR})",
        )
    polygon = None
    if aoi:
        try:
            polygon = aoi_mod.parse(aoi)
        except ValueError as exc:
            raise HTTPException(422, detail=f"aoi: {exc}") from None
    elif lat is None or lon is None:
        raise HTTPException(422, detail="give lat and lon, or an aoi polygon")
    sat_ids = _parse_sats(sats)

    stats = {}
    doc = generate.compute_passes_for_target(
        lat, lon, name, targets=_TARGETS, stats=stats, aoi=polygon, sat_ids=sat_ids
    )
    response.headers["X-Weather-Source"] = stats["weather"]
    response.headers["X-Climatology-Source"] = stats["climatology"]
    return doc


@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=1, max_length=80)):
    """Place-name search: up to 5 matches as {name, admin1, country, lat, lon}."""
    return {"query": q, "results": weather.geocode(q)}


@app.get("/api/clouds")
def clouds(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
):
    """Hourly forecast cloud cover on a grid around a point, for the overlay."""
    grid = weather.cloud_grid(lat, lon)
    if grid is None:
        raise HTTPException(503, detail="cloud forecast unavailable (Open-Meteo unreachable)")
    return grid


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
        "tle_max_age_hours": (_PASSES.get("tle_age") or {}).get("max_hours"),
    }


@app.post("/api/reload")
def reload_passes():
    """Re-read passes.json without restarting, after re-running generate."""
    _load()
    if _PASSES is None:
        raise HTTPException(status_code=503, detail=_LOAD_ERROR)
    return {"status": "reloaded", "generated_at": _PASSES["generated_at"]}
