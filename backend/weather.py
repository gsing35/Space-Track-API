"""Cloud-cover forecast for the ground target, from Open-Meteo.

Free, no API key. Fetched once and cached to disk so the generate step can be
re-run with the network down.
"""

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config


def _get_json(url, params, timeout=20):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(full, timeout=timeout) as resp:
        return json.load(resp)


def _request_forecast(lat, lon, timeout):
    """One call to Open-Meteo for hourly cloud cover. No disk writes."""
    data = _get_json(
        config.FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "cloud_cover",
            # 4 days covers the 72h horizon with margin on both ends.
            "forecast_days": 4,
            "timezone": "UTC",
        },
        timeout=timeout,
    )
    return {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": lat,
        "longitude": lon,
        "hourly": data["hourly"],
    }


# (rounded lat, rounded lon) -> (monotonic time fetched, forecast dict)
_LIVE_CACHE = {}


def live_forecast(lat, lon):
    """Forecast for an arbitrary point, for the live /api/calculate path.

    Never touches data/weather_cache.json -- that file belongs to the static
    Blacksburg run, and overwriting it from a click on another city would
    silently corrupt the next `generate`. Returns None if Open-Meteo is slow
    or down, so the caller can fall back to climatology instead of failing.
    """
    key = (round(lat, config.LIVE_WEATHER_ROUND), round(lon, config.LIVE_WEATHER_ROUND))
    hit = _LIVE_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < config.LIVE_WEATHER_TTL_S:
        return hit[1]
    try:
        forecast = _request_forecast(lat, lon, timeout=config.LIVE_WEATHER_TIMEOUT_S)
    except (OSError, ValueError, KeyError):
        return None
    _LIVE_CACHE[key] = (time.monotonic(), forecast)
    return forecast


# lowercase query -> (monotonic time fetched, results)
_GEOCODE_CACHE = {}


def geocode(query):
    """Place-name search via Open-Meteo geocoding. [] on no match or failure.

    Each result: {name, admin1, country, lat, lon}. admin1 is the state or
    region, so "Paris" can be told apart from Paris, Texas.
    """
    key = query.strip().lower()
    if not key:
        return []
    hit = _GEOCODE_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < config.GEOCODE_TTL_S:
        return hit[1]
    try:
        data = _get_json(
            config.GEOCODE_URL,
            {"name": query.strip(), "count": 5, "language": "en", "format": "json"},
            timeout=config.LIVE_WEATHER_TIMEOUT_S,
        )
    except (OSError, ValueError):
        return []
    results = [
        {
            "name": r["name"],
            "admin1": r.get("admin1"),
            "country": r.get("country"),
            "lat": r["latitude"],
            "lon": r["longitude"],
        }
        for r in data.get("results", [])
    ]
    _GEOCODE_CACHE[key] = (time.monotonic(), results)
    return results


# (rounded lat, rounded lon) -> (monotonic time fetched, grid dict)
_GRID_CACHE = {}


def cloud_grid(lat, lon):
    """Hourly forecast cloud cover on a grid around a point, for the overlay.

    One multi-location Open-Meteo call. Returns
    {lats, lons, step_deg, times, cover} where cover[t][i] is the cloud percent
    at time t for grid point i (lats[i], lons[i]), or None if Open-Meteo is
    slow or down. Never written to disk.
    """
    key = (round(lat, 1), round(lon, 1))
    hit = _GRID_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < config.LIVE_WEATHER_TTL_S:
        return hit[1]

    n = config.CLOUD_GRID_N
    step = config.CLOUD_GRID_SPAN_DEG / (n - 1)
    half = config.CLOUD_GRID_SPAN_DEG / 2
    lats, lons = [], []
    for r in range(n):
        for c in range(n):
            glat = max(-89.5, min(89.5, lat - half + r * step))
            glon = ((lon - half + c * step + 180) % 360) - 180
            lats.append(round(glat, 3))
            lons.append(round(glon, 3))
    try:
        data = _get_json(
            config.FORECAST_URL,
            {
                "latitude": ",".join(map(str, lats)),
                "longitude": ",".join(map(str, lons)),
                "hourly": "cloud_cover",
                "forecast_days": 4,
                "timezone": "UTC",
            },
            timeout=config.LIVE_WEATHER_TIMEOUT_S * 2,
        )
        if isinstance(data, dict):  # a single location comes back unwrapped
            data = [data]
        times = [f"{t}:00Z" for t in data[0]["hourly"]["time"]]
        per_point = [d["hourly"]["cloud_cover"] for d in data]
        cover = [
            [per_point[i][t] for i in range(len(per_point))] for t in range(len(times))
        ]
    except (OSError, ValueError, KeyError, IndexError):
        return None
    grid = {"lats": lats, "lons": lons, "step_deg": round(step, 4), "times": times, "cover": cover}
    _GRID_CACHE[key] = (time.monotonic(), grid)
    return grid


_CLIM_TABLE = None
_CLIM_CACHE = {}  # (lat 0.1deg, lon 0.1deg, month) -> clear rate


def _clim_table():
    global _CLIM_TABLE
    if _CLIM_TABLE is None:
        try:
            with open(config.CLIMATOLOGY_PATH) as fh:
                table = json.load(fh)
            _CLIM_TABLE = table if table.get("version") == 2 else {}
        except (OSError, ValueError):
            _CLIM_TABLE = {}
    return _CLIM_TABLE


def _band_rate(lat, month):
    bands = _clim_table().get("band_rates", {})
    a = abs(lat)
    for key, rates in bands.items():
        lo, hi = (float(x) for x in key.split("-"))
        if lo <= a < hi or (hi >= 90 and a >= lo):
            return rates.get(str(month))
    return None


def _recent_years_for(month, now):
    """The two most recent COMPLETE occurrences of a calendar month."""
    newest = now.year if month < now.month else now.year - 1
    return newest, newest - 1


def _fetch_month_clear(lat, lon, year, month):
    start = f"{year}-{month:02d}-01"
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = (datetime(end_year, end_month, 1) - timedelta(days=1)).strftime("%Y-%m-%d")
    data = _get_json(
        config.ARCHIVE_URL,
        {"latitude": lat, "longitude": lon, "hourly": "cloud_cover",
         "start_date": start, "end_date": end, "timezone": "UTC"},
        timeout=config.LIVE_WEATHER_TIMEOUT_S,
    )
    cover = [c for c in data["hourly"]["cloud_cover"] if c is not None]
    return sum(c < config.CLOUD_USABLE_PCT for c in cover), len(cover)


def point_climatology(lat, lon, month, now=None):
    """Historical clear-sky rate at this point for a calendar month.

    Returns (rate, source). Sources, in order of preference:
      "table"          a location from the training run, within ~25 km
      "archive"        live: this month in each of the last two years
      "latitude-band"  fallback average for the latitude band
      None             nothing available
    """
    for pt in _clim_table().get("points", []):
        if abs(pt["lat"] - lat) < 0.25 and abs(pt["lon"] - lon) < 0.25:
            rate = pt["monthly"].get(str(month))
            if rate is not None:
                return rate, "table"

    key = (round(lat, 1), round(lon, 1), month)
    if key in _CLIM_CACHE:
        return _CLIM_CACHE[key], "archive"

    now = now or datetime.now(timezone.utc)
    try:
        clear = total = 0
        for year in _recent_years_for(month, now):
            c, n = _fetch_month_clear(lat, lon, year, month)
            clear, total = clear + c, total + n
        if total:
            _CLIM_CACHE[key] = clear / total
            return _CLIM_CACHE[key], "archive"
    except (OSError, ValueError, KeyError):
        pass

    rate = _band_rate(lat, month)
    return (rate, "latitude-band") if rate is not None else (None, None)


def fetch_forecast(lat=None, lon=None, path=None):
    """One call to Open-Meteo for hourly cloud cover; caches to disk."""
    lat = config.TARGET_LAT if lat is None else lat
    lon = config.TARGET_LON if lon is None else lon
    path = path or config.WEATHER_CACHE_PATH

    cache = _request_forecast(lat, lon, timeout=20)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(cache, fh)
    return cache


def load_or_fetch(path=None, refresh=False):
    """Prefer the on-disk cache; fetch only if missing or explicitly refreshed."""
    path = path or config.WEATHER_CACHE_PATH
    if not refresh and path.exists():
        with open(path) as fh:
            return json.load(fh)
    return fetch_forecast(path=path)


class CloudLookup:
    """Nearest-hour cloud cover lookup over the cached forecast."""

    def __init__(self, cache):
        hourly = cache["hourly"]
        self._by_hour = {}
        for stamp, cover in zip(hourly["time"], hourly["cloud_cover"]):
            if cover is None:
                continue
            # Open-Meteo returns "2026-09-19T00:00" with timezone=UTC.
            dt = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
            self._by_hour[dt.replace(minute=0, second=0, microsecond=0)] = int(cover)
        self.fetched_at = cache.get("fetched_at")

    def __len__(self):
        return len(self._by_hour)

    def cloud_at(self, when):
        """Cloud cover percent at the nearest hour, or None if out of range."""
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        # Round to nearest hour rather than truncating.
        hour = when.replace(minute=0, second=0, microsecond=0)
        if when.minute >= 30:
            hour += timedelta(hours=1)
        return self._by_hour.get(hour)

    def coverage(self):
        """(earliest, latest) hour present in the cache."""
        if not self._by_hour:
            return None, None
        keys = sorted(self._by_hour)
        return keys[0], keys[-1]
