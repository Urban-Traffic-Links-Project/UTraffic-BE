"""
src/modules/correlation/router.py

GET /api/v1/correlation/nodes/{osm_node_id}
    → Ego-Network: tất cả nodes tương quan khi click 1 node

Query params cho Ego-Network Focus (frontend truyền lên):
    max_dist_m  : chỉ lấy nodes trong phạm vi X mét (VD: 1000 = 1km)
    min_corr    : chỉ lấy nodes có |corr| >= X (VD: 0.5)
    adjacent_only: chỉ lấy nodes có edge trực tiếp

Ví dụ call từ frontend:
    /api/v1/correlation/nodes/366381388
    /api/v1/correlation/nodes/366381388?max_dist_m=1000&min_corr=0.5
    /api/v1/correlation/nodes/366381388?adjacent_only=true
"""
from fastapi import APIRouter, Query
from src.api.dependencies import DbSession
from src.modules.correlation import service
from src.modules.correlation.schemas import CorrelationResponse

router = APIRouter(prefix="/correlation", tags=["Correlation"])


@router.get("/nodes/{osm_node_id}")
def get_node_correlation(
    osm_node_id: int,
    session: DbSession,
    max_dist_m: float | None = Query(
        default=None,
        description="Chỉ lấy nodes trong phạm vi X mét. VD: 1000"
    ),
    min_corr: float | None = Query(
        default=None,
        ge=0.0, le=1.0,
        description="Chỉ lấy nodes có |corr| >= X. VD: 0.5"
    ),
    adjacent_only: bool = Query(
        default=False,
        description="Chỉ lấy nodes có edge trực tiếp với node này"
    ),
):
    """
    Ego-Network Focus: khi user click node X.

    Trả về tất cả nodes tương quan, mỗi node kèm:
    - lat/lon để vẽ marker
    - corr để hiển thị số và tô màu
    - dist_m để frontend filter theo bán kính
    - is_adjacent để vẽ Polyline nối

    Frontend dùng response này để:
    1. Làm mờ toàn bộ map (opacity 0.1)
    2. Highlight node được click + các neighbors
    3. Vẽ Polyline từ selected → mỗi neighbor
    4. Hiển thị số corr nổi trên mỗi neighbor marker
    """
    result = service.get_node_correlations(
        session=session,
        osm_node_id=osm_node_id,
        max_dist_m=max_dist_m,
        min_corr=min_corr,
    )

    # Filter adjacent_only sau khi query (tránh logic phức tạp trong service)
    if adjacent_only:
        result["neighbors"] = [
            n for n in result["neighbors"] if n["is_adjacent"]
        ]
        result["total"] = len(result["neighbors"])

    return result