"""
src/modules/route_monitor/service.py

Service xử lý dữ liệu theo dõi kẹt xe theo tuyến đường.

Tuyến cố định:
  A: (10.794694, 106.792639) — 10°47'40.9"N 106°47'33.5"E
  B: (10.788056, 106.803500) — 10°47'17.0"N 106°48'12.6"E
  Khu vực: Xa lộ Hà Nội, TP. Thủ Đức, TPHCM
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from src.integrations.tomtom import get_flow_segment
from src.storage.models.route_monitor import RouteSegmentPoint, RouteTrafficSnapshot

# ─── Tuyến đường cố định ───────────────────────────────────────────────────────

ROUTE_NAME = "Xa lộ Hà Nội - TP. Thủ Đức"

ROUTE_POINTS_SEED = [
    {
        "route_name": ROUTE_NAME,
        "point_label": "A",
        "lat": 10.794694,
        "lon": 106.792639,
        "description": 'Điểm đầu tuyến — 10°47\'40.9"N 106°47\'33.5"E',
    },
    {
        "route_name": ROUTE_NAME,
        "point_label": "B",
        "lat": 10.788056,
        "lon": 106.803500,
        "description": 'Điểm cuối tuyến — 10°47\'17.0"N 106°48\'12.6"E',
    },
]

# ─── Helpers ───────────────────────────────────────────────────────────────────


def _calculate_status(
    current_speed: float | None,
    free_flow_speed: float | None,
    road_closure: bool,
) -> str:
    if road_closure:
        return "road_closed"
    if not current_speed or not free_flow_speed or free_flow_speed <= 0:
        return "unknown"
    ratio = current_speed / free_flow_speed
    if ratio <= 0.45:
        return "congested"
    if ratio <= 0.75:
        return "moderate"
    return "stable"


def _calculate_congestion_percent(
    current_speed: float | None,
    free_flow_speed: float | None,
) -> float | None:
    if current_speed is None or free_flow_speed is None or free_flow_speed <= 0:
        return None
    return max(0.0, min(100.0, round((1 - current_speed / free_flow_speed) * 100, 2)))


def _worst_status(statuses: list[str]) -> str:
    """Trả về trạng thái xấu nhất từ danh sách."""
    order = ["road_closed", "congested", "moderate", "stable", "unknown"]
    for s in order:
        if s in statuses:
            return s
    return "unknown"


def _format_snapshot(snap: RouteTrafficSnapshot, point: RouteSegmentPoint) -> dict:
    return {
        "snapshot_id": str(snap.id),
        "point_id": str(snap.point_id),
        "point_label": point.point_label,
        "lat": point.lat,
        "lon": point.lon,
        "captured_at": snap.captured_at.isoformat(),
        "current_speed": snap.current_speed,
        "free_flow_speed": snap.free_flow_speed,
        "current_travel_time": snap.current_travel_time,
        "free_flow_travel_time": snap.free_flow_travel_time,
        "delay_seconds": snap.delay_seconds,
        "congestion_percent": snap.congestion_percent,
        "confidence": snap.confidence,
        "road_closure": snap.road_closure,
        "status": snap.status,
        "frc": snap.frc,
    }


# ─── Public API ────────────────────────────────────────────────────────────────


def seed_route_points(session: Session) -> int:
    """Seed 2 điểm A và B nếu chưa tồn tại. Idempotent."""
    existing = session.exec(
        select(RouteSegmentPoint).where(
            RouteSegmentPoint.route_name == ROUTE_NAME
        )
    ).all()

    existing_labels = {p.point_label for p in existing}
    created = 0

    for point_data in ROUTE_POINTS_SEED:
        if point_data["point_label"] not in existing_labels:
            point = RouteSegmentPoint(**point_data)
            session.add(point)
            created += 1

    if created > 0:
        session.commit()
        print(f"[RouteMonitor] ✅ Seeded {created} route points")

    return created


async def collect_route_snapshots(session: Session) -> int:
    """
    Gọi TomTom Flow API cho tất cả điểm active trong tuyến.
    Lưu snapshot với đầy đủ timestamp (không truncate).
    """
    points = session.exec(
        select(RouteSegmentPoint).where(
            RouteSegmentPoint.route_name == ROUTE_NAME,
            RouteSegmentPoint.is_active == True,  # noqa: E712
        )
    ).all()

    if not points:
        print("[RouteMonitor] ⚠️  Không có điểm nào để thu thập. Chạy seed trước.")
        return 0

    async def _fetch_one(point: RouteSegmentPoint) -> RouteTrafficSnapshot | None:
        try:
            data = await get_flow_segment(point.lat, point.lon)
            flow = data.get("flowSegmentData", {})

            current_speed = flow.get("currentSpeed")
            free_flow_speed = flow.get("freeFlowSpeed")
            current_travel_time = flow.get("currentTravelTime")
            free_flow_travel_time = flow.get("freeFlowTravelTime")
            road_closure = bool(flow.get("roadClosure", False))

            delay_seconds = None
            if current_travel_time is not None and free_flow_travel_time is not None:
                delay_seconds = max(0, current_travel_time - free_flow_travel_time)

            return RouteTrafficSnapshot(
                point_id=point.id,
                current_speed=current_speed,
                free_flow_speed=free_flow_speed,
                current_travel_time=current_travel_time,
                free_flow_travel_time=free_flow_travel_time,
                delay_seconds=delay_seconds,
                congestion_percent=_calculate_congestion_percent(
                    current_speed, free_flow_speed
                ),
                confidence=flow.get("confidence"),
                road_closure=road_closure,
                status=_calculate_status(current_speed, free_flow_speed, road_closure),
                frc=flow.get("frc"),
            )
        except Exception as e:
            print(f"[RouteMonitor] ❌ Lỗi thu thập điểm {point.point_label}: {e}")
            return None

    # Gọi song song nhưng sleep nhỏ giữa các request để tránh spam
    tasks = []
    for i, point in enumerate(points):
        if i > 0:
            await asyncio.sleep(0.2)
        tasks.append(_fetch_one(point))

    snapshots = await asyncio.gather(*tasks)

    created = 0
    for snap in snapshots:
        if snap is not None:
            session.add(snap)
            created += 1

    session.commit()
    print(f"[RouteMonitor] ✅ Collected {created}/{len(points)} snapshots")
    return created


def get_latest_route_status(session: Session) -> dict:
    """
    Trả về trạng thái mới nhất của toàn tuyến.
    Bao gồm: thông tin từng điểm + tổng hợp trung bình.
    """
    points = session.exec(
        select(RouteSegmentPoint).where(
            RouteSegmentPoint.route_name == ROUTE_NAME,
            RouteSegmentPoint.is_active == True,  # noqa: E712
        )
    ).all()

    if not points:
        return {
            "route_name": ROUTE_NAME,
            "captured_at": None,
            "points": [],
            "average_speed": None,
            "average_congestion_percent": None,
            "overall_status": "unknown",
            "has_data": False,
        }

    # Subquery: latest snapshot per point
    latest_subq = (
        select(
            RouteTrafficSnapshot.point_id,
            func.max(RouteTrafficSnapshot.captured_at).label("latest_at"),
        )
        .group_by(RouteTrafficSnapshot.point_id)
        .subquery()
    )

    rows = session.exec(
        select(RouteTrafficSnapshot)
        .join(
            latest_subq,
            (RouteTrafficSnapshot.point_id == latest_subq.c.point_id)
            & (RouteTrafficSnapshot.captured_at == latest_subq.c.latest_at),
        )
    ).all()

    point_map = {p.id: p for p in points}
    snap_map = {s.point_id: s for s in rows}

    point_results = []
    speeds = []
    congestions = []
    statuses = []
    captured_ats = []

    for point in points:
        snap = snap_map.get(point.id)
        if snap:
            point_results.append(_format_snapshot(snap, point))
            if snap.current_speed is not None:
                speeds.append(snap.current_speed)
            if snap.congestion_percent is not None:
                congestions.append(snap.congestion_percent)
            statuses.append(snap.status)
            captured_ats.append(snap.captured_at)
        else:
            point_results.append(
                {
                    "point_id": str(point.id),
                    "point_label": point.point_label,
                    "lat": point.lat,
                    "lon": point.lon,
                    "captured_at": None,
                    "status": "unknown",
                    "current_speed": None,
                    "free_flow_speed": None,
                    "delay_seconds": None,
                    "congestion_percent": None,
                    "confidence": None,
                    "road_closure": False,
                    "frc": None,
                    "current_travel_time": None,
                    "free_flow_travel_time": None,
                }
            )

    return {
        "route_name": ROUTE_NAME,
        "captured_at": max(captured_ats).isoformat() if captured_ats else None,
        "points": point_results,
        "average_speed": round(sum(speeds) / len(speeds), 2) if speeds else None,
        "average_congestion_percent": (
            round(sum(congestions) / len(congestions), 2) if congestions else None
        ),
        "overall_status": _worst_status(statuses) if statuses else "unknown",
        "has_data": bool(rows),
    }


def get_route_history(
    session: Session,
    hours: int = 24,
    point_label: str | None = None,
) -> list[dict]:
    """
    Trả về lịch sử snapshots trong khoảng `hours` giờ gần nhất.
    Nếu point_label không None, chỉ trả về điểm đó.
    Giới hạn tối đa 2000 records.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=min(hours, 168))

    points = session.exec(
        select(RouteSegmentPoint).where(
            RouteSegmentPoint.route_name == ROUTE_NAME,
            RouteSegmentPoint.is_active == True,  # noqa: E712
        )
    ).all()

    point_map = {p.id: p for p in points}

    # Filter theo point_label nếu có
    point_ids_filter = None
    if point_label:
        filtered = [p for p in points if p.point_label == point_label.upper()]
        if filtered:
            point_ids_filter = [p.id for p in filtered]

    query = (
        select(RouteTrafficSnapshot)
        .where(RouteTrafficSnapshot.captured_at >= since)
        .order_by(RouteTrafficSnapshot.captured_at.asc())
        .limit(2000)
    )

    if point_ids_filter:
        query = query.where(RouteTrafficSnapshot.point_id.in_(point_ids_filter))
    else:
        all_ids = [p.id for p in points]
        if all_ids:
            query = query.where(RouteTrafficSnapshot.point_id.in_(all_ids))

    rows = session.exec(query).all()

    results = []
    for snap in rows:
        point = point_map.get(snap.point_id)
        if point:
            results.append(_format_snapshot(snap, point))

    return results


def get_snapshot_at_time(
    session: Session,
    target_dt: datetime,
    point_label: str | None = None,
) -> dict | None:
    """
    Tìm snapshot gần nhất với target_dt (trong khoảng ±30 phút).
    Trả về None nếu không tìm thấy.
    """
    window = timedelta(minutes=30)
    start = target_dt - window
    end = target_dt + window

    points = session.exec(
        select(RouteSegmentPoint).where(
            RouteSegmentPoint.route_name == ROUTE_NAME,
            RouteSegmentPoint.is_active == True,  # noqa: E712
        )
    ).all()

    point_map = {p.id: p for p in points}

    # Filter theo point_label nếu có
    point_ids = [p.id for p in points]
    if point_label:
        filtered = [p for p in points if p.point_label == point_label.upper()]
        if filtered:
            point_ids = [p.id for p in filtered]

    if not point_ids:
        return None

    rows = session.exec(
        select(RouteTrafficSnapshot)
        .where(
            RouteTrafficSnapshot.captured_at >= start,
            RouteTrafficSnapshot.captured_at <= end,
            RouteTrafficSnapshot.point_id.in_(point_ids),
        )
        .order_by(
            func.abs(
                func.extract("epoch", RouteTrafficSnapshot.captured_at)
                - func.extract("epoch", target_dt)
            )
        )
        .limit(len(point_ids))
    ).all()

    if not rows:
        return None

    point_results = []
    captured_ats = []

    for snap in rows:
        point = point_map.get(snap.point_id)
        if point:
            point_results.append(_format_snapshot(snap, point))
            captured_ats.append(snap.captured_at)

    speeds = [
        r["current_speed"] for r in point_results if r.get("current_speed") is not None
    ]
    congestions = [
        r["congestion_percent"]
        for r in point_results
        if r.get("congestion_percent") is not None
    ]
    statuses = [r["status"] for r in point_results]

    return {
        "route_name": ROUTE_NAME,
        "requested_at": target_dt.isoformat(),
        "actual_captured_at": (
            max(captured_ats).isoformat() if captured_ats else None
        ),
        "points": point_results,
        "average_speed": round(sum(speeds) / len(speeds), 2) if speeds else None,
        "average_congestion_percent": (
            round(sum(congestions) / len(congestions), 2) if congestions else None
        ),
        "overall_status": _worst_status(statuses) if statuses else "unknown",
        "has_data": True,
    }
