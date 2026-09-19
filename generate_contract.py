#!/usr/bin/env python3
import json
import requests
from datetime import datetime, timezone, timedelta
from skyfield.api import load, EarthSatellite, wgs84

TARGET_NAME = "Blacksburg, VA"
TARGET_LAT = 37.2296
TARGET_LON = -80.4139
HORIZON_HOURS = 48

# Define satellite metadata
TARGET_SATS = {
    "LANDSAT 9": {"norad": "49260", "swath": 185, "color": "#f4c430"},
    "Sentinel-2A": {"norad": "40697", "swath": 290, "color": "#00ff00"},
    "ISS (Wide FOV)": {"norad": "25544", "swath": 1200, "color": "#ff3333"}
} 

with open("gp_catalog.json", "r") as f:
    catalog = json.load(f)["records"]

print("Fetching weather data...")
weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={TARGET_LAT}&longitude={TARGET_LON}&hourly=cloud_cover"
weather_res = requests.get(weather_url).json()
cloud_times = weather_res["hourly"]["time"]
cloud_covers = weather_res["hourly"]["cloud_cover"]

def get_cloud_cover_for_time(utc_dt):
    iso_time = utc_dt.strftime("%Y-%m-%dT%H:00")
    try:
        idx = cloud_times.index(iso_time)
        return cloud_covers[idx]
    except ValueError:
        return 50 

ts = load.timescale()
t0 = ts.now()
t1 = ts.utc(t0.utc_datetime() + timedelta(hours=HORIZON_HOURS))
blacksburg = wgs84.latlon(TARGET_LAT, TARGET_LON)

final_passes = []
active_satellites = []

for sat_name, meta in TARGET_SATS.items():
    sat_data = next((s for s in catalog if str(s.get("NORAD_CAT_ID")) == meta["norad"]), None)
    if not sat_data:
        continue
        
    # Save the exact TLE used for the frontend
    active_satellites.append({
        "id": sat_name,
        "swathKm": meta["swath"],
        "colorHex": meta["color"],
        "tle1": sat_data["TLE_LINE1"],
        "tle2": sat_data["TLE_LINE2"]
    })
        
    satellite = EarthSatellite(sat_data["TLE_LINE1"], sat_data["TLE_LINE2"], sat_name, ts)
    t, events = satellite.find_events(blacksburg, t0, t1, altitude_degrees=10.0)
    
    for i in range(0, len(events), 3):
        if i + 2 >= len(events): break 
        
        t_rise, t_peak, t_set = t[i], t[i+1], t[i+2]
        topocentric = (satellite - blacksburg).at(t_peak)
        alt, az, distance = topocentric.altaz()
        
        cloud_pct = get_cloud_cover_for_time(t_rise.utc_datetime())
        model_score = round(((alt.degrees / 90.0) * 0.4) + (((100 - cloud_pct) / 100.0) * 0.6), 2)
        
        if cloud_pct < 30: verdict = "good"
        elif cloud_pct > 70: verdict = "bad"
        else: verdict = "marginal"
        
        final_passes.append({
            "pass_id": f"{sat_name.replace(' ', '')}-{t_rise.utc_datetime().strftime('%Y%m%dT%H%M')}",
            "satellite": sat_name,
            "start_utc": t_rise.utc_datetime().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "peak_utc": t_peak.utc_datetime().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "end_utc": t_set.utc_datetime().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "max_elevation_deg": round(alt.degrees, 1),
            "cloud_forecast_pct": cloud_pct,
            "scores": { "model": model_score },
            "verdict": verdict
        })

output = {
    "generated_at": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    "horizon_hours": HORIZON_HOURS,
    "satellites": active_satellites, # Passes TLEs to JS
    "targets": [{
        "name": TARGET_NAME,
        "lat": TARGET_LAT,
        "lon": TARGET_LON,
        "passes": sorted(final_passes, key=lambda p: p["start_utc"])
    }]
}

with open("contract.json", "w") as f:
    json.dump(output, f, indent=2)

print("Contract generated.")