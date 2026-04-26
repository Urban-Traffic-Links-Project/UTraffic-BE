"""
src/storage/models/ml.py
Nhóm bảng học máy và phiên bản mô hình:
  - model_versions        : thông tin mô hình T-GCN đã huấn luyện
  - model_horizon_metrics : metrics chi tiết theo từng horizon (1-4)
  - predictions           : kết quả dự báo tốc độ (hypertable)
"""

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Column, DateTime, Field, Relationship, SQLModel


class CongestionLabel(str, Enum):
    free = "free"
    slow = "slow"
    congested = "congested"


# ════════════════════════════════════════════════════════════
# Bảng 11: model_versions
# Mỗi lần train xong → tạo 1 bản ghi ở đây
# is_active=True → đây là model đang được dùng để inference
# ════════════════════════════════════════════════════════════
class ModelVersion(SQLModel, table=True):
    """
    Lưu thông tin về model T-GCN đã huấn luyện.

    Ví dụ: model T-GCN v1.0 train trên 385 node,
    đạt RMSE=0.443 tại horizon 1 (15 phút)
    """

    __tablename__ = "model_versions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    graph_snapshot_id: uuid.UUID = Field(foreign_key="graph_snapshots.id")
    model_name: str = Field(max_length=100)  # VD: "T-GCN", "DTC-STGCN"
    version: str = Field(max_length=50)  # VD: "v1.0", "v1.1"

    # Hyperparameters
    num_nodes: int
    input_dim: int
    hidden_dim: int
    seq_len: int  # Số bước lịch sử đầu vào (12 = 3 giờ)
    pred_len: int  # Số bước dự báo (4 = 60 phút)

    # Metrics tổng hợp (chi tiết theo horizon xem bảng model_horizon_metrics)
    rmse: float | None = Field(default=None)
    mae: float | None = Field(default=None)
    r2: float | None = Field(default=None)

    checkpoint_path: str | None = Field(default=None, max_length=500)
    is_active: bool = Field(default=False)
    trained_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    snapshot: "GraphSnapshot" = Relationship(back_populates="model_versions")
    metrics: list["ModelHorizonMetric"] = Relationship(
        back_populates="model_version",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    predictions: list["Prediction"] = Relationship(
        back_populates="model_version",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    correlation_snapshots: list["CorrelationSnapshot"] = Relationship(
        back_populates="model_version",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


# ════════════════════════════════════════════════════════════
# Bảng 12: model_horizon_metrics
# Tách riêng metrics theo từng horizon để so sánh dễ hơn
# Mỗi model_version có đúng 4 bản ghi (horizon 1, 2, 3, 4)
# ════════════════════════════════════════════════════════════
class ModelHorizonMetric(SQLModel, table=True):
    """
    Metrics theo từng bước dự báo:
      horizon_step=1 → dự báo T+15 phút
      horizon_step=2 → dự báo T+30 phút
      horizon_step=3 → dự báo T+45 phút
      horizon_step=4 → dự báo T+60 phút
    """

    __tablename__ = "model_horizon_metrics"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    model_version_id: uuid.UUID = Field(foreign_key="model_versions.id", index=True)
    horizon_step: int  # 1, 2, 3, hoặc 4
    rmse: float | None = Field(default=None)
    mae: float | None = Field(default=None)
    r2: float | None = Field(default=None)
    var_score: float | None = Field(default=None)  # Explained Variance Score

    model_version: ModelVersion = Relationship(back_populates="metrics")


# ════════════════════════════════════════════════════════════
# Bảng 13: predictions
# Kết quả dự báo từ T-GCN — 385 node × 4 horizon = 1540 bản ghi/lần chạy
# Chạy mỗi 15 phút → ~147,840 bản ghi/ngày
# Cần cấu hình là TimescaleDB hypertable + TTL 30 ngày
# ════════════════════════════════════════════════════════════
class Prediction(SQLModel, table=True):
    """
    1 bản ghi = dự báo tốc độ cho 1 node tại 1 horizon cụ thể.

    Ví dụ:
      node_id       = <ngã tư Điện Biên Phủ - Võ Thị Sáu>
      predicted_at  = 2024-01-15 08:00:00  (lúc chạy inference)
      target_time   = 2024-01-15 08:15:00  (dự báo cho lúc này)
      horizon_step  = 1
      predicted_speed = 18.3 km/h
      congestion_label = "slow"
    """

    __tablename__ = "predictions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    model_version_id: uuid.UUID = Field(foreign_key="model_versions.id")
    node_id: uuid.UUID = Field(foreign_key="nodes.id", index=True)
    # Thời điểm chạy inference
    predicted_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    target_time: datetime  # Thời điểm được dự báo
    horizon_step: int  # 1–4

    predicted_speed: float | None = Field(default=None)
    congestion_index: float | None = Field(default=None)
    congestion_label: CongestionLabel | None = Field(default=None)

    # Relationship
    model_version: ModelVersion = Relationship(back_populates="predictions")
