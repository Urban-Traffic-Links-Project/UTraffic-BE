from fastapi import APIRouter

from src.api.dependencies import DbSession
from src.modules.traffic_dashboard import service
from src.modules.traffic_dashboard.schemas import (
    DashboardOverviewResponse,
    TopCongestedSegmentResponse,
    TrafficHistoryPointResponse,
    TrafficIncidentResponse,
)

router = APIRouter(prefix="/traffic-dashboard", tags=["Traffic Dashboard"])


@router.post("/seed-monitored-segments")
def seed_monitored_segments(session: DbSession, limit: int = 50):
    created = service.seed_monitored_segments_from_nodes(session, limit=limit)
    return {"message": "Seed monitored segments completed", "created": created}


@router.post("/collect-now")
async def collect_now(session: DbSession):
    created = await service.collect_traffic_snapshots(session)
    return {"message": "Collect traffic snapshots completed", "created": created}


@router.get("/overview", response_model=DashboardOverviewResponse)
def overview(session: DbSession):
    return service.get_dashboard_overview(session)


@router.get("/top-congested", response_model=list[TopCongestedSegmentResponse])
def top_congested(session: DbSession, limit: int = 10):
    return service.get_top_congested(session, limit=limit)


@router.get("/history", response_model=list[TrafficHistoryPointResponse])
def history(session: DbSession, hours: int = 24):
    return service.get_history(session, hours=hours)


@router.get("/incidents", response_model=list[TrafficIncidentResponse])
async def incidents():
    try:
        return await service.get_incidents()
    except Exception as error:
        print("[TrafficDashboard] Failed to load incidents:", error)
        return []