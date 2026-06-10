"""
src/modules/route_monitor/schemas.py
Pydantic response schemas cho Route Monitor API.
"""

from datetime import datetime

from pydantic import BaseModel


class PointStatusResponse(BaseModel):
    point_id: str | None = None
    snapshot_id: str | None = None
    point_label: str
    lat: float
    lon: float
    captured_at: str | None = None
    current_speed: float | None = None
    free_flow_speed: float | None = None
    current_travel_time: float | None = None
    free_flow_travel_time: float | None = None
    delay_seconds: float | None = None
    congestion_percent: float | None = None
    confidence: float | None = None
    road_closure: bool = False
    status: str
    frc: str | None = None


class RouteStatusResponse(BaseModel):
    route_name: str
    captured_at: str | None = None
    points: list[PointStatusResponse]
    average_speed: float | None = None
    average_congestion_percent: float | None = None
    overall_status: str
    has_data: bool


class RouteSnapshotAtResponse(BaseModel):
    route_name: str
    requested_at: str
    actual_captured_at: str | None = None
    points: list[PointStatusResponse]
    average_speed: float | None = None
    average_congestion_percent: float | None = None
    overall_status: str
    has_data: bool


class CollectResponse(BaseModel):
    message: str
    created: int


class SeedResponse(BaseModel):
    message: str
    created: int
