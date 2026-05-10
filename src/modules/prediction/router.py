from fastapi import APIRouter
from src.api.dependencies import DbSession
from src.modules.prediction import service
import uuid

router = APIRouter(prefix="/predict", tags=["Prediction"])

@router.get("/affected/{incident_id}")
def get_affected(session: DbSession, incident_id: uuid.UUID, horizon: int = 1, mode: str = "spread", radius: float = 3.0): 
    affected, _ = service.predict_spread_for_incident(session, incident_id, horizon, mode, radius)
    return {"items": affected}

@router.get("/spread/{incident_id}")
def get_spread(session: DbSession, incident_id: uuid.UUID, horizon: int = 1, mode: str = "spread", radius: float = 3.0): 
    _, spread = service.predict_spread_for_incident(session, incident_id, horizon, mode, radius)
    if not spread:
        return {"center": {"lat": 10.77, "lng": 106.69}, "rings": [], "arrows": []}
    return spread
