from __future__ import annotations

from fastapi import APIRouter, Query

from src.api.dependencies import DbSession
from src.modules.incidents import service
from src.modules.incidents.schemas import IncidentFetchResult, IncidentListResponse

router = APIRouter(prefix="/incidents", tags=["Incidents"])


@router.post("/fetch", response_model=IncidentFetchResult)
def fetch_incidents_and_match(
    session: DbSession,
    buffer_m: float = Query(default=45.0, ge=5.0, le=200.0, description="Match buffer in meters"),
    limit_edges: int = Query(default=8, ge=1, le=50, description="Max matched edges per incident"),
    category_filter: str | None = Query(default=None, description="TomTom categoryFilter (e.g. Accident,Jam,RoadClosed or 1,6,8)"),
    t: str | None = Query(default=None, description="Traffic Model ID (t). If omitted, use current and read TrafficModelID header."),
):
    fetched_at, traffic_model_id, bbox_used, total_received, total_saved = (
        service.fetch_match_and_save_incidents(
            session,
            buffer_m=buffer_m,
            limit_edges=limit_edges,
            category_filter=category_filter,
            t=t,
        )
    )
    return IncidentFetchResult(
        fetched_at=fetched_at,
        traffic_model_id_t=traffic_model_id,
        bbox_used=bbox_used,
        total_received=total_received,
        total_saved=total_saved,
    )


@router.get("", response_model=IncidentListResponse)
def list_incidents(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
):
    incidents = service.list_recent_incidents_with_edges_geojson(session, limit=limit)
    return IncidentListResponse(total=len(incidents), incidents=incidents)

