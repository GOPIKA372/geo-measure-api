"""CRS selection.

Strategy: every geometry is first brought to EPSG:4326, then projected into a
*local* metric CRS chosen from the feature's own location:

* Feature fits inside one UTM zone (lon span <= 6 deg) -> UTM (EPSG:326xx/327xx)
* Near the poles (|lat| > 80)                          -> UPS (EPSG:3413 / 3031)
* Wider features                                       -> Lambert Azimuthal
  Equal Area centred on the feature (area-preserving, small distortion locally)
"""
from pyproj import CRS

WGS84 = CRS.from_epsg(4326)


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def select_projected_crs(minx: float, miny: float, maxx: float, maxy: float) -> CRS:
    """Pick a metric CRS for a bounding box given in lon/lat degrees."""
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    if abs(cy) > 80:
        return CRS.from_epsg(3413 if cy > 0 else 3031)
    if (maxx - minx) <= 6.0:
        return CRS.from_epsg(utm_epsg(cx, cy))
    return CRS.from_proj4(f"+proj=laea +lat_0={cy} +lon_0={cx} +datum=WGS84 +units=m +no_defs")


def looks_like_lonlat(minx: float, miny: float, maxx: float, maxy: float) -> bool:
    return -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90


def reproject(geom, transformer):
    """Reproject a shapely geometry with a pyproj Transformer (shapely 2.x, vectorised)."""
    import numpy as np
    import shapely

    def _fn(coords):
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, _fn)
