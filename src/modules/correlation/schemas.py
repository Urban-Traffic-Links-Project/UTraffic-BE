"""src/modules/correlation/schemas.py"""
import uuid
from sqlmodel import SQLModel


class NeighborNode(SQLModel):
    """
    1 node trong danh sách tương quan — đủ info để frontend:
    - Vẽ CircleMarker tại (lat, lon)
    - Hiển thị số corr lên trên marker
    - Vẽ Polyline từ selected_node đến node này
    - Tô màu theo |corr| (đỏ cao, xanh thấp)
    - Filter theo dist_m và is_adjacent cho Ego-Network
    """
    node_id: uuid.UUID
    osm_node_id: int
    node_index: int
    lat: float
    lon: float
    corr: float           # [-1, 1] — giá trị tương quan
    rank: int             # 0 = tương quan cao nhất
    dist_m: float         # khoảng cách thực tế tính bằng mét
    is_adjacent: bool     # có edge trực tiếp với selected node không


class CorrelationResponse(SQLModel):
    """Response khi click 1 node."""
    selected_node: dict           # {osm_node_id, lat, lon}
    neighbors: list[NeighborNode] # tất cả 304 nodes còn lại, sorted by |corr| desc
    total: int