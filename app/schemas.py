from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    feature_count: int
    crs: Optional[str]
    status: str
    error: Optional[str] = None
    warnings: list[str] = []


class MeasurementOut(BaseModel):
    status: str
    area_m2: Optional[float] = None
    perimeter_m: Optional[float] = None
    length_m: Optional[float] = None
    projected_crs: Optional[str] = None
    note: Optional[str] = None


class FeatureOut(BaseModel):
    index: int
    geometry_type: Optional[str]
    crs: Optional[str]
    geometry: Optional[dict[str, Any]]
    properties: dict[str, Any]
    measurement: MeasurementOut


class MeasurementsResponse(BaseModel):
    file_id: str
    crs: Optional[str]
    page: int
    page_size: int
    total_features: int
    features: list[FeatureOut]
