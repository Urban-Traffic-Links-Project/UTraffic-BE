"""src/modules/traffic/schemas.py"""
import uuid
from sqlmodel import SQLModel


class NodeResponse(SQLModel):
    """Response cho 1 node — đủ thông tin để vẽ CircleMarker trên Leaflet."""
    node_id: uuid.UUID          # UUID nội bộ DB
    osm_node_id: int            # ID thật của OSM (dùng làm key với frontend)
    node_index: int             # 0..304
    lat: float
    lon: float
    degree: float | None = None
    betweenness_norm: float | None = None
    street_name: str | None = None


class EdgeResponse(SQLModel):
    """Response cho 1 edge — để vẽ Polyline giữa 2 nodes."""
    edge_id: uuid.UUID
    source_osm_id: int
    target_osm_id: int
    source_lat: float
    source_lon: float
    target_lat: float
    target_lon: float
    length_m: float | None = None