from datetime import datetime
from sqlmodel import SQLModel


class DashboardOverviewResponse(SQLModel):
    average_speed: float | None
    average_delay_seconds: float | None
    congested_segments: int
    moderate_segments: int
    stable_segments: int
    road_closures: int
    monitored_segments: int
    last_updated_at: datetime | None


class TopCongestedSegmentResponse(SQLModel):
    segment_id: str
    road_name: str | None
    lat: float
    lon: float
    current_speed: float | None
    free_flow_speed: float | None
    delay_seconds: float | None
    congestion_percent: float | None
    status: str
    captured_at: datetime


class TrafficHistoryPointResponse(SQLModel):
    bucket: datetime
    average_speed: float | None
    average_delay_seconds: float | None
    congested_segments: int


class TrafficIncidentResponse(SQLModel):
    incident_type: str | None = None
    road_name: str | None = None
    description: str | None = None
    delay_seconds: float | None = None
    length_m: float | None = None
    magnitude: int | None = None