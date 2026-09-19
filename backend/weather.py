"""Cloud-cover forecast for the ground target, from Open-Meteo.

Free, no API key. Fetched once and cached to disk so the generate step can be
re-run with the network down.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config


def _get_json(url, params, timeout=20):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(full, timeout=timeout) as resp:
        return json.load(resp)


def fetch_forecast(lat=None, lon=None, path=None):
    """One call to Open-Meteo for hourly cloud cover; caches to disk."""
    lat = config.TARGET_LAT if lat is None else lat
    lon = config.TARGET_LON if lon is None else lon
    path = path or config.WEATHER_CACHE_PATH

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
    )
    cache = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": lat,
        "longitude": lon,
        "hourly": data["hourly"],
    }
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
