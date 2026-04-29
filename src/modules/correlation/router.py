"""
src/modules/correlation/router.py

GET /api/v1/correlation/snapshots            → list tất cả snapshots (dates, slots)
GET /api/v1/correlation/nodes/{osm_node_id}  → ego-network của 1 node
  ?snapshot_mode=2024-08-27_Slot_0900        → chọn ngày + giờ cụ thể
  ?max_dist_m=1000                           → lọc theo khoảng cách
  ?min_corr=0.5                              → lọc theo giá trị tương quan
  ?adjacent_only=true                        → chỉ nodes kề nhau
"""
from fastapi import APIRouter, Query
from src.api.dependencies import DbSession
from src.modules.correlation import service

router = APIRouter(prefix="/correlation", tags=["Correlation"])


@router.get("/snapshots")
def get_snapshots(session: DbSession):
    """
    Trả về danh sách tất cả correlation snapshots.
    Dùng để frontend build slider chọn ngày + giờ.

    Response shape:
    ```json
    {
      "total": 256,
      "dates": ["2024-08-26", "2024-08-27", ...],
      "slots": ["Slot_0815", "Slot_0830", ...],
      "snapshots": [
        { "snapshot_id": "...", "method": "dmfm_bridge_h1",
          "mode": "2024-08-27_Slot_0900", "date": "2024-08-27",
          "slot": "Slot_0900", "mean_corr": 0.062, "is_active": true },
        ...
      ]
    }
    ```
    """
    return service.list_snapshots(session=session)


@router.get("/nodes/{osm_node_id}")
def get_node_correlation(
    osm_node_id: int,
    session: DbSession,
    snapshot_mode: str | None = Query(
        default=None,
        description="Mode snapshot cụ thể, VD: '2024-08-27_Slot_0900'. Mặc định dùng active snapshot.",
    ),
    max_dist_m: float | None = Query(default=None, description="Giới hạn khoảng cách (m)"),
    min_corr: float | None = Query(default=None, ge=0.0, le=1.0, description="|corr| tối thiểu"),
    adjacent_only: bool = Query(default=False, description="Chỉ trả về nodes kề nhau"),
):
    """
    Trả về top-K neighbors có tương quan cao nhất với node được chọn.

    - **osm_node_id**: OSM node ID (VD: 277956990)
    - **snapshot_mode**: chọn ngày+giờ cụ thể (lấy từ `/snapshots`). Nếu bỏ qua → dùng active
    - **max_dist_m**: lọc theo khoảng cách (mét)
    - **min_corr**: lọc theo |corr| tối thiểu
    - **adjacent_only**: chỉ trả về nodes có edge trực tiếp
    """
    result = service.get_node_correlations(
        session=session,
        node_index=osm_node_id,
        max_dist_m=max_dist_m,
        min_corr=min_corr,
        snapshot_mode=snapshot_mode,
    )
    if adjacent_only:
        result["neighbors"] = [n for n in result["neighbors"] if n["is_adjacent"]]
        result["total"] = len(result["neighbors"])
    return result