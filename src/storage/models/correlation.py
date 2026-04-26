"""
src/storage/models/correlation.py
Nhóm bảng phân tích tương quan — tính năng cốt lõi của UTraffic:
  - correlation_snapshots     : metadata mỗi lần chạy correlation job
  - node_correlations         : các cặp node có |corr| >= 0.7
  - node_correlation_cache    : JSONB blob top-20 neighbors per node (tối ưu click)
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


# ════════════════════════════════════════════════════════════
# Bảng 14: correlation_snapshots
# Metadata của mỗi lần chạy correlation job
# ════════════════════════════════════════════════════════════
class CorrelationSnapshot(SQLModel, table=True):
    """
    Mỗi lần chạy correlation_job.py → tạo 1 snapshot.
    is_active=True → snapshot này đang được API serve.

    npz_path: đường dẫn tới file .npz chứa ma trận 385×385 đầy đủ
              (chỉ lưu file path, không lưu ma trận vào DB)
    """

    __tablename__ = "correlation_snapshots"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    model_version_id: uuid.UUID = Field(foreign_key="model_versions.id", index=True)
    method: str = Field(default="detrended_pearson", max_length=100)
    mode: str = Field(default="prediction", max_length=50)
    num_nodes: int
    mean_corr: float | None = Field(default=None)
    std_corr: float | None = Field(default=None)
    npz_path: str | None = Field(default=None, max_length=500)
    is_active: bool = Field(default=False)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_version: "ModelVersion" = Relationship(back_populates="correlation_snapshots")


# ════════════════════════════════════════════════════════════
# Bảng 15: node_correlations
# Lưu các cặp (node_a, node_b) có |corr| >= 0.7
# Từ dữ liệu thực: ~34,307 cặp trong 385×385 ma trận
# ════════════════════════════════════════════════════════════
class NodeCorrelation(SQLModel, table=True):
    """
    1 bản ghi = 1 cặp node có tương quan đáng kể.

    rank_from_a: thứ hạng của node_b trong danh sách neighbor của node_a
                 (xếp theo |corr| giảm dần)
    is_adjacent: node_a và node_b có cạnh trực tiếp trên đồ thị không?
                 Hữu ích để phân biệt "tương quan vì kề nhau" hay "tương quan xa"
    """

    __tablename__ = "node_correlations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(foreign_key="correlation_snapshots.id", index=True)
    node_a_id: uuid.UUID = Field(foreign_key="nodes.id", index=True)
    node_b_id: uuid.UUID = Field(foreign_key="nodes.id", index=True)
    correlation_value: float  # Giá trị trong [-1, 1]
    rank_from_a: int | None = Field(default=None)
    rank_from_b: int | None = Field(default=None)
    is_adjacent: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ════════════════════════════════════════════════════════════
# Bảng 16 (bonus): node_correlation_cache
# Tối ưu hóa quan trọng nhất cho tính năng click-node
# Pre-aggregate top-20 neighbors → JSONB blob → load vào Redis khi khởi động
# ════════════════════════════════════════════════════════════
class NodeCorrelationCache(SQLModel, table=True):
    """
    Cache top-20 neighbors của mỗi node dưới dạng JSONB.

    Khi user click node X:
    1. Correlation Service GET từ Redis → < 1ms
    2. Cache miss → đọc bảng này → vẫn nhanh hơn JOIN node_correlations

    Cấu trúc JSON trong cột neighbors_json:
    [
      {"node_id": "uuid", "corr": 0.9979, "lat": 10.765, "lon": 106.678, "rank": 1},
      {"node_id": "uuid", "corr": 0.9975, "lat": 10.762, "lon": 106.675, "rank": 2},
      ...
    ]

    Tổng kích thước: 385 node × ~1.6KB/node ≈ 0.63 MB — đủ nhỏ để load hết vào Redis.
    """

    __tablename__ = "node_correlation_cache"

    node_id: uuid.UUID = Field(foreign_key="nodes.id", primary_key=True)
    snapshot_id: uuid.UUID = Field(foreign_key="correlation_snapshots.id")

    # JSONB: cần dùng sa_column để SQLAlchemy hiểu kiểu PostgreSQL đặc biệt này
    neighbors_json: Any = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
