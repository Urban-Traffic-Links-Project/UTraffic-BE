import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel
from sqlalchemy import Column, ForeignKey


class TrafficMonitoredSegment(SQLModel, table=True):
    __tablename__ = "traffic_monitored_segments"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    node_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    )

    road_name: str | None = Field(default=None, max_length=255)
    lat: float
    lon: float
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TrafficSnapshot(SQLModel, table=True):
    __tablename__ = "traffic_snapshots"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    monitored_segment_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("traffic_monitored_segments.id", ondelete="CASCADE"), index=True)
    )

    current_speed: float | None = None
    free_flow_speed: float | None = None
    current_travel_time: float | None = None
    free_flow_travel_time: float | None = None
    delay_seconds: float | None = None
    congestion_percent: float | None = None
    confidence: float | None = None
    road_closure: bool = Field(default=False)
    status: str = Field(default="unknown", max_length=50)
    frc: str | None = Field(default=None, max_length=50)

    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)