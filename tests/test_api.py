import io
import zipfile

import pytest
from pyproj import Geod, Transformer

from tests.conftest import KML, make_shapefile_zip

GEOD = Geod(ellps="WGS84")


def upload(client, name, data):
    return client.post("/api/files/", files={"upload": (name, data)})


def measurements(client, fid):
    return client.get(f"/api/files/{fid}/measurements/").json()


def test_kml_upload_and_measurements_match_geodesic(client):
    r = upload(client, "survey.kml", KML.encode())
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "COMPLETED" and body["feature_count"] == 4
    assert body["crs"] == "EPSG:4326"

    feats = measurements(client, body["id"])["features"]
    poly, line, pt, broken = feats

    ref_area, _ = GEOD.polygon_area_perimeter([77.0, 77.01, 77.01, 77.0], [12.0, 12.0, 12.01, 12.01])
    assert poly["measurement"]["area_m2"] == pytest.approx(abs(ref_area), rel=0.005)
    assert poly["measurement"]["projected_crs"] == "EPSG:32643"  # UTM 43N
    assert line["measurement"]["length_m"] == pytest.approx(GEOD.line_length([77.0, 77.0], [12.0, 12.01]), rel=0.005)
    assert pt["measurement"]["status"] == "NOT_REQUIRED"
    assert broken["measurement"]["status"] == "ERROR"  # bad feature doesn't fail the file


def test_shapefile_geographic(client):
    r = upload(client, "s.zip", make_shapefile_zip("polygon", 4326))
    assert r.status_code == 201
    f = measurements(client, r.json()["id"])["features"][0]
    assert f["geometry_type"] == "Polygon"
    assert f["properties"]["NAME"] == "feature-1"
    assert 1.1e6 < f["measurement"]["area_m2"] < 1.3e6  # ~1.19 km2


def test_shapefile_projected_crs_is_normalised(client):
    # Same polygon expressed in UTM 43N must give (almost) the same area.
    t = Transformer.from_crs(4326, 32643, always_xy=True)
    ring = [list(t.transform(x, y)) for x, y in
            [(77.0, 12.0), (77.01, 12.0), (77.01, 12.01), (77.0, 12.01), (77.0, 12.0)]]
    r = upload(client, "utm.zip", make_shapefile_zip("polygon", 32643, ring))
    assert r.json()["crs"] == "EPSG:32643"
    utm = measurements(client, r.json()["id"])["features"][0]["measurement"]["area_m2"]
    r2 = upload(client, "geo.zip", make_shapefile_zip("polygon", 4326))
    geo = measurements(client, r2.json()["id"])["features"][0]["measurement"]["area_m2"]
    assert utm == pytest.approx(geo, rel=1e-3)


def test_missing_prj_assumes_wgs84_with_warning(client):
    r = upload(client, "s.zip", make_shapefile_zip("line", None))
    assert r.status_code == 201
    assert any("EPSG:4326" in w for w in r.json()["warnings"])


def test_wide_feature_uses_equal_area_fallback(client):
    big = [[60, 10], [75, 10], [75, 25], [60, 25], [60, 10]]  # 15 deg wide
    r = upload(client, "big.zip", make_shapefile_zip("polygon", 4326, big))
    m = measurements(client, r.json()["id"])["features"][0]["measurement"]
    ref, _ = GEOD.polygon_area_perimeter([60, 75, 75, 60], [10, 10, 25, 25])
    assert "laea" in m["projected_crs"].lower()
    assert m["area_m2"] == pytest.approx(abs(ref), rel=0.01)


def test_file_info_and_404(client):
    fid = upload(client, "a.kml", KML.encode()).json()["id"]
    info = client.get(f"/api/files/{fid}/").json()
    assert info["filename"] == "a.kml" and info["feature_count"] == 4
    assert client.get("/api/files/nope/").status_code == 404
    assert client.get("/api/files/nope/measurements/").status_code == 404


def test_pagination_and_filter(client):
    fid = upload(client, "a.kml", KML.encode()).json()["id"]
    r = client.get(f"/api/files/{fid}/measurements/?page_size=2&page=2").json()
    assert r["total_features"] == 4 and len(r["features"]) == 2
    r = client.get(f"/api/files/{fid}/measurements/?geometry_type=Polygon").json()
    assert r["total_features"] == 1


@pytest.mark.parametrize("name,data,code", [
    ("a.txt", b"hello", 415),
    ("a.kml", b"", 400),
    ("a.kml", b"<not-xml", 422),
    ("a.kml", b"<root/>", 422),
    ("a.zip", b"not a zip", 422),
])
def test_bad_uploads(client, name, data, code):
    assert upload(client, name, data).status_code == code


def test_zip_without_shp(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "x")
    assert upload(client, "a.zip", buf.getvalue()).status_code == 422


def test_unsupported_geometry_handled_gracefully(client):
    kml = """<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><MultiGeometry>
    <Point><coordinates>77,12</coordinates></Point>
    <LineString><coordinates>77,12 77,12.01</coordinates></LineString></MultiGeometry></Placemark></kml>"""
    r = upload(client, "m.kml", kml.encode())
    f = measurements(client, r.json()["id"])["features"][0]
    assert f["geometry_type"] == "GeometryCollection"
    assert f["measurement"]["status"] == "UNSUPPORTED"
