"""
src/modules/traffic/router.py

GET /api/v1/traffic/nodes  → 305 nodes để vẽ bản đồ
GET /api/v1/traffic/edges  → 429 edges để vẽ Polyline
"""
from fastapi import APIRouter
from src.api.dependencies import DbSession
from src.modules.traffic import service
from src.modules.traffic.schemas import NodeResponse, EdgeResponse

router = APIRouter(prefix="/traffic", tags=["Traffic"])


@router.get("/nodes", response_model=list[NodeResponse])
def get_nodes(session: DbSession):
    """
    Trả về tất cả 305 nodes với tọa độ thật.
    Frontend dùng để vẽ CircleMarker lên Leaflet khi load trang Correlation.
    """
    nodes = service.get_all_nodes(session)
    return [
        NodeResponse(
            node_id=n.id,
            osm_node_id=n.osm_node_id,
            node_index=n.node_index,
            lat=n.lat,
            lon=n.lon,
            degree=n.degree,
            betweenness_norm=n.betweenness_norm,
        )
        for n in nodes
    ]


@router.get("/edges", response_model=list[EdgeResponse])
def get_edges(session: DbSession):
    """
    Trả về 429 edges với tọa độ source+target.
    Frontend dùng để vẽ đường nối giữa các nodes (Semantic Zooming level cao).
    """
    edges = service.get_all_edges(session)
    return [EdgeResponse(**e) for e in edges]