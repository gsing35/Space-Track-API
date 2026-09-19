"""Pull the handful of Earth-observation satellites we care about out of the
35 MB GP catalog.

This runs OFFLINE, as part of generate.py. The API process never touches the
big catalog file -- see load_targets().
"""

import json
import sys

from . import config


def extract_targets(catalog_path=None, names=None, out_path=None):
    """Read the full catalog once and write a small targets file.

    Matches on EXACT OBJECT_NAME. Substring matching is unsafe here: "TERRA"
    also matches SKYTERRA 1 and TERRA SAR X, and "AQUA" also matches
    SAC-D (AQUARIUS). Raises if any requested satellite is missing, rather
    than silently computing passes for a short list.
    """
    catalog_path = catalog_path or config.CATALOG_PATH
    names = names or config.SATELLITE_NAMES
    out_path = out_path or config.TARGETS_PATH

    with open(catalog_path) as fh:
        catalog = json.load(fh)
    records = catalog["records"] if isinstance(catalog, dict) else catalog

    wanted = set(names)
    found = {}
    for rec in records:
        name = rec.get("OBJECT_NAME")
        if name in wanted and name not in found:
            found[name] = rec

    missing = wanted - set(found)
    if missing:
        raise SystemExit(
            f"Catalog is missing {len(missing)} satellite(s): {sorted(missing)}\n"
            f"Check spelling against the catalog -- Sentinel names mix spaces "
            f"and hyphens."
        )

    targets = []
    for name in names:
        rec = found[name]
        l1, l2 = rec.get("TLE_LINE1"), rec.get("TLE_LINE2")
        if not l1 or not l2:
            raise SystemExit(f"{name} has no TLE lines in the catalog.")
        targets.append(
            {
                # Catalog stores this as a string; contract.json wants an int.
                "norad_id": int(rec["NORAD_CAT_ID"]),
                "name": name,
                "tle_line1": l1,
                "tle_line2": l2,
                "epoch": rec.get("EPOCH"),
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(targets, fh, indent=2)
    return targets


def load_targets(path=None):
    """Read the small targets file written by extract_targets()."""
    path = path or config.TARGETS_PATH
    with open(path) as fh:
        return json.load(fh)


if __name__ == "__main__":
    t = extract_targets()
    print(f"Extracted {len(t)} satellites to {config.TARGETS_PATH}")
    for rec in t:
        print(f"  {rec['norad_id']:>6}  {rec['name']}")
    sys.exit(0)
