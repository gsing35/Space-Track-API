# Space-Track GP catalog cache

Fetches a full GP (general perturbations) catalog snapshot from
[Space-Track.org](https://www.space-track.org) and caches it to a local JSON file,
optionally exporting a classic 3-line TLE file.

## Setup

1. Create a free Space-Track account: https://www.space-track.org/auth/createAccount
2. Fill in credentials:

   ```bash
   cp .env.example .env
   # edit .env and set SPACETRACK_USER and SPACETRACK_PASS
   ```

3. Install the one dependency:

   ```bash
   pip install requests
   ```

## Usage

```bash
python3 spacetrack_gp.py                  # use cache if <8h old, else fetch
python3 spacetrack_gp.py --force          # always hit the API
python3 spacetrack_gp.py --max-age 6      # treat cache as stale after 6 hours
python3 spacetrack_gp.py --no-tle         # cache the JSON, skip the .tle file
```

Every run leaves you with two files:

| File | What it is |
| --- | --- |
| `gp_catalog.json` | The full cached snapshot — every GP field for every object |
| `sats.tle` | Standard 3LE text, ready for STK / GMAT / gpredict / `sgp4` |

### Pulling individual satellites back out

`--find` reads the local cache only — no login, no API call, nothing against your rate
limit. It takes a NORAD ID or a name substring (case-insensitive), comma-separated for
several at once:

```bash
python3 spacetrack_gp.py --find 25544             # the ISS, by NORAD ID
python3 spacetrack_gp.py --find starlink          # every Starlink, by name
python3 spacetrack_gp.py --find 25544,iridium     # union of both
python3 spacetrack_gp.py --find starlink --all    # don't cap at 50 results
```

Matching TLEs go to **stdout** and all notes to **stderr**, so you can redirect straight
into a file that a TLE parser will accept:

```bash
python3 spacetrack_gp.py --find 25544 > iss.tle
```

Output is capped at 50 matches by default (`--find starlink` hits thousands); use
`--limit N` or `--all`.

### The cache file

`gp_catalog.json` looks like this:

```json
{
  "fetched_at": "2026-09-18T12:00:00+00:00",
  "source": "space-track.org basicspacedata/query/class/gp",
  "query": "/class/gp/decay_date/null-val/epoch/%3Enow-30/orderby/norad_cat_id/format/json",
  "count": 27000,
  "records": [ { "NORAD_CAT_ID": "25544", "OBJECT_NAME": "ISS (ZARYA)", "TLE_LINE1": "1 ...", "TLE_LINE2": "2 ...", "...": "..." } ]
}
```

## How the API call works

1. `POST https://www.space-track.org/ajaxauth/login` with form fields `identity`
   and `password`. Success returns an empty body and a session cookie; failure
   returns HTTP 200 with `{"Login":"Failed"}`, so check the body, not the status.
2. `GET https://www.space-track.org/basicspacedata/query/class/gp/...` reusing that
   cookie. The query path is built from `/predicate/value` pairs; `>` must be
   URL-encoded as `%3E`.
3. `GET https://www.space-track.org/ajaxauth/logout` when done.

## Rate limits

Space-Track allows **fewer than 30 requests/minute and 300 requests/hour**. The
full catalog only updates a few times a day, so refresh it every several hours at
most — that is what the cache-age check is for. Abusive polling gets accounts
suspended.

## Useful query variations

Swap `GP_CATALOG_QUERY` in the script, or pass a different query to
`fetch_gp_catalog()`:

| Goal | Query path |
| --- | --- |
| One object | `/class/gp/NORAD_CAT_ID/25544/format/json` |
| Everything ever catalogued (incl. decayed) | `/class/gp/orderby/NORAD_CAT_ID/format/json` |
| Only recently updated elsets | `/class/gp/EPOCH/%3Enow-1/orderby/NORAD_CAT_ID/format/json` |
| Starlink by name | `/class/gp/OBJECT_NAME/~~STARLINK/format/json` |
| Raw 3LE instead of JSON | `...format/3le` (returns text, not JSON) |
| Latest-only, faster | use `class/gp` (already latest); `gp_history` for past elsets |

## Pass prediction backend

`backend/` answers one question for any point on Earth: **over the next 72 hours, which
Earth-observation satellite passes will actually produce a usable image of this spot?**

```bash
.venv/bin/python -m backend.train_model     # fit the model (offline after first run)
.venv/bin/python -m backend.generate        # static Blacksburg -> data/passes.json
.venv/bin/uvicorn backend.main:app --port 8000
```

| Endpoint | What it does | Speed |
| --- | --- | --- |
| `GET /api/passes` | Precomputed Blacksburg file, for instant page load; recomputed in memory once over an hour old | ~5 ms |
| `GET /api/calculate?lat=&lon=&name=` | Live computation for any point on the globe | ~2 s new point, ~0.2 s repeat |
| `GET /api/calculate?aoi=lat,lon;lat,lon;…` | Same, for an area (polygon) instead of a point | ~1–2 s |
| `…&sats=39084,49260` | Only these satellites (NORAD IDs) produce passes | |
| `GET /api/geocode?q=` | Place-name search, up to 5 matches | ~0.3 s |
| `GET /api/clouds?lat=&lon=` | Hourly forecast cloud cover on a 7×7 grid (1° cells) around a point, for the globe overlay | ~1.5 s, then cached 30 min |
| `GET /api/health` | Liveness, file age, pass count | |

A pass counts only if it is **above 20°**, the satellite's **sensor swath covers the point**,
and it happens in **daylight** — optical sensors need reflected sunlight. Each pass then gets
three comparable probabilities of a usable image (observed cloud cover below 30%):

| Score | Meaning |
| --- | --- |
| `forecast_only` | Naive: `1 - forecast cloud %` |
| `climatology` | This location's historical clear-sky rate for the month |
| `model` | Trained model, zeroed in darkness |

`lead_time_hours` is measured from generation time, so `/api/passes` quietly recomputes once
the file is over an hour old (in memory only; if that fails it serves the file).

### Optimizing for time-to-image

What an operator needs is **how soon they can get a usable image**, not a score for each pass.
Every target in the response carries an `acquisition` block that answers exactly that:

| Field | Meaning |
| --- | --- |
| `recommended_pass_id` | The **earliest** pass rated `good`; if none, the earliest pass within 5 points of the best score |
| `hours_to_recommended` | Wait until that pass peaks |
| `p_image_24h` / `p_image_48h` / `p_image_horizon` | Probability of at least one usable image by then |
| `median_hours_to_image` | When that probability first reaches 50% (`null` if never within 72 h) |
| `cumulative` | The curve behind those numbers, one point per weather window |

Each pass also gets `limited_by` (`night`, `clouds`, `light`, `geometry`, or `null` when
nothing is limiting it) and `imaging` (`false` for the ISS, which is excluded from the numbers above).

Passes close together see the same clouds, so they are not separate chances. Imaging passes
peaking within **3 hours** of each other form one weather window, which succeeds with its best
pass's probability. Windows are then treated as independent, so `p_image_*` is somewhat
optimistic when cloud patterns persist for days. `recommended_pass_id` and
`hours_to_recommended` do not depend on that assumption.

### Areas, satellite filter and orbit-data age

**Areas (AOI = area of interest).** Pass `aoi` as a polygon of 3–20 corners, at most 600 km
across. Beyond that, one cloud forecast at the centroid can't honestly speak for the whole
region. The backend spreads sample points inside the polygon and walks every satellite's
ground track: a pass is any stretch where the sensor swath touches at least one sample.
Each pass then reports `aoi_coverage_pct`, the share of the area it images. A pass imaging
**less than half** the area is at most `marginal` (`limited_by: "coverage"`) and doesn't
count toward time-to-image. A sliver of the area isn't the picture you asked for. Ground tracks
come straight from SGP4 with an Earth-rotation formula, not Skyfield's full conversion.
That keeps a 72 h area search at ~0.25 s, and positions within 0.7 km of Skyfield's.

**Satellite filter.** `sats` limits which satellites produce passes and the time-to-image
numbers. `satellites[]` in the response still lists all of them so the globe can draw them.

**Orbit-data age.** Every `satellites[]` entry carries `epoch_utc` and `tle_age_hours`, and
the document has `tle_age: {max_hours, min_hours, status}`. The status is `fresh` under
3 days, `stale` under 7 and `old` beyond that; SGP4 predictions drift by kilometers after
about a week. The age comes from the saved catalog; refresh it with `tools/spacetrack_gp.py`.

### How the model was trained and tested

One global model, not a grid of regional ones. The live Open-Meteo forecast is already
specific to the clicked point; what the model learns is how far to trust that forecast.

- **Data:** real cloud forecasts issued 0–3 days ahead (Open-Meteo Previous Runs API),
  paired with what was actually observed (ERA5 reanalysis), Sep 2025 – Aug 2026.
- **Training:** 520,396 daylight hours from 27 locations on every continent and ocean.
- **Testing:** 8 locations **never seen in training** — Blacksburg, Anchorage, Honolulu,
  Santiago, Johannesburg, Istanbul, Delhi, Sydney — chosen far from any training site.

Results on the unseen locations (128,880 daylight hours; Brier score, lower is better):

| Method | Brier | Log loss | Accuracy |
| --- | --- | --- | --- |
| **model** | **0.1245** | **0.399** | 82.7% |
| forecast_only (raw forecast) | 0.1480 | 0.637 | 79.4% |
| climatology | 0.2092 | 0.609 | 69.3% |
| "forecast < 30% = clear" rule | — | — | 82.8% |

What those numbers do and don't say:

- The model's probabilities are **16% better (Brier) than trusting the raw forecast**, and
  better at 7 of the 8 unseen locations. Istanbul is the exception.
- Its **yes/no accuracy is no better than the simple rule** "forecast under 30% means clear."
  The value is calibration. When the model says 50–60% it is clear 60% of the time, and
  60–70% means 70%; it is slightly cautious at the top (70–80% is actually clear 84% of
  the time). The raw forecast's 60–70% is clear only 36% of the time.
- **Brier Skill Score vs climatology:** model +0.405, raw forecast +0.292, persistence
  ("same as N days ago") −0.660.
- **Passes it calls good (score ≥ 0.70) are clear 90.5% of the time**, versus 79.7% for the
  raw forecast — half the false "good" calls, at the cost of flagging fewer passes.
- The shipped model scores exactly the same as the forecast calibrated per lead time
  (Brier Skill Score +0.405 for both): it is a calibration layer, not a better cloud predictor.
- **Adding location features did not help.** Local time, season, latitude and climatology
  were all tried; on unseen locations each made Brier equal or worse. With 27 training
  sites they learn site quirks that don't transfer, so the shipped model uses only the
  forecast and its lead time. Full table in `data/model_report.json`.
- "Observed" is ERA5 reanalysis — itself a model, not satellite cloud masks.

## Data source & attribution

Orbital data in this repository comes from [Space-Track.org](https://www.space-track.org),
provided by United States Space Command (USSPACECOM) and the 18th Space Defense Squadron.

Cloud forecasts, the historical archive used for training, and place-name search
(geocoding, which uses GeoNames data) come from [Open-Meteo](https://open-meteo.com), free
for non-commercial use under CC BY 4.0.

`gp_catalog.json` and `sats.tle` are committed here as a point-in-time snapshot.
Redistribution is permitted under USSPACECOM's standing grant:

> USSPACECOM has provided express blanket approval for transfer/redistribution of basic
> SSA data and services accessed via www.Space-Track.org conditioned on appropriate
> citation.

That approval covers Two-Line Elements, Orbital Mean-Element Messages, SATCAT, and
decay/reentry data. The citation above is the condition being met; keep it with the data
if you redistribute it further, and cite Space-Track in any published analysis derived
from it.

### You need your own account

The committed snapshot is free to use, but the API is not open. Space-Track's User
Agreement is explicit:

> The User agrees he or she will only enter this site utilizing his or her own username
> and password. The User agrees not to share, assign or transfer his or her username or
> password to another. Each individual user or entity is required to obtain a separate
> account.

Register at https://www.space-track.org/auth/createAccount and put your own credentials in
`.env`. Never commit `.env` — it is gitignored for that reason.

### The snapshot goes stale

TLEs decay in accuracy within days, and low-perigee objects drift fastest. `fetched_at` at
the top of `gp_catalog.json` records when the committed copy was pulled. For anything
operational, run `python3 spacetrack_gp.py --force` and use fresh elements rather than the
snapshot in this repo.