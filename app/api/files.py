from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES
from app.database import get_db
from app.services.processor import process
from app.services.readers import FileProcessingError

router = APIRouter(prefix="/api/files", tags=["files"])


def _get_file_or_404(db: Session, file_id: str) -> models.UploadedFile:
    f = db.get(models.UploadedFile, file_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return f


@router.post("/", response_model=schemas.FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(upload: UploadFile = File(...), db: Session = Depends(get_db)):
    name = upload.filename or ""
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            "Only .zip (Shapefile) and .kml files are supported")

    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    record = models.UploadedFile(filename=name, status="PROCESSING")
    db.add(record)
    try:
        result = process(name, data)
    except FileProcessingError as exc:
        # Bad input is the client's fault: reject, and keep nothing half-processed.
        db.rollback()
        raise HTTPException(422, str(exc)) from exc

    record.crs, record.warnings = result.crs, result.warnings
    record.feature_count, record.status = len(result.features), "COMPLETED"
    for pf in result.features:
        m = pf.measurement
        record.features.append(models.Feature(
            index=pf.index, geometry_type=pf.geometry_type, geometry=pf.geometry,
            crs=result.crs, properties=pf.properties, measurement_status=m.status,
            area_m2=m.area_m2, perimeter_m=m.perimeter_m, length_m=m.length_m,
            projected_crs=m.projected_crs, note=m.note))
    db.commit()
    return record


@router.get("/{file_id}/", response_model=schemas.FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file_or_404(db, file_id)


@router.get("/{file_id}/measurements/", response_model=schemas.MeasurementsResponse)
def get_measurements(
    file_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    geometry_type: str | None = Query(None, description="Filter, e.g. Polygon"),
    db: Session = Depends(get_db),
):
    f = _get_file_or_404(db, file_id)
    q = db.query(models.Feature).filter(models.Feature.file_id == file_id)
    if geometry_type:
        q = q.filter(models.Feature.geometry_type == geometry_type)
    total = q.count()
    rows = q.order_by(models.Feature.index).offset((page - 1) * page_size).limit(page_size).all()
    feats = [schemas.FeatureOut(
        index=r.index, geometry_type=r.geometry_type, crs=r.crs, geometry=r.geometry,
        properties=r.properties,
        measurement=schemas.MeasurementOut(
            status=r.measurement_status, area_m2=r.area_m2, perimeter_m=r.perimeter_m,
            length_m=r.length_m, projected_crs=r.projected_crs, note=r.note)) for r in rows]
    return schemas.MeasurementsResponse(file_id=f.id, crs=f.crs, page=page, page_size=page_size,
                                        total_features=total, features=feats)
