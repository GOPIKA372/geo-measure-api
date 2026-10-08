"""Orchestrates: parse file -> normalise to lon/lat -> measure each feature."""
from dataclasses import dataclass
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape

from .crs import WGS84, reproject
from .measurements import Measurement, measure
from .readers import FileProcessingError, RawFeature, read_kml, read_shapefile_zip


@dataclass
class ProcessedFeature:
    index: int
    geometry_type: str | None
    geometry: dict | None          # GeoJSON in the file's ORIGINAL CRS
    properties: dict
    measurement: Measurement


@dataclass
class ProcessedFile:
    crs: str
    features: list[ProcessedFeature]
    warnings: list[str]


def process(filename: str, data: bytes) -> ProcessedFile:
    ext = Path(filename).suffix.lower()
    if ext == ".zip":
        raw, crs_str, warnings = read_shapefile_zip(data)
    elif ext == ".kml":
        raw, crs_str, warnings = read_kml(data)
    else:
        raise FileProcessingError("Unsupported file type; upload a .zip (Shapefile) or .kml")

    try:
        to_lonlat = Transformer.from_crs(CRS.from_user_input(crs_str), WGS84, always_xy=True)
        is_lonlat = CRS.from_user_input(crs_str).equals(WGS84)
    except Exception as exc:  # noqa: BLE001
        raise FileProcessingError(f"Unsupported or invalid CRS: {exc}") from exc

    out = [_process_feature(r, to_lonlat, is_lonlat) for r in raw]
    return ProcessedFile(crs_str, out, warnings)


def _process_feature(r: RawFeature, to_lonlat, is_lonlat: bool) -> ProcessedFeature:
    if r.geojson is None:
        return ProcessedFeature(r.index, None, None, r.properties,
                                Measurement("ERROR", note=r.error))
    try:
        geom = shape(r.geojson)
        lonlat = geom if is_lonlat else reproject(geom, to_lonlat)
        return ProcessedFeature(r.index, geom.geom_type, mapping(geom), r.properties, measure(lonlat))
    except Exception as exc:  # noqa: BLE001
        return ProcessedFeature(r.index, r.geojson.get("type"), r.geojson, r.properties,
                                Measurement("ERROR", note=f"Processing failed: {exc}"))
