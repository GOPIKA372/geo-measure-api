# Geospatial File Measurement API

A FastAPI service that accepts a **Shapefile (`.zip`)** or **KML (`.kml`)**, extracts every feature
(ID, geometry type, geometry, CRS, attributes) and returns **area** (polygons) and **length** (lines),
calculated in a *projected* metric CRS, never in raw degrees.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open <http://localhost:8000/docs> for interactive Swagger UI.

Run the tests: `pytest`

Docker: `docker build -t geo-api . && docker run -p 8000:8000 geo-api`

Config (env vars): `DATABASE_URL` (default `sqlite:///./geo.db`; any SQLAlchemy URL such as PostgreSQL works),
`MAX_UPLOAD_BYTES` (default 50 MB), `MAX_UNZIPPED_BYTES` (default 200 MB).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/files/` | Upload and process a `.zip` (Shapefile) or `.kml` (multipart field `upload`) |
| GET | `/api/files/{id}/` | File information |
| GET | `/api/files/{id}/measurements/` | Per-feature measurements (`?page=1&page_size=100&geometry_type=Polygon`) |
| GET | `/health` | Liveness check |

### Upload
```bash
curl -F "upload=@survey.kml" http://localhost:8000/api/files/
```
`201 Created`
```json
{"id": "29e9fd585f47404497337d3a4ae6b1f4", "filename": "survey.kml", "feature_count": 4,
 "crs": "EPSG:4326", "status": "COMPLETED", "error": null, "warnings": []}
```
Errors: `415` unsupported extension, `413` too large, `400` empty file, `422` unreadable / invalid content
(corrupt zip, no `.shp`, malformed KML, undeterminable CRS).

### Measurements
`GET /api/files/{id}/measurements/`
```json
{
  "file_id": "29e9...", "crs": "EPSG:4326", "page": 1, "page_size": 100, "total_features": 4,
  "features": [
    {"index": 0, "geometry_type": "Polygon", "crs": "EPSG:4326",
     "geometry": {"type": "Polygon", "coordinates": [[[77.0, 12.0], [77.01, 12.0], [77.01, 12.01], [77.0, 12.01], [77.0, 12.0]]]},
     "properties": {"name": "Plot A"},
     "measurement": {"status": "MEASURED", "area_m2": 1205141.19, "perimeter_m": 4391.29,
                     "length_m": null, "projected_crs": "EPSG:32643", "note": null}},
    {"index": 1, "geometry_type": "LineString", "crs": "EPSG:4326", "geometry": {"...": "..."},
     "properties": {"name": "Road"},
     "measurement": {"status": "MEASURED", "length_m": 1106.43, "projected_crs": "EPSG:32643"}},
    {"index": 2, "geometry_type": "Point", "properties": {"name": "Pt"},
     "measurement": {"status": "NOT_REQUIRED", "note": "No measurement defined for points"}},
    {"index": 3, "geometry_type": null, "properties": {"name": "Broken"},
     "measurement": {"status": "ERROR", "note": "Placemark has no supported geometry"}}
  ]
}
```
Measurement `status` values: `MEASURED`, `NOT_REQUIRED` (points), `UNSUPPORTED` (e.g. GeometryCollection),
`ERROR` (empty/unreadable geometry). A bad feature never fails the whole file.
Geometry is returned in the file's **original CRS**.

## Architecture

```
app/
  main.py            FastAPI app, table creation on startup
  config.py          env-driven settings
  database.py        SQLAlchemy engine/session
  models.py          UploadedFile, Feature
  schemas.py         Pydantic response models
  api/files.py       HTTP layer only (validation, status codes, persistence)
  services/
    readers.py       Shapefile (pyshp) + KML (stdlib XML) -> RawFeature
    crs.py           projected-CRS selection + reprojection helper
    measurements.py  area / length calculation
    processor.py     orchestrates reading -> normalising -> measuring
tests/               pytest suite (accuracy checked against pyproj.Geod)
```

**File-processing flow:** validate extension/size -> read bytes -> reader parses features (ZIP members are
read in memory by basename, so there is no extraction to disk and no zip-slip) -> each feature is
normalised and measured independently -> file + features saved in one transaction.

**Measurement flow:** feature geometry -> transform to EPSG:4326 (if needed) -> pick a local metric CRS ->
transform -> shapely `area` / `length` (polygon holes are subtracted; Multi* geometries are summed).

**CRS handling**
- Shapefile CRS comes from `.prj`; KML is always WGS84 by spec. No `.prj`: assume EPSG:4326 only if the
  coordinates fit lon/lat bounds (with a warning), otherwise reject with 422.
- Local CRS per feature: **UTM** if it fits in one 6° zone; **UPS** polar stereographic above 80° latitude;
  otherwise a **Lambert Azimuthal Equal Area** centred on the feature.
- Projected inputs (e.g. EPSG:32643, 3857) are first converted to lon/lat and then to the local CRS, so
  distorting CRSs like Web Mercator never leak into results.

## Design Decisions

- **FastAPI over Django:** the task is a small API; no admin/ORM-heavy needs, and I get validation and docs for free.
- **pyshp + stdlib XML instead of GeoPandas/Fiona/GDAL:** GDAL's KML driver is not available in every
  build, and GDAL makes installs and Docker images heavy. Trade-off: I support fewer exotic formats.
- **UTM-per-feature over one CRS per file:** files may span zones; per-feature picking keeps error low (<~0.1%
  in tests vs. geodesic). Alternative considered: geodesic maths (`pyproj.Geod`), which is exact, but the task
  asks for a projected CRS and projected maths is easier to reason about and test.
- **Synchronous processing:** simple and deterministic for the file sizes expected. `status` is already in the
  schema so moving to background jobs does not change the API.
- **Features stored in SQL (JSON columns)** so measurements can be paged and filtered without reparsing.
  PostGIS would allow spatial queries but is unnecessary here.
- **Partial failure tolerance:** per-feature errors are recorded, while file-level problems return 4xx.

## Learning
-Learned why geographic coordinates in degrees should not be directly used for area and length calculations, and how projected CRS such as UTM can be used for measurements.
-Learned how Shapefiles work as a group of files such as .shp, .shx, .dbf, and .prj, and how KML stores geographic features and coordinates.
-Learned how to validate measurement results by comparing the calculated values with an independent reference.
-Learned how to test a GIS API using both KML and Shapefile ZIP files.
-Improved my understanding of FastAPI, Git, GitHub, and automated testing while developing the project.
## Future Scope
-Add background processing and progress tracking for very large GIS files.
-Use PostgreSQL/PostGIS for storing and querying spatial data.
-Add authentication and user-specific file management.
-Support additional formats such as GeoJSON, KMZ, and GeoPackage.
-Add support for more geometry types and 3D measurements.
-Provide export options such as CSV and GeoJSON.
-Add rate limiting, structured logging, and GitHub Actions for automated testing and code quality checks.