from fastapi import APIRouter
from src.api.dependencies import DbSession
from src.integrations.cache_helpers import get_json_cache, set_json_cache
from src.integrations.redis_client import get_redis_inference
from src.modules.prediction import service
import uuid

router = APIRouter(prefix="/predict", tags=["Prediction"])

INFERENCE_CACHE_TTL = 30  # 30 giây — khớp thiết kế trong báo cáo


@router.get("/affected/{incident_id}")
async def get_affected(
    session: DbSession,
    incident_id: uuid.UUID,
    horizon: int = 1,
    mode: str = "spread",
    radius: float = 3.0,
):
    """
    Trả về danh sách các đoạn đường bị ảnh hưởng bởi sự cố.

    Cache: Redis-Inference key "{mode}:{incident_id}:h{horizon}:r{radius_x10}" TTL 30 giây.
    Key pattern theo thiết kế báo cáo: {mode}:{incident_id}:h{horizon}:r{radius}
    """
    redis_inf = get_redis_inference()
    radius_x10 = int(radius * 10)
    cache_key = f"{mode}:{incident_id}:h{horizon}:r{radius_x10}:affected"

    cached = await get_json_cache(redis_inf, cache_key)
    if cached is not None:
        return cached

    affected, _ = service.predict_spread_for_incident(session, incident_id, horizon, mode, radius)
    result = {"items": affected}
    await set_json_cache(redis_inf, cache_key, result, INFERENCE_CACHE_TTL)
    return result


@router.get("/spread/{incident_id}")
async def get_spread(
    session: DbSession,
    incident_id: uuid.UUID,
    horizon: int = 1,
    mode: str = "spread",
    radius: float = 3.0,
):
    """
    Trả về dữ liệu phân tích lan truyền (spread map) hoặc truy nguyên nguyên nhân (cause map).

    Cache: Redis-Inference key "{mode}:{incident_id}:h{horizon}:r{radius_x10}:spread" TTL 30 giây.
    """
    redis_inf = get_redis_inference()
    radius_x10 = int(radius * 10)
    cache_key = f"{mode}:{incident_id}:h{horizon}:r{radius_x10}:spread"

    cached = await get_json_cache(redis_inf, cache_key)
    if cached is not None:
        return cached

    _, spread = service.predict_spread_for_incident(session, incident_id, horizon, mode, radius)
    if not spread:
        result = {"center": {"lat": 10.77, "lng": 106.69}, "rings": [], "arrows": []}
    else:
        result = spread

    await set_json_cache(redis_inf, cache_key, result, INFERENCE_CACHE_TTL)
    return result
