"""
src/storage/models/traffic.py
Nhóm bảng dữ liệu giao thông:
  - traffic_observations  : quan sát giao thông theo khung giờ 15 phút (hypertable)
  - hourly_traffic_stats  : aggregate theo giờ (TimescaleDB continuous aggregate)
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlmodel import Column, DateTime, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .graph import Edge


# ════════════════════════════════════════════════════════════
# Bảng 9: traffic_observations
# Đây là bảng lớn nhất — mỗi 15 phút ghi ~385 bản ghi (1 per edge)
# Được cấu hình là TimescaleDB hypertable, phân vùng theo observed_at
# ════════════════════════════════════════════════════════════
class TrafficObservation(SQLModel, table=True):
    """
    Lưu trạng thái giao thông tại một cạnh đường trong 1 khung giờ 15 phút.

    Ví dụ 1 bản ghi:
      edge_id      = <uuid của đường Điện Biên Phủ đoạn A-B>
      observed_at  = 2024-01-15 08:00:00
      average_speed = 23.5   (km/h — đang kẹt xe)
      congestion_index = 0.67 (1 = tắc hoàn toàn)
    """

    __tablename__ = "traffic_observations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    edge_id: uuid.UUID = Field(foreign_key="edges.id", index=True)
    # TimescaleDB partition column
    observed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    time_slot: str | None = Field(default=None, max_length=20)  # VD: "Slot_0800"

    # Dữ liệu tốc độ từ TomTom
    average_speed: float | None = Field(default=None)
    harmonic_avg_speed: float | None = Field(default=None)
    median_speed: float | None = Field(default=None)
    std_speed: float | None = Field(default=None)
    avg_travel_time: float | None = Field(default=None)
    travel_time_ratio: float | None = Field(default=None)

    # Đặc trưng tính toán thêm (Feature Engineering)
    congestion_index: float | None = Field(default=None)  # CI = 1 - v_obs/v_limit
    speed_limit_ratio: float | None = Field(default=None)  # SLR = v_obs/v_limit
    sample_size: int | None = Field(default=None)  # Số xe đi qua

    # Relationshihp
    edge: "Edge" = Relationship(back_populates="observations")


# ════════════════════════════════════════════════════════════
# Bảng 10: hourly_traffic_stats
# Aggregate tự động từ traffic_observations, nhóm theo giờ
# TimescaleDB continuous aggregate policy tự cập nhật bảng này
# ════════════════════════════════════════════════════════════
class HourlyTrafficStat(SQLModel, table=True):
    """
    Thống kê tốc độ trung bình theo giờ cho mỗi cạnh đường.
    Khóa chính composite: (bucket, edge_id)

    bucket = time_bucket('1 hour', observed_at)
    → VD: 2024-01-15 08:00:00 (gom tất cả data từ 8:00 đến 8:59)
    """

    __tablename__ = "hourly_traffic_stats"
    __table_args__ = {"info": {"skip_autogenerate": True}}

    # Composite PK — dùng sa_primary_key thay vì primary_key=True đơn lẻ
    bucket: datetime = Field(primary_key=True)
    edge_id: uuid.UUID = Field(foreign_key="edges.id", primary_key=True)

    avg_speed: float | None = Field(default=None)
    max_congestion: float | None = Field(default=None)

    # Relationshihp
    edge: "Edge" = Relationship(back_populates="hourly_stats")
