"""
src/storage/models/incident.py
Lưu incidents từ TomTom Incident Details v5 sau khi đã map-match sang OSM edges.

- incidents: 1 record/incident TomTom (đã chuẩn hóa + lưu geometry)
- incident_edges: bảng nối (incident ↔ edges) + score match
"""

import uuid
from datetime import datetime, timezone
from typing import Any, List

from geoalchemy2 import Geometry
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


class Incident(SQLModel, table=True):
    __tablename__ = "incidents"
    __table_args__ = (UniqueConstraint("tomtom_incident_id", name="uq_incidents_tomtom_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # TomTom properties.id
    tomtom_incident_id: str = Field(index=True, max_length=64)

    # TomTom properties fields (subset useful for UI/analytics)
    icon_category: int | None = Field(default=None, index=True)
    magnitude_of_delay: int | None = Field(default=None)
    delay_seconds: int | None = Field(default=None)
    time_validity: str | None = Field(default=None, max_length=16)  # present|future
    start_time: datetime | None = Field(default=None)
    end_time: datetime | None = Field(default=None)

    # Store events + other metadata (language dependent strings)
    events_json: Any | None = Field(default=None, sa_column=Column(JSONB))
    raw_properties_json: Any | None = Field(default=None, sa_column=Column(JSONB))

    # Traceability
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    traffic_model_id_t: str | None = Field(default=None, max_length=32)
    bbox_used: str | None = Field(default=None, max_length=128)

    # TomTom geometry (Point/LineString). We store as generic geometry.
    geom: str | None = Field(
        default=None,
        sa_column=Column(Geometry(geometry_type="GEOMETRY", srid=4326)),
    )

    matched_edges: List["IncidentEdge"] = Relationship(
        back_populates="incident",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class IncidentEdge(SQLModel, table=True):
    __tablename__ = "incident_edges"
    __table_args__ = (UniqueConstraint("incident_id", "edge_id", name="uq_incident_edges"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    incident_id: uuid.UUID = Field(foreign_key="incidents.id", index=True)
    edge_id: uuid.UUID = Field(foreign_key="edges.id", index=True)

    rank: int | None = Field(default=None, index=True)
    match_dist_m: float | None = Field(default=None)
    overlap_m: float | None = Field(default=None)

    incident: Incident = Relationship(back_populates="matched_edges")
