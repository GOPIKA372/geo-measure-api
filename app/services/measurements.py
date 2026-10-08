"""Measurement calculation for shapely geometries (input must be lon/lat)."""
from dataclasses import dataclass
from typing import Optional

from pyproj import Transformer
from shapely.geometry.base import BaseGeometry

from .crs import WGS84, reproject, select_projected_crs

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
NO_MEASURE_TYPES = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    status: str  # MEASURED | NOT_REQUIRED | UNSUPPORTED | ERROR
    area_m2: Optional[float] = None
    length_m: Optional[float] = None
    perimeter_m: Optional[float] = None
    projected_crs: Optional[str] = None
    note: Optional[str] = None


def measure(geom: BaseGeometry) -> Measurement:
    """Measure a lon/lat geometry. Never raises; problems are reported in status."""
    gtype = geom.geom_type
    if geom.is_empty:
        return Measurement("ERROR", note="Empty geometry")
    if gtype in NO_MEASURE_TYPES:
        return Measurement("NOT_REQUIRED", note="No measurement defined for points")
    if gtype not in AREA_TYPES | LENGTH_TYPES:
        return Measurement("UNSUPPORTED", note=f"Measurement not supported for {gtype}")

    try:
        target = select_projected_crs(*geom.bounds)
        tr = Transformer.from_crs(WGS84, target, always_xy=True)
        projected = reproject(geom, tr)
        note = None if geom.is_valid else "Geometry is invalid (e.g. self-intersection); result may be inaccurate"
        if gtype in AREA_TYPES:
            return Measurement("MEASURED", area_m2=projected.area, perimeter_m=projected.length,
                               projected_crs=target.to_string(), note=note)
        return Measurement("MEASURED", length_m=projected.length,
                           projected_crs=target.to_string(), note=note)
    except Exception as exc:  # noqa: BLE001 - one bad feature must not fail the file
        return Measurement("ERROR", note=f"Measurement failed: {exc}")
