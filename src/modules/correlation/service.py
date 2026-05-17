"""src/modules/correlation/service.py"""
import math
from fastapi import HTTPException, status
from sqlalchemy import text
from sqlmodel import Session, select

from src.storage.models.graph import Node
from src.storage.models.correlation import CorrelationSnapshot


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def list_snapshots(session: Session) -> dict:
    """
    Trả về danh sách tất cả snapshots, gom nhóm theo (method, date, slot).
    mode format: "YYYY-MM-DD_Slot_HHMM"
    """
    rows = session.execute(
        text("""
            SELECT id, method, mode, mean_corr, std_corr, is_active, computed_at
            FROM correlation_snapshots
            ORDER BY method, mode
        """)
    ).fetchall()

    snapshots = []
    dates_set = set()
    slots_set = set()

    for r in rows:
        mode = r[2]  # "2024-08-27_Slot_0900"
        parts = mode.split("_", 1) if "_" in mode else [None, mode]
        date_str = parts[0]   # "2024-08-27"
        slot_str = parts[1]   # "Slot_0900"

        dates_set.add(date_str)
        slots_set.add(slot_str)

        snapshots.append({
            "snapshot_id":  str(r[0]),
            "method":       r[1],
            "mode":         mode,
            "date":         date_str,
            "slot":         slot_str,
            "mean_corr":    round(r[3], 4) if r[3] is not None else None,
            "is_active":    r[5],
        })

    return {
        "snapshots": snapshots,
        "dates":     sorted(dates_set),
        "slots":     sorted(slots_set),
        "total":     len(snapshots),
    }


def get_node_correlations(
    session: Session,
    node_index: int,                    # osm_node_id
    max_dist_m: float | None = None,
    min_corr: float | None = None,
    snapshot_mode: str | None = None,   # "2024-08-27_Slot_0900" | None → active
) -> dict:
    """
    Tìm tương quan của 1 node.
    - snapshot_mode=None  → dùng active snapshot
    - snapshot_mode="2024-08-27_Slot_0900" → lookup đúng snapshot đó
    """
    # 1. Tìm node theo osm_node_id
    node_a = session.exec(
        select(Node).where(Node.osm_node_id == node_index)
    ).first()

    if not node_a:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node với osm_node_id={node_index} không tồn tại trong DB",
        )

    # 2. Chọn snapshot
    if snapshot_mode:
        snapshot = session.exec(
            select(CorrelationSnapshot).where(CorrelationSnapshot.mode == snapshot_mode)
        ).first()
        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Snapshot với mode='{snapshot_mode}' không tồn tại. "
                       "Dùng GET /api/v1/correlation/snapshots để xem danh sách.",
            )
    else:
        snapshot = session.exec(
            select(CorrelationSnapshot).where(CorrelationSnapshot.is_active == True)
        ).first()
        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chưa có active correlation snapshot. Chạy seed_correlation.py trước.",
            )

    # 3. Query node_correlations + JOIN nodes
    rows = session.execute(
        text("""
            SELECT
                nc.correlation_value,
                nc.rank_from_a,
                nc.is_adjacent,
                n.id            AS node_b_uuid,
                n.osm_node_id   AS node_b_osm,
                n.node_index    AS node_b_index,
                n.lat           AS node_b_lat,
                n.lon           AS node_b_lon,
                n.street_name   AS node_b_street
            FROM node_correlations nc
            JOIN nodes n ON n.id = nc.node_b_id
            WHERE nc.node_a_id  = :node_a_id
              AND nc.snapshot_id = :snapshot_id
            ORDER BY nc.rank_from_a ASC
        """),
        {"node_a_id": str(node_a.id), "snapshot_id": str(snapshot.id)},
    ).fetchall()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Không có correlation data cho node osm_node_id={node_index} "
                f"trong snapshot '{snapshot.mode}'."
            ),
        )

    # 4. Build neighbors
    neighbors = []
    for r in rows:
        dist_m = _haversine_m(node_a.lat, node_a.lon, r.node_b_lat, r.node_b_lon)

        if max_dist_m is not None and dist_m > max_dist_m:
            continue
        if min_corr is not None and abs(r.correlation_value) < min_corr:
            continue

        neighbors.append({
            "node_id":     str(r.node_b_uuid),
            "osm_node_id": r.node_b_osm,
            "node_index":  r.node_b_index,
            "lat":         r.node_b_lat,
            "lon":         r.node_b_lon,
            "street_name": r.node_b_street,
            "corr":        round(r.correlation_value, 6),
            "rank":        r.rank_from_a,
            "dist_m":      round(dist_m, 1),
            "is_adjacent": r.is_adjacent,
        })

    # Parse date/slot từ mode
    mode_parts = snapshot.mode.split("_", 1) if "_" in snapshot.mode else [None, snapshot.mode]

    return {
        "snapshot_id":   str(snapshot.id),
        "snapshot_mode": snapshot.mode,
        "snapshot_date": mode_parts[0],
        "snapshot_slot": mode_parts[1],
        "selected_node": {
            "node_id":     str(node_a.id),
            "osm_node_id": node_a.osm_node_id,
            "node_index":  node_a.node_index,
            "lat":         node_a.lat,
            "lon":         node_a.lon,
            "street_name": node_a.street_name,
        },
        "neighbors": neighbors,
        "total":     len(neighbors),
    }