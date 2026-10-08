"""File readers. Each returns (list[RawFeature], source_crs_string)."""
import io
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import shapefile  # pyshp
from pyproj import CRS
from shapely.geometry import shape

from app.config import MAX_UNZIPPED_BYTES
from .crs import looks_like_lonlat


class FileProcessingError(Exception):
    """Raised for user-correctable problems (bad archive, missing parts...)."""


@dataclass
class RawFeature:
    index: int
    geojson: dict | None
    properties: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------- Shapefile
def read_shapefile_zip(data: bytes) -> tuple[list[RawFeature], str, list[str]]:
    warnings: list[str] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise FileProcessingError("Not a valid ZIP archive") from exc

    if sum(i.file_size for i in zf.infolist()) > MAX_UNZIPPED_BYTES:
        raise FileProcessingError("Archive is too large when uncompressed")

    # Zip-slip safe: only read members into memory by basename, never extract paths.
    members = {Path(i.filename).name.lower(): i for i in zf.infolist() if not i.is_dir()}
    shp_names = [n for n in members if n.endswith(".shp")]
    if not shp_names:
        raise FileProcessingError("ZIP does not contain a .shp file")
    if len(shp_names) > 1:
        warnings.append(f"Multiple shapefiles found; using '{shp_names[0]}'")
    stem = shp_names[0][:-4]

    def part(ext: str):
        m = members.get(stem + ext)
        return io.BytesIO(zf.read(m)) if m else None

    shp, shx, dbf = part(".shp"), part(".shx"), part(".dbf")
    if shp is None or dbf is None:
        raise FileProcessingError("Shapefile requires at least .shp and .dbf files")

    try:
        reader = shapefile.Reader(shp=shp, shx=shx, dbf=dbf)
    except Exception as exc:  # noqa: BLE001
        raise FileProcessingError(f"Could not read shapefile: {exc}") from exc

    crs_str = None
    prj = members.get(stem + ".prj")
    if prj is not None:
        try:
            crs = CRS.from_wkt(zf.read(prj).decode("utf-8", errors="ignore"))
            epsg = crs.to_epsg()
            crs_str = f"EPSG:{epsg}" if epsg else crs.to_wkt()
        except Exception:  # noqa: BLE001
            warnings.append(".prj could not be parsed")

    fields = [f[0] for f in reader.fields[1:]]
    feats: list[RawFeature] = []
    for i, sr in enumerate(reader.iterShapeRecords()):
        props = {k: _jsonable(v) for k, v in zip(fields, list(sr.record))}
        try:
            gj = sr.shape.__geo_interface__ if sr.shape.shapeType != 0 else None
            feats.append(RawFeature(i, gj, props, None if gj else "Null shape"))
        except Exception as exc:  # noqa: BLE001
            feats.append(RawFeature(i, None, props, f"Unreadable geometry: {exc}"))

    if crs_str is None:
        bounds = reader.bbox if feats else None
        if bounds and looks_like_lonlat(*bounds[:4]):
            crs_str = "EPSG:4326"
            warnings.append("No .prj found; coordinates look like lon/lat so EPSG:4326 was assumed")
        else:
            raise FileProcessingError("No .prj file and coordinates are not lon/lat; cannot determine CRS")
    return feats, crs_str, warnings


def _jsonable(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


# --------------------------------------------------------------------- KML
_NS = "{http://www.opengis.net/kml/2.2}"


def _strip(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _coords(text: str | None) -> list[tuple[float, ...]]:
    out = []
    for tok in (text or "").split():
        parts = tok.split(",")
        if len(parts) < 2:
            raise ValueError(f"Bad coordinate '{tok}'")
        out.append(tuple(float(p) for p in parts[:2]))  # drop altitude
    return out


def _ring(el) -> list:
    c = next((e for e in el.iter() if _strip(e.tag) == "coordinates"), None)
    return [list(p) for p in _coords(c.text if c is not None else None)]


def _kml_geom(el) -> dict | None:
    t = _strip(el.tag)
    if t == "Point":
        pts = _ring(el)
        return {"type": "Point", "coordinates": pts[0]}
    if t == "LineString":
        return {"type": "LineString", "coordinates": _ring(el)}
    if t == "LinearRing":
        return {"type": "LineString", "coordinates": _ring(el)}
    if t == "Polygon":
        outer = next(e for e in el if _strip(e.tag) == "outerBoundaryIs")
        rings = [_ring(outer)]
        rings += [_ring(e) for e in el if _strip(e.tag) == "innerBoundaryIs"]
        return {"type": "Polygon", "coordinates": rings}
    if t == "MultiGeometry":
        subs = [g for g in (_kml_geom(c) for c in el if _strip(c.tag) in
                            {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}) if g]
        kinds = {s["type"] for s in subs}
        if len(kinds) == 1:
            k = kinds.pop()
            if k in ("Point", "LineString", "Polygon"):
                return {"type": "Multi" + k, "coordinates": [s["coordinates"] for s in subs]}
        return {"type": "GeometryCollection", "geometries": subs}
    return None


def read_kml(data: bytes) -> tuple[list[RawFeature], str, list[str]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FileProcessingError(f"Invalid KML/XML: {exc}") from exc
    if _strip(root.tag) != "kml":
        raise FileProcessingError("Root element is not <kml>")

    feats: list[RawFeature] = []
    for pm in (e for e in root.iter() if _strip(e.tag) == "Placemark"):
        props: dict[str, Any] = {}
        geom_el = None
        for child in pm:
            name = _strip(child.tag)
            if name in {"name", "description"}:
                props[name] = (child.text or "").strip()
            elif name == "ExtendedData":
                for d in child.iter():
                    if _strip(d.tag) == "Data":
                        v = next((x.text for x in d if _strip(x.tag) == "value"), None)
                        props[d.get("name", "")] = v
                    elif _strip(d.tag) == "SimpleData":
                        props[d.get("name", "")] = d.text
            elif name in {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}:
                geom_el = child
        idx = len(feats)
        if geom_el is None:
            feats.append(RawFeature(idx, None, props, "Placemark has no supported geometry"))
            continue
        try:
            gj = _kml_geom(geom_el)
            shape(gj)  # validate structure early
            feats.append(RawFeature(idx, gj, props))
        except Exception as exc:  # noqa: BLE001
            feats.append(RawFeature(idx, None, props, f"Unreadable geometry: {exc}"))
    return feats, "EPSG:4326", []  # KML is always WGS84 lon/lat by spec
