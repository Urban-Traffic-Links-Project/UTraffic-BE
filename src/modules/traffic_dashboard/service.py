import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select
from sqlalchemy import func, desc

from src.integrations.tomtom import get_flow_segment, get_incidents_district_1
from src.storage.models.graph import Node
from src.storage.models.traffic_dashboard import (
    TrafficMonitoredSegment,
    TrafficSnapshot,
)


def calculate_status(current_speed: float | None, free_flow_speed: float | None, road_closure: bool) -> str:
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


def calculate_congestion_percent(current_speed: float | None, free_flow_speed: float | None) -> float | None:
    if current_speed is None or free_flow_speed is None or free_flow_speed <= 0:
        return None

    return max(0, min(100, round((1 - current_speed / free_flow_speed) * 100, 2)))


def seed_monitored_segments_from_nodes(session: Session, limit: int = 50) -> int:
    existing = session.exec(select(TrafficMonitoredSegment)).first()
    if existing:
        return 0

    nodes = session.exec(select(Node).order_by(Node.node_index).limit(limit)).all()

    created = 0
    for node in nodes:
        segment = TrafficMonitoredSegment(
            node_id=node.id,
            road_name=node.street_name,
            lat=node.lat,
            lon=node.lon,
            is_active=True,
        )
        session.add(segment)
        created += 1

    session.commit()
    return created


async def collect_traffic_snapshots(session: Session) -> int:
    segments = session.exec(
        select(TrafficMonitoredSegment).where(TrafficMonitoredSegment.is_active == True)
    ).all()

    created = 0

    for segment in segments:
        try:
            data = await get_flow_segment(segment.lat, segment.lon)
            flow = data.get("flowSegmentData", {})

            current_speed = flow.get("currentSpeed")
            free_flow_speed = flow.get("freeFlowSpeed")
            current_travel_time = flow.get("currentTravelTime")
            free_flow_travel_time = flow.get("freeFlowTravelTime")
            road_closure = bool(flow.get("roadClosure", False))

            delay_seconds = None
            if current_travel_time is not None and free_flow_travel_time is not None:
                delay_seconds = max(0, current_travel_time - free_flow_travel_time)

            snapshot = TrafficSnapshot(
                monitored_segment_id=segment.id,
                current_speed=current_speed,
                free_flow_speed=free_flow_speed,
                current_travel_time=current_travel_time,
                free_flow_travel_time=free_flow_travel_time,
                delay_seconds=delay_seconds,
                congestion_percent=calculate_congestion_percent(current_speed, free_flow_speed),
                confidence=flow.get("confidence"),
                road_closure=road_closure,
                status=calculate_status(current_speed, free_flow_speed, road_closure),
                frc=flow.get("frc"),
            )

            session.add(snapshot)
            created += 1

            # Tránh spam TomTom quá nhanh
            await asyncio.sleep(0.1)

        except Exception as error:
            print(f"[TrafficCollector] Failed segment {segment.id}: {error}")

    session.commit()
    return created


def get_latest_snapshot_subquery(session: Session):
    latest_time_subq = (
        select(
            TrafficSnapshot.monitored_segment_id,
            func.max(TrafficSnapshot.captured_at).label("latest_at"),
        )
        .group_by(TrafficSnapshot.monitored_segment_id)
        .subquery()
    )

    return latest_time_subq


def get_dashboard_overview(session: Session) -> dict:
    latest_time_subq = get_latest_snapshot_subquery(session)

    rows = session.exec(
        select(TrafficSnapshot)
        .join(
            latest_time_subq,
            (TrafficSnapshot.monitored_segment_id == latest_time_subq.c.monitored_segment_id)
            & (TrafficSnapshot.captured_at == latest_time_subq.c.latest_at),
        )
    ).all()

    if not rows:
        return {
            "average_speed": None,
            "average_delay_seconds": None,
            "congested_segments": 0,
            "moderate_segments": 0,
            "stable_segments": 0,
            "road_closures": 0,
            "monitored_segments": 0,
            "last_updated_at": None,
        }

    speeds = [r.current_speed for r in rows if r.current_speed is not None]
    delays = [r.delay_seconds for r in rows if r.delay_seconds is not None]

    return {
        "average_speed": round(sum(speeds) / len(speeds), 2) if speeds else None,
        "average_delay_seconds": round(sum(delays) / len(delays), 2) if delays else None,
        "congested_segments": sum(1 for r in rows if r.status == "congested"),
        "moderate_segments": sum(1 for r in rows if r.status == "moderate"),
        "stable_segments": sum(1 for r in rows if r.status == "stable"),
        "road_closures": sum(1 for r in rows if r.road_closure),
        "monitored_segments": len(rows),
        "last_updated_at": max(r.captured_at for r in rows),
    }


def get_top_congested(session: Session, limit: int = 10) -> list[dict]:
    latest_time_subq = get_latest_snapshot_subquery(session)

    rows = session.exec(
        select(TrafficSnapshot, TrafficMonitoredSegment)
        .join(TrafficMonitoredSegment, TrafficSnapshot.monitored_segment_id == TrafficMonitoredSegment.id)
        .join(
            latest_time_subq,
            (TrafficSnapshot.monitored_segment_id == latest_time_subq.c.monitored_segment_id)
            & (TrafficSnapshot.captured_at == latest_time_subq.c.latest_at),
        )
        .order_by(desc(TrafficSnapshot.congestion_percent))
        .limit(limit)
    ).all()

    return [
        {
            "segment_id": str(segment.id),
            "road_name": segment.road_name,
            "lat": segment.lat,
            "lon": segment.lon,
            "current_speed": snapshot.current_speed,
            "free_flow_speed": snapshot.free_flow_speed,
            "delay_seconds": snapshot.delay_seconds,
            "congestion_percent": snapshot.congestion_percent,
            "status": snapshot.status,
            "captured_at": snapshot.captured_at,
        }
        for snapshot, segment in rows
    ]


def get_history(session: Session, hours: int = 24) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows = session.exec(
        select(
            func.date_trunc("hour", TrafficSnapshot.captured_at).label("bucket"),
            func.avg(TrafficSnapshot.current_speed).label("average_speed"),
            func.avg(TrafficSnapshot.delay_seconds).label("average_delay_seconds"),
            func.count().filter(TrafficSnapshot.status == "congested").label("congested_segments"),
        )
        .where(TrafficSnapshot.captured_at >= since)
        .group_by("bucket")
        .order_by("bucket")
    ).all()

    return [
        {
            "bucket": row.bucket,
            "average_speed": round(float(row.average_speed), 2) if row.average_speed is not None else None,
            "average_delay_seconds": round(float(row.average_delay_seconds), 2) if row.average_delay_seconds is not None else None,
            "congested_segments": int(row.congested_segments or 0),
        }
        for row in rows
    ]


async def get_incidents() -> list[dict]:
    data = await get_incidents_district_1()
    incidents = data.get("incidents", [])

    result = []

    for item in incidents:
        if not item:
            continue

        props = item.get("properties", {}) or {}
        events = props.get("events") or []
        first_event = events[0] if events else {}

        road_name = props.get("from") or props.get("to")

        if not road_name:
            road_numbers = props.get("roadNumbers") or []
            road_name = ", ".join(road_numbers) if road_numbers else None

        result.append({
            "incident_type": str(first_event.get("code") or props.get("iconCategory") or ""),
            "road_name": road_name,
            "description": first_event.get("description"),
            "delay_seconds": props.get("delay"),
            "length_m": props.get("length"),
            "magnitude": props.get("magnitudeOfDelay"),
        })

    return result