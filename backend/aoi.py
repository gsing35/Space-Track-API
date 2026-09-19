"""Area targets (AOI = area of interest): a polygon instead of a point.

Coverage of an area is measured on sample points spread inside the polygon:
a pass images the share of those points that fall inside its swath. The
weather for the whole area comes from its centroid, which is why areas are
capped at AOI_MAX_SPAN_KM.

Polygons are small, so plain lat/lon geometry is used (no projection). Areas
that cross the antimeridian (180 deg longitude) are rejected rather than
handled half-right.
"""

from . import config
from .passes import surface_distance_km

GRID = 6  # sample grid is GRID x GRID across the bounding box, clipped to the polygon


def parse(text):
    """'lat,lon;lat,lon;...' -> [(lat, lon), ...]. Raises ValueError with a readable reason."""
    try:
        poly = [
            (float(a), float(b))
            for a, b in (pair.split(",") for pair in text.strip().strip(";").split(";"))
        ]
    except ValueError:
        raise ValueError("aoi must look like 'lat,lon;lat,lon;lat,lon'") from None
    if len(poly) > 1 and poly[0] == poly[-1]:
        poly = poly[:-1]  # accept an explicitly closed ring
    if len(poly) < 3:
        raise ValueError("an area needs at least 3 corners")
    if len(poly) > config.AOI_MAX_VERTICES:
        raise ValueError(f"an area can have at most {config.AOI_MAX_VERTICES} corners")
    for lat, lon in poly:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"corner ({lat}, {lon}) is out of range")
    lons = [lon for _, lon in poly]
    if max(lons) - min(lons) > 180:
        raise ValueError("areas crossing the 180 degree meridian are not supported")
    span = max(
        surface_distance_km(a[0], a[1], b[0], b[1]) for a in poly for b in poly
    )
    if span > config.AOI_MAX_SPAN_KM:
        raise ValueError(
            f"area is {span:.0f} km across; the limit is {config.AOI_MAX_SPAN_KM} km"
            f" because one cloud forecast cannot speak for a larger region"
        )
    return poly


def centroid(poly):
    """Vertex average. Good enough for the small, roughly convex areas people draw."""
    return (
        sum(p[0] for p in poly) / len(poly),
        sum(p[1] for p in poly) / len(poly),
    )


def _inside(lat, lon, poly):
    """Ray-casting point-in-polygon test."""
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def sample_points(poly):
    """Points that stand for the area: an interior grid, the corners, the centroid."""
    lats = [p[0] for p in poly]
    lons = [p[1] for p in poly]
    lat0, lat1, lon0, lon1 = min(lats), max(lats), min(lons), max(lons)
    pts = []
    for r in range(GRID):
        for c in range(GRID):
            lat = lat0 + (r + 0.5) * (lat1 - lat0) / GRID
            lon = lon0 + (c + 0.5) * (lon1 - lon0) / GRID
            if _inside(lat, lon, poly):
                pts.append((lat, lon))
    pts.extend(poly)
    pts.append(centroid(poly))
    return pts
