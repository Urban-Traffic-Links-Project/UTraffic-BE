"""
src/storage/models/graph.py
Nhóm bảng đồ thị đường bộ:
  nodes, edges, tomtom_segments, segment_edge_mappings, graph_snapshots

Lưu ý về PostGIS:
  Kiểu GEOMETRY không phải Python thuần — phải dùng sa_column với
  geoalchemy2.types.Geometry để SQLAlchemy hiểu.
"""

import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import Column
from sqlmodel import Field, Relationship, SQLModel


# ══════════════════════════════════════════════════════════════════════════════
# BẢNG 4: graph_snapshots
# ══════════════════════════════════════════════════════════════════════════════
class GraphSnapshot(SQLModel, table=True):
    """
    Phiên bản hóa cấu trúc đồ thị — mỗi lần chạy lại pipeline map-matching
    sẽ tạo ra 1 snapshot mới. is_active=True là phiên bản đang dùng.
    """

    __tablename__ = "graph_snapshots"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    version: str = Field(max_length=50)  # VD: "v1.0", "v1.1"
    num_nodes: int = Field(default=0)
    num_edges: int = Field(default=0)
    coverage_ratio: float | None = Field(default=None)
    npz_path: str | None = Field(default=None, max_length=500)  # Path tới file .npz
    is_active: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_versions: list["ModelVersion"] = Relationship(back_populates="snapshot")


# ══════════════════════════════════════════════════════════════════════════════
# BẢNG 5: nodes
# ══════════════════════════════════════════════════════════════════════════════
class Node(SQLModel, table=True):
    """
    Mỗi node = 1 giao lộ trong subgraph đã map-match.
    node_index: chỉ số trong tensor T-GCN — PHẢI khớp với file .npz
    """

    __tablename__ = "nodes"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    osm_node_id: int = Field(unique=True, index=True)  # ID node trong OpenStreetMap
    node_index: int = Field(index=True)  # Vị trí trong tensor
    lat: float = Field()
    lon: float = Field()

    # PostGIS GEOMETRY(POINT) — dùng sa_column vì SQLModel chưa hỗ trợ trực tiếp
    # GIST index tạo trong Alembic migration (không tạo được qua SQLModel)
    geom: str | None = Field(
        default=None,
        sa_column=Column(Geometry(geometry_type="POINT", srid=4326)),
    )

    # Đặc trưng topo học (tính từ subgraph sau map-matching)
    degree: float | None = Field(default=None)
    betweenness_norm: float | None = Field(default=None)
    lat_norm: float | None = Field(default=None)  # Chuẩn hóa về [0, 1]
    lon_norm: float | None = Field(default=None)
    street_name: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    outgoing_edges: list["Edge"] = Relationship(
        back_populates="source_node",
        sa_relationship_kwargs={"foreign_keys": "[Edge.source_node_id]"},
    )
    incoming_edges: list["Edge"] = Relationship(
        back_populates="target_node",
        sa_relationship_kwargs={"foreign_keys": "[Edge.target_node_id]"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# BẢNG 6: edges
# ══════════════════════════════════════════════════════════════════════════════
class Edge(SQLModel, table=True):
    """
    Mỗi edge = 1 đoạn đường có hướng (directed).
    Đường 2 chiều → 2 edge ngược nhau.
    """

    __tablename__ = "edges"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_node_id: uuid.UUID = Field(foreign_key="nodes.id", index=True)
    target_node_id: uuid.UUID = Field(foreign_key="nodes.id", index=True)
    source_index: int = Field()  # node_index của source (để map với tensor)
    target_index: int = Field()  # node_index của target

    geom: str | None = Field(
        default=None,
        sa_column=Column(Geometry(geometry_type="LINESTRING", srid=4326)),
    )

    length_m: float | None = Field(default=None)
    maxspeed_kmh: float | None = Field(default=None)
    lanes: float | None = Field(default=None)
    highway_type: int | None = Field(default=None)  # Mã hóa từ OSM highway tag
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    source_node: Node = Relationship(
        back_populates="outgoing_edges",
        sa_relationship_kwargs={"foreign_keys": "[Edge.source_node_id]"},
    )
    target_node: Node = Relationship(
        back_populates="incoming_edges",
        sa_relationship_kwargs={"foreign_keys": "[Edge.target_node_id]"},
    )
    mappings: list["SegmentEdgeMapping"] = Relationship(
        back_populates="edge", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    observations: list["TrafficObservation"] = Relationship(
        back_populates="edge", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    hourly_stats: list["HourlyTrafficStat"] = Relationship(
        back_populates="edge", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


# ══════════════════════════════════════════════════════════════════════════════
# BẢNG 7: tomtom_segments
# ══════════════════════════════════════════════════════════════════════════════
class TomtomSegment(SQLModel, table=True):
    """Phân đoạn đường gốc từ TomTom API (trước khi map-match sang OSM edge)."""

    __tablename__ = "tomtom_segments"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    segment_id: int = Field(unique=True, index=True)  # TomTom segment ID
    geom: str | None = Field(
        default=None,
        sa_column=Column(Geometry(geometry_type="LINESTRING", srid=4326)),
    )
    street_name: str | None = Field(default=None, max_length=255)
    frc: int | None = Field(default=None)  # Functional Road Class (0–7)
    speed_limit_kmh: float | None = Field(default=None)
    distance_m: float | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    mappings: list["SegmentEdgeMapping"] = Relationship(
        back_populates="segment",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# BẢNG 8: segment_edge_mappings
# ══════════════════════════════════════════════════════════════════════════════
class SegmentEdgeMapping(SQLModel, table=True):
    """
    Bảng junction nhiều-nhiều: TomTom segment ↔ OSM edge
    1 segment TomTom dài → nhiều edge OSM ngắn
    1 edge OSM → có thể nhận data từ nhiều segment lân cận
    """

    __tablename__ = "segment_edge_mappings"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    segment_id: uuid.UUID = Field(foreign_key="tomtom_segments.id", index=True)
    edge_id: uuid.UUID = Field(foreign_key="edges.id", index=True)
    match_dist_m: float | None = Field(default=None)  # Khoảng cách snap (m)
    matched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Relationship
    segment: TomtomSegment = Relationship(back_populates="mappings")
    edge: Edge = Relationship(back_populates="mappings")
