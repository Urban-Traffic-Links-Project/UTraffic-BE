"""
src/modules/forecast/schemas.py

Response schemas cho Forecast API.
"""
from pydantic import BaseModel


class NodeInfo(BaseModel):
    node_id: str
    osm_node_id: int
    node_index: int
    lat: float
    lon: float
    street_name: str


class NeighborInfo(BaseModel):
    osm_node_id: int
    node_index: int
    lat: float
    lon: float
    street_name: str
    corr: float
    dist_m: float
    rank: int
    is_adjacent: bool


class ForecastNodeResponse(BaseModel):
    base_time: str
    base_date: str
    base_slot: str
    predicted_time: str
    predicted_slot: str
    horizon: int
    horizon_minutes: int
    source: str  # "historical_bundle" | "dmfm_online"
    pred_idx: int
    selected_node: NodeInfo
    neighbors: list[NeighborInfo]
    total: int


class SnapshotsResponse(BaseModel):
    dates: list[str]
    slots: list[str]
    total: int
    available_horizons: list[int]
    horizon_labels: dict[str, str]
