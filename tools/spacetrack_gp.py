#!/usr/bin/env python3
"""Fetch a full GP (general perturbations) catalog snapshot from Space-Track
and cache it to a local JSON file.

Every run leaves you with two files: gp_catalog.json (the full cached snapshot)
and sats.tle (a standard 3-line element file you can feed to any TLE reader).

Usage:
    python3 spacetrack_gp.py                  # use cache if fresh, else fetch
    python3 spacetrack_gp.py --force          # always fetch
    python3 spacetrack_gp.py --max-age 6      # consider cache stale after 6 hours
    python3 spacetrack_gp.py --no-tle         # skip the .tle file, JSON only
    python3 spacetrack_gp.py --find 25544     # print one sat's TLE from the cache
    python3 spacetrack_gp.py --find starlink  # match on name instead (offline)

--find reads the cache only: no login, no API call, no rate-limit cost.

Credentials come from the environment (see .env.example):
    SPACETRACK_USER, SPACETRACK_PASS
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://www.space-track.org"
LOGIN_URL = f"{BASE}/ajaxauth/login"
LOGOUT_URL = f"{BASE}/ajaxauth/logout"
QUERY_BASE = f"{BASE}/basicspacedata/query"

# Full catalog of on-orbit objects: not decayed, epoch within the last 30 days.
# %3E is the URL encoding for '>'. Space-Track requires the encoded form.
GP_CATALOG_QUERY = (
    "/class/gp"
    "/decay_date/null-val"
    "/epoch/%3Enow-30"
    "/orderby/norad_cat_id/format/json"
)

CACHE_PATH = Path(__file__).parent / "gp_catalog.json"
TLE_PATH = Path(__file__).parent / "sats.tle"
DEFAULT_MAX_AGE_HOURS = 8.0
DEFAULT_FIND_LIMIT = 50

# Space-Track throttles at 30 requests/minute and 300 requests/hour.
# The full catalog only refreshes a few times a day, so don't poll it harder
# than every few hours or your account can be suspended.
REQUEST_TIMEOUT = 300  # seconds; the full catalog is a large response


def load_credentials():
    """Read Space-Track credentials from the environment (or a local .env)."""
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

    user = os.environ.get("SPACETRACK_USER", "")
    password = os.environ.get("SPACETRACK_PASS", "")

    if not user or not password:
        sys.exit(
            "Missing credentials. Set SPACETRACK_USER and SPACETRACK_PASS in the\n"
            "environment or in a .env file next to this script (see .env.example)."
        )
    return user, password


def fetch_gp_catalog(user, password, query=GP_CATALOG_QUERY):
    """Log in, run one GP query, log out. Returns the parsed JSON list."""
    with requests.Session() as session:
        session.headers["User-Agent"] = "spacetrack-gp-cache/1.0"

        resp = session.post(
            LOGIN_URL,
            data={"identity": user, "password": password},
            timeout=60,
        )
        resp.raise_for_status()
        # A failed login still returns HTTP 200, with a {"Login":"Failed"} body.
        # A successful login returns an empty body and sets the session cookie.
        if "Failed" in resp.text:
            raise RuntimeError(f"Space-Track login failed: {resp.text[:200]}")

        try:
            url = QUERY_BASE + query
            print(f"GET {url}", file=sys.stderr)
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        finally:
            session.get(LOGOUT_URL, timeout=30)

    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected response shape: {type(data).__name__}")
    return data


def write_cache(records, path=CACHE_PATH):
    """Write the snapshot plus a small metadata header."""
    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "space-track.org basicspacedata/query/class/gp",
        "query": GP_CATALOG_QUERY,
        "count": len(records),
        "records": records,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(snapshot, indent=None, separators=(",", ":")))
    tmp.replace(path)  # atomic: never leave a half-written cache behind
    return snapshot


def read_cache(path=CACHE_PATH):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Cache unreadable ({exc}); will refetch.", file=sys.stderr)
        return None


def cache_age_hours(snapshot):
    fetched = datetime.fromisoformat(snapshot["fetched_at"])
    return (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0


def tle_block(rec):
    """Return a record's three 3LE lines, or None if it carries no TLE."""
    l1, l2 = rec.get("TLE_LINE1"), rec.get("TLE_LINE2")
    if not l1 or not l2:
        return None
    name = (rec.get("OBJECT_NAME") or f"NORAD {rec.get('NORAD_CAT_ID')}").strip()
    return [f"0 {name}", l1, l2]


def write_tle_file(records, path):
    """Emit a classic 3-line (3LE) file from the cached GP records."""
    lines = []
    skipped = 0
    for rec in records:
        block = tle_block(rec)
        if block is None:
            skipped += 1
            continue
        lines.extend(block)
    Path(path).write_text("\n".join(lines) + "\n")
    return len(lines) // 3, skipped


def find_records(records, query):
    """Match records by NORAD ID or name substring.

    An all-digit term matches NORAD_CAT_ID exactly; anything else is a
    case-insensitive substring match on OBJECT_NAME. Comma-separated terms are
    unioned, de-duplicated by NORAD ID, in catalog order.
    """
    terms = [t.strip() for t in query.split(",") if t.strip()]
    if not terms:
        return []

    ids = {t for t in terms if t.isdigit()}
    names = [t.casefold() for t in terms if not t.isdigit()]

    matches = []
    seen = set()
    for rec in records:
        norad = str(rec.get("NORAD_CAT_ID", ""))
        if norad in seen:
            continue
        name = (rec.get("OBJECT_NAME") or "").casefold()
        if norad in ids or any(term in name for term in names):
            matches.append(rec)
            seen.add(norad)
    return matches


def run_find(snapshot, query, limit, show_all, cache_path):
    """Print matching TLEs from the cache. Returns a process exit code."""
    matches = find_records(snapshot["records"], query)
    if not matches:
        print(f"No matches for {query!r} in {cache_path}", file=sys.stderr)
        return 1

    shown = matches if show_all else matches[:limit]
    # All notes go to stderr so stdout stays pure 3LE and can be redirected
    # straight into a .tle file: `--find 25544 > iss.tle`.
    print(
        f"# {cache_path.name}, {cache_age_hours(snapshot):.1f}h old",
        file=sys.stderr,
    )

    no_tle = 0
    for rec in shown:
        block = tle_block(rec)
        if block is None:
            no_tle += 1
            continue
        print("\n".join(block))

    # Keep the notes below from racing ahead of the TLE lines in a terminal.
    sys.stdout.flush()

    if no_tle:
        print(f"# {no_tle} match(es) had no TLE lines", file=sys.stderr)
    if len(shown) < len(matches):
        print(
            f"# Showing {len(shown)} of {len(matches)} matches (--all for everything)",
            file=sys.stderr,
        )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--force", action="store_true", help="ignore cache freshness")
    parser.add_argument(
        "--max-age",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=f"hours before the cache is stale (default {DEFAULT_MAX_AGE_HOURS})",
    )
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument(
        "--tle",
        type=Path,
        default=TLE_PATH,
        help=f"3LE file to write on every run (default {TLE_PATH.name})",
    )
    parser.add_argument(
        "--no-tle", action="store_true", help="skip the .tle file, cache JSON only"
    )
    parser.add_argument(
        "--find",
        metavar="QUERY",
        help="print TLEs matching a NORAD ID or name substring, from the cache "
        "only (comma-separate for several); makes no API call",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_FIND_LIMIT,
        help=f"max --find results to print (default {DEFAULT_FIND_LIMIT})",
    )
    parser.add_argument("--all", action="store_true", help="print all --find results")
    args = parser.parse_args()

    snapshot = read_cache(args.cache)

    # Lookup mode is purely offline: no freshness check, no fetch, no rewrite.
    if args.find:
        if snapshot is None:
            print(
                f"No cache at {args.cache}; run `python3 {Path(__file__).name}` first.",
                file=sys.stderr,
            )
            return 1
        return run_find(snapshot, args.find, args.limit, args.all, args.cache)

    if snapshot and not args.force:
        age = cache_age_hours(snapshot)
        if age < args.max_age:
            print(f"Cache hit: {snapshot['count']} objects, {age:.1f}h old ({args.cache})")
        else:
            print(f"Cache is {age:.1f}h old (max {args.max_age}h); refreshing.")
            snapshot = None
    else:
        snapshot = None

    if snapshot is None:
        user, password = load_credentials()
        started = time.monotonic()
        records = fetch_gp_catalog(user, password)
        snapshot = write_cache(records, args.cache)
        print(
            f"Cached {len(snapshot['records'])} objects to {args.cache} "
            f"in {time.monotonic() - started:.1f}s"
        )

    if not args.no_tle:
        written, skipped = write_tle_file(snapshot["records"], args.tle)
        print(f"Wrote {written} TLEs to {args.tle} ({skipped} without TLE lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
