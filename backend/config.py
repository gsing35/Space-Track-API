"""All tunable constants for the pass-prediction backend.

Single place to change things on stage. No logic lives here.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

# Inputs
CATALOG_PATH = DATA_DIR / "gp_catalog.json"
CONTRACT_PATH = DATA_DIR / "contract.json"

# Generated artifacts
TARGETS_PATH = DATA_DIR / "targets.json"
WEATHER_CACHE_PATH = DATA_DIR / "weather_cache.json"
CLIMATOLOGY_PATH = DATA_DIR / "climatology.json"
MODEL_PATH = DATA_DIR / "model.pkl"
PASSES_PATH = DATA_DIR / "passes.json"

# Ground target
TARGET_ID = "blacksburg"
TARGET_NAME = "Blacksburg, VA"
TARGET_LAT = 37.2296
TARGET_LON = -80.4139

# Pass geometry
HORIZON_HOURS = 72
MIN_ELEV_DEG = 20.0

# Earth-observation satellites, resolved by EXACT catalog name.
# Names are inconsistent in the catalog on purpose: "SENTINEL 2A"/"2B" use a
# space, "SENTINEL-2C" uses a hyphen. Substring matching also pulls in
# SKYTERRA 1, TERRA SAR X and SAC-D (AQUARIUS), so never match loosely.
SATELLITE_NAMES = [
    "LANDSAT 8",
    "LANDSAT 9",
    "SENTINEL 2A",
    "SENTINEL 2B",
    "SENTINEL-2C",
    "TERRA",
    "AQUA",
    "ISS (ZARYA)",
]

# Sensor swath width and globe color, per satellite. Swaths are the real
# instrument values: Landsat OLI 185 km, Sentinel-2 MSI 290 km, MODIS 2330 km.
# The ISS is not an EO asset -- it stands in as a wide-FOV visual.
SATELLITE_META = {
    "LANDSAT 8": {"swath_km": 185, "color_hex": "#f4c430", "imaging": True},
    "LANDSAT 9": {"swath_km": 185, "color_hex": "#ffa000", "imaging": True},
    "SENTINEL 2A": {"swath_km": 290, "color_hex": "#00ff00", "imaging": True},
    "SENTINEL 2B": {"swath_km": 290, "color_hex": "#00c853", "imaging": True},
    "SENTINEL-2C": {"swath_km": 290, "color_hex": "#69f0ae", "imaging": True},
    "TERRA": {"swath_km": 2330, "color_hex": "#00e5ff", "imaging": True},
    "AQUA": {"swath_km": 2330, "color_hex": "#2979ff", "imaging": True},
    "ISS (ZARYA)": {"swath_km": 1200, "color_hex": "#ff3333", "imaging": False},
}

# Drop passes whose sensor swath never covers the target. A satellite can sit
# 20 degrees above the horizon while its nadir camera images 800 km away.
FOOTPRINT_FILTER = True

# Time-to-image. Passes whose peaks fall within this many minutes see much the same
# sky, so they count as one chance at a clear image, not several independent
# ones. Only satellites with "imaging": True above count toward it.
WEATHER_WINDOW_MIN = 180

# Area targets (AOI = area of interest). One cloud forecast at the centroid
# stands in for the whole area, which stops being honest past a few hundred km.
AOI_MAX_VERTICES = 20
AOI_MAX_SPAN_KM = 600
AOI_GOOD_COVERAGE = 0.5  # a pass must image at least half the area to be "good"
AREA_STEP_S = 20  # ground-track sampling step when finding area passes

# TLE freshness. SGP4 error grows with TLE age: fine for a few days, drifting
# by kilometers after a week. Refresh with tools/spacetrack_gp.py.
TLE_STALE_HOURS = 72
TLE_OLD_HOURS = 168

# Scoring
CLOUD_USABLE_PCT = 30  # observed cloud below this counts as a usable image
VERDICT_GOOD = 0.70
VERDICT_MARGINAL = 0.40

# The frontend colors good=green, bad=red, anything else=yellow. Emitting
# "bad" is what makes red render. Change to "poor" once it handles that word.
VERDICT_POOR_LABEL = "bad"

# Elevation below which even a clear sky is only a marginal image. The
# operational floor is MIN_ELEV_DEG; this is the "good geometry" threshold.
GOOD_ELEV_DEG = 25.0

# Optical sensors need reflected sunlight. Below DAYLIGHT_NONE_DEG (civil
# twilight) a pass yields no image at all; above DAYLIGHT_FULL_DEG lighting is
# not the limiting factor. In between the score ramps linearly.
DAYLIGHT_NONE_DEG = -6.0
DAYLIGHT_FULL_DEG = 5.0

# Open-Meteo (free, no API key)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
LIVE_WEATHER_TIMEOUT_S = 6  # past this, /api/calculate falls back to climatology
LIVE_WEATHER_TTL_S = 30 * 60  # reuse a point's forecast for 30 min
LIVE_WEATHER_ROUND = 2  # decimal places, ~1 km: nearby clicks share a forecast
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODE_TTL_S = 24 * 3600

# Cloud overlay: a CLOUD_GRID_N x CLOUD_GRID_N grid of forecast points centered
# on the target, fetched in one multi-location call (49 Open-Meteo "locations").
CLOUD_GRID_N = 7
CLOUD_GRID_SPAN_DEG = 6.0

TRAIN_START = "2023-09-01"
TRAIN_END = "2026-09-01"

# API
PASSES_MAX_AGE_S = 3600  # older than this, /api/passes recomputes in memory
API_HOST = "127.0.0.1"
API_PORT = 8000
CORS_ORIGINS = ["*"]  # hackathon: allow the partner's dev server from anywhere
