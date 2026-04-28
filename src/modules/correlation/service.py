"""src/modules/correlation/service.py"""
from fastapi import HTTPException, status
from sqlmodel import Session, select

from src.storage.models.graph import Node
from src.storage.models.correlation import NodeCorrelationCache


def get_node_correlations(
    session: Session,
    osm_node_id: int,
    max_dist_m: float | None = None,   # None = tidak filter jarak
    min_corr: float | None = None,     # None = tidak filter corr
) -> dict:
    """
    Đọc từ node_correlation_cache (JSONB) → trả về ngay, không cần join.
    
    Params:
        osm_node_id : ID OSM của node được click
        max_dist_m  : nếu truyền → chỉ lấy nodes trong phạm vi (mét)
        min_corr    : nếu truyền → chỉ lấy nodes có |corr| >= min_corr
    """
    # Tìm node theo osm_node_id
    node = session.exec(
        select(Node).where(Node.osm_node_id == osm_node_id)
    ).first()

    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node với osm_node_id={osm_node_id} không tồn tại",
        )

    # Đọc cache JSONB
    cache = session.exec(
        select(NodeCorrelationCache).where(
            NodeCorrelationCache.node_id == node.id
        )
    ).first()

    if not cache or not cache.neighbors_json:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chưa có dữ liệu correlation. Chạy seed_correlation.py trước.",
        )

    neighbors = cache.neighbors_json  # list of dict từ JSONB

    # Filter theo dist_m nếu có
    if max_dist_m is not None:
        neighbors = [n for n in neighbors if n["dist_m"] <= max_dist_m]

    # Filter theo |corr| nếu có
    if min_corr is not None:
        neighbors = [n for n in neighbors if abs(n["corr"]) >= min_corr]

    return {
        "selected_node": {
            "osm_node_id": node.osm_node_id,
            "node_id": str(node.id),
            "node_index": node.node_index,
            "lat": node.lat,
            "lon": node.lon,
        },
        "neighbors": neighbors,
        "total": len(neighbors),
    }