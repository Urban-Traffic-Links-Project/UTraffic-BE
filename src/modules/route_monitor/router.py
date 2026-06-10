"""
src/modules/route_monitor/router.py

API endpoints theo dõi kẹt xe theo tuyến đường cố định.

Endpoints:
  GET  /api/v1/route-monitor/status        → trạng thái mới nhất
  GET  /api/v1/route-monitor/history       → lịch sử snapshots
  GET  /api/v1/route-monitor/snapshot-at   → snapshot gần nhất với datetime chỉ định
  POST /api/v1/route-monitor/collect       → thu thập thủ công
  POST /api/v1/route-monitor/seed          → seed điểm A, B
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import DbSession
from src.modules.route_monitor import service
from src.modules.route_monitor.schemas import (
    CollectResponse,
    RouteSnapshotAtResponse,
    RouteStatusResponse,
    SeedResponse,
)

router = APIRouter(prefix="/route-monitor", tags=["Route Monitor"])


@router.post("/seed", response_model=SeedResponse)
def seed(session: DbSession):
    """Seed điểm A và B vào DB (idempotent — an toàn khi gọi nhiều lần)."""
    created = service.seed_route_points(session)
    return SeedResponse(
        message=f"Đã seed {created} điểm mới" if created else "Điểm đã tồn tại",
        created=created,
    )


@router.post("/collect", response_model=CollectResponse)
async def collect_now(session: DbSession):
    """Thu thập snapshot thủ công từ TomTom API cho cả tuyến đường."""
    created = await service.collect_route_snapshots(session)
    return CollectResponse(
        message=f"Đã thu thập {created} snapshots",
        created=created,
    )


@router.get("/status", response_model=RouteStatusResponse)
def get_status(session: DbSession):
    """
    Trả về trạng thái giao thông mới nhất của toàn tuyến.
    Bao gồm thông tin từng điểm (A, B) và tổng hợp trung bình.
    """
    result = service.get_latest_route_status(session)
    return result


@router.get("/history")
def get_history(
    session: DbSession,
    hours: int = Query(default=24, ge=1, le=168),
    point: str | None = Query(default=None, description="Lọc theo điểm: A hoặc B"),
):
    """
    Trả về lịch sử snapshots trong khoảng `hours` giờ gần nhất.
    Tối đa 168h (7 ngày). Tối đa 2000 records.
    """
    return service.get_route_history(session, hours=hours, point_label=point)


@router.get("/snapshot-at", response_model=RouteSnapshotAtResponse)
def get_snapshot_at(
    session: DbSession,
    dt: str = Query(
        alias="datetime",
        description="Thời điểm muốn xem, ISO 8601 (VD: 2024-08-26T10:30:00+07:00)",
    ),
    point: str | None = Query(default=None, description="Lọc theo điểm: A hoặc B"),
):
    """
    Tìm snapshot gần nhất với thời điểm `datetime` (trong khoảng ±30 phút).
    Trả về 404 nếu không có dữ liệu trong khoảng thời gian đó.
    """
    try:
        target_dt = datetime.fromisoformat(dt)
        # Đảm bảo luôn có timezone
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)
        # Chuyển về UTC
        target_dt = target_dt.astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Định dạng datetime không hợp lệ: '{dt}'. Dùng ISO 8601 (VD: 2024-08-26T10:30:00+07:00)",
        )

    result = service.get_snapshot_at_time(session, target_dt=target_dt, point_label=point)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không có dữ liệu trong khoảng ±30 phút xung quanh {dt}.",
        )

    return result
