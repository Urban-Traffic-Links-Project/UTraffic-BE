from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class IncidentFetchResult(BaseModel):
    fetched_at: datetime
    traffic_model_id_t: str | None = None
    bbox_used: str
    total_received: int
    total_saved: int


class IncidentMatchedEdge(BaseModel):
    edge_id: uuid.UUID
    rank: int | None = None
    match_dist_m: float | None = None
    overlap_m: float | None = None
    geometry: dict[str, Any] = Field(description="GeoJSON geometry of the matched edge")


class IncidentDto(BaseModel):
    id: uuid.UUID
    tomtom_incident_id: str
    fetched_at: datetime
    icon_category: int | None = None
    icon_category_label: str | None = None
    magnitude_of_delay: int | None = None
    delay_seconds: int | None = None
    time_validity: Literal["present", "future"] | str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    events: Any | None = None
    raw_properties: Any | None = None
    geometry: Any | None = None
    matched_edges: list[IncidentMatchedEdge]


class IncidentListResponse(BaseModel):
    total: int
    incidents: list[IncidentDto]


class IncidentSessionDto(BaseModel):
    session_time: datetime
    incident_count: int


class IncidentSessionListResponse(BaseModel):
    sessions: list[IncidentSessionDto]


class IncidentHistoryResponse(BaseModel):
    actual_fetched_at: datetime | None
    incidents: list[IncidentDto]

