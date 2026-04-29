"""src/modules/traffic/schemas.py — cập nhật cho Edge-as-Node paradigm"""
import uuid
from sqlmodel import SQLModel


class NodeResponse(SQLModel):
    """
    1 model node = 1 đoạn đường (OSM directed edge).
    Tọa độ = midpoint của đoạn đường (để vẽ marker trên bản đồ).
    """
    node_id: uuid.UUID
    osm_node_id: int        # = node_index (surrogate key)
    node_index: int
    lat: float              # midpoint lat
    lon: float              # midpoint lon — backend dùng "lon"
    street_name: str | None # OSM edge ID string "u_v"
    degree: float | None = None


class EdgeResponse(SQLModel):
    """Connection giữa 2 model nodes."""
    edge_id: uuid.UUID
    source_osm_id: int
    target_osm_id: int
    source_lat: float
    source_lon: float
    target_lat: float
    target_lon: float
    length_m: float | None = None