import io
import zipfile

import pytest
import shapefile
from fastapi.testclient import TestClient
from pyproj import CRS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>Plot A</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
77.0,12.0,0 77.01,12.0,0 77.01,12.01,0 77.0,12.01,0 77.0,12.0,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>Road</name><LineString><coordinates>77.0,12.0 77.0,12.01</coordinates></LineString></Placemark>
<Placemark><name>Pt</name><Point><coordinates>77.0,12.0</coordinates></Point></Placemark>
<Placemark><name>Broken</name></Placemark>
</Document></kml>"""


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_shapefile_zip(kind="polygon", epsg: int | None = 4326, coords=None) -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf)
    w.field("NAME", "C")
    if kind == "polygon":
        w.poly([coords or [[77.0, 12.0], [77.01, 12.0], [77.01, 12.01], [77.0, 12.01], [77.0, 12.0]]])
    elif kind == "line":
        w.line([coords or [[77.0, 12.0], [77.0, 12.01]]])
    w.record("feature-1")
    w.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("sites.shp", shp.getvalue())
        z.writestr("sites.shx", shx.getvalue())
        z.writestr("sites.dbf", dbf.getvalue())
        if epsg:
            z.writestr("sites.prj", CRS.from_epsg(epsg).to_wkt())
    return buf.getvalue()
