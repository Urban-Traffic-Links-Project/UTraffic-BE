"""
src/modules/traffic/router.py

GET /api/v1/traffic/nodes  → 305 nodes để vẽ bản đồ
GET /api/v1/traffic/edges  → 429 edges để vẽ Polyline

Redis Cache (DB 3 — API Cache):
  nodes:all  → JSON (TTL 60 giây)
  edges:all  → JSON (TTL 60 giây)
"""
from fastapi import APIRouter

from src.api.dependencies import DbSession
from src.integrations.cache_helpers import get_json_cache, set_json_cache
from src.integrations.redis_client import get_redis_api
from src.modules.traffic import service
from src.modules.traffic.schemas import EdgeResponse, NodeResponse

router = APIRouter(prefix="/traffic", tags=["Traffic"])

API_CACHE_TTL = 60  # 60 giây — dữ liệu topology thay đổi rất ít


@router.get("/nodes", response_model=list[NodeResponse])
async def get_nodes(session: DbSession):
    """
    Trả về tất cả 305 nodes với tọa độ thật.
    Frontend dùng để vẽ CircleMarker lên Leaflet khi load trang Correlation.

    Cache: Redis-API key "nodes:all" TTL 60 giây.
    Nếu topology thay đổi (admin cập nhật), key sẽ được invalidate.
    """
    redis_api = get_redis_api()
    cache_key = "nodes:all"

    cached = await get_json_cache(redis_api, cache_key)
    if cached is not None:
        return cached

    nodes = service.get_all_nodes(session)
    result = [
        NodeResponse(
            node_id=n.id,
            osm_node_id=n.osm_node_id,
            node_index=n.node_index,
            lat=n.lat,
            lon=n.lon,
            degree=n.degree,
            betweenness_norm=n.betweenness_norm,
            street_name=n.street_name,
        )
        for n in nodes
    ]
    # Serialize via dict để cache (Pydantic model không JSON serializable trực tiếp)
    serializable = [r.model_dump() for r in result]
    await set_json_cache(redis_api, cache_key, serializable, API_CACHE_TTL)
    return result


@router.get("/edges", response_model=list[EdgeResponse])
async def get_edges(session: DbSession):
    """
    Trả về 429 edges với tọa độ source+target.
    Frontend dùng để vẽ đường nối giữa các nodes (Semantic Zooming level cao).

    Cache: Redis-API key "edges:all" TTL 60 giây.
    """
    redis_api = get_redis_api()
    cache_key = "edges:all"

    cached = await get_json_cache(redis_api, cache_key)
    if cached is not None:
        return cached

    edges = service.get_all_edges(session)
    result = [EdgeResponse(**e) for e in edges]
    serializable = [r.model_dump() for r in result]
    await set_json_cache(redis_api, cache_key, serializable, API_CACHE_TTL)
    return result