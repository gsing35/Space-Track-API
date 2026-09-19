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
| `GET /api/passes` | Precomputed Blacksburg file, for instant page load | ~5 ms |
| `GET /api/calculate?lat=&lon=&name=` | Live computation for any point on the globe | ~2 s new point, ~0.2 s repeat |
| `GET /api/health` | Liveness, file age, pass count | |

A pass counts only if it is **above 20°**, the satellite's **sensor swath covers the point**,
and it happens in **daylight** — optical sensors need reflected sunlight. Each pass then gets
three comparable probabilities of a usable image (observed cloud cover below 30%):

| Score | Meaning |
| --- | --- |
| `forecast_only` | Naive: `1 - forecast cloud %` |
| `climatology` | This location's historical clear-sky rate for the month |
| `model` | Trained model, zeroed in darkness |

Re-run `generate` right before a demo: `lead_time_hours` is measured from generation time.

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

Cloud forecasts and the historical archive used for training come from
[Open-Meteo](https://open-meteo.com), free for non-commercial use under CC BY 4.0.

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