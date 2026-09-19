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
    "LANDSAT 8": {"swath_km": 185, "color_hex": "#f4c430"},
    "LANDSAT 9": {"swath_km": 185, "color_hex": "#ffa000"},
    "SENTINEL 2A": {"swath_km": 290, "color_hex": "#00ff00"},
    "SENTINEL 2B": {"swath_km": 290, "color_hex": "#00c853"},
    "SENTINEL-2C": {"swath_km": 290, "color_hex": "#69f0ae"},
    "TERRA": {"swath_km": 2330, "color_hex": "#00e5ff"},
    "AQUA": {"swath_km": 2330, "color_hex": "#2979ff"},
    "ISS (ZARYA)": {"swath_km": 1200, "color_hex": "#ff3333"},
}

# Drop passes whose sensor swath never covers the target. A satellite can sit
# 20 degrees above the horizon while its nadir camera images 800 km away.
FOOTPRINT_FILTER = True

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
TRAIN_START = "2023-09-01"
TRAIN_END = "2026-09-01"

# API
API_HOST = "127.0.0.1"
API_PORT = 8000
CORS_ORIGINS = ["*"]  # hackathon: allow the partner's dev server from anywhere
