from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlmodel import Session

from src.core.config import get_settings
from src.storage.models.incident import Incident, IncidentEdge


ICON_CATEGORY_LABELS: dict[int, str] = {
    0: "Unknown",
    1: "Accident",
    2: "Fog",
    3: "DangerousConditions",
    4: "Rain",
    5: "Ice",
    6: "Jam",
    7: "LaneClosed",
    8: "RoadClosed",
    9: "RoadWorks",
    10: "Wind",
    11: "Flooding",
    14: "BrokenDownVehicle",
}


def _coords_to_linestring_wkt(coords: list[list[float]]) -> str:
    if not coords or len(coords) < 2:
        raise ValueError("LineString coordinates must have at least 2 points")
    parts: list[str] = []
    for p in coords:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        lon, lat = float(p[0]), float(p[1])
        parts.append(f"{lon} {lat}")
    if len(parts) < 2:
        raise ValueError("Invalid LineString coordinates")
    return f"LINESTRING({', '.join(parts)})"


def _coords_to_point_wkt(coord: list[float]) -> str:
    lon, lat = float(coord[0]), float(coord[1])
    return f"POINT({lon} {lat})"


def fetch_tomtom_incident_details(
    *,
    bbox: str,
    fields: str,
    language: str,
    time_validity_filter: str,
    t: str | None = None,
    category_filter: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    settings = get_settings()
    if not settings.tomtom_api_key:
        raise ValueError("Missing TOMTOM_API_KEY (set tomtom_api_key in BE/.env)")

    url = f"{settings.tomtom_base_url}/traffic/services/5/incidentDetails"
    params = {
        "key": settings.tomtom_api_key,
        "bbox": bbox,
        "fields": fields,
        "language": language,
        "timeValidityFilter": time_validity_filter,
    }
    if t:
        params["t"] = t
    if category_filter:
        params["categoryFilter"] = category_filter

    headers = {"Accept-Encoding": "gzip"}

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        traffic_model_id = resp.headers.get("TrafficModelID")
        data = resp.json()

    incidents = data.get("incidents") or []
    if not isinstance(incidents, list):
        incidents = []
    return incidents, traffic_model_id


def match_incident_geom_to_edges(
    session: Session,
    *,
    geom_wkt: str,
    buffer_m: float = 45.0,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """
    Return ranked candidate edges for a TomTom incident geometry.
    - dist_m: ST_Distance between incident geom and edge geom (meters)
    - overlap_m: length of overlap between buffered incident and edge (meters)
    """
    q = text(
        """
WITH inc AS (
  SELECT ST_GeomFromText(:wkt, 4326) AS g
)
SELECT
  e.id AS edge_id,
  ST_Distance(e.geom::geography, inc.g::geography) AS dist_m,
  ST_Length(
    ST_Intersection(
      ST_Buffer(inc.g::geography, :buffer_m)::geometry,
      e.geom
    )::geography
  ) AS overlap_m
FROM edges e, inc
WHERE e.geom IS NOT NULL
  AND ST_DWithin(e.geom::geography, inc.g::geography, :buffer_m)
ORDER BY overlap_m DESC NULLS LAST, dist_m ASC NULLS LAST
LIMIT :limit
"""
    )
    rows = session.execute(
        q, {"wkt": geom_wkt, "buffer_m": buffer_m, "limit": limit}
    ).mappings().all()
    return [dict(r) for r in rows]


def upsert_incident_and_edges(
    session: Session,
    *,
    tomtom_incident: dict[str, Any],
    traffic_model_id_t: str | None,
    bbox_used: str,
    buffer_m: float = 45.0,
    limit_edges: int = 8,
    fetched_at: datetime,
) -> Incident | None:
    props = tomtom_incident.get("properties") or {}
    geom = tomtom_incident.get("geometry") or {}
    tomtom_id = props.get("id")
    if not tomtom_id:
        return None

    geom_type = geom.get("type")
    coords = geom.get("coordinates")
    if geom_type == "LineString" and isinstance(coords, list):
        geom_wkt = _coords_to_linestring_wkt(coords)
    elif geom_type == "Point" and isinstance(coords, list):
        geom_wkt = _coords_to_point_wkt(coords)
    else:
        return None

    # Basic normalized fields
    icon_category = props.get("iconCategory")
    magnitude = props.get("magnitudeOfDelay")
    delay_seconds = props.get("delay")
    time_validity = props.get("timeValidity")
    start_time = props.get("startTime")
    end_time = props.get("endTime")
    events = props.get("events")

    # Parse ISO timestamps if present (keep robust: store raw JSON too)
    def _parse_iso(v: Any) -> datetime | None:
        if not v:
            return None
        try:
            # Python 3.11 supports fromisoformat but Z needs replace
            s = str(v).replace("Z", "+00:00")
            return datetime.fromisoformat(s)
        except Exception:
            return None

    inc = Incident(
        tomtom_incident_id=str(tomtom_id),
        fetched_at=fetched_at,
    )

    inc.traffic_model_id_t = traffic_model_id_t
    inc.bbox_used = bbox_used
    inc.icon_category = int(icon_category) if icon_category is not None else None
    inc.magnitude_of_delay = int(magnitude) if magnitude is not None else None
    inc.delay_seconds = int(delay_seconds) if delay_seconds is not None else None
    inc.time_validity = str(time_validity) if time_validity is not None else None
    inc.start_time = _parse_iso(start_time)
    inc.end_time = _parse_iso(end_time)
    inc.events_json = events
    inc.raw_properties_json = props

    # Store incident geometry
    session.add(inc)
    session.flush()  # ensure inc.id

    session.execute(
        text(
            "UPDATE incidents SET geom = ST_GeomFromText(:wkt, 4326) WHERE id = :id"
        ),
        {"wkt": geom_wkt, "id": str(inc.id)},
    )

    # Rebuild matches each fetch for this incident (simpler & deterministic)
    session.execute(
        text("DELETE FROM incident_edges WHERE incident_id = :id"),
        {"id": str(inc.id)},
    )

    candidates = match_incident_geom_to_edges(
        session, geom_wkt=geom_wkt, buffer_m=buffer_m, limit=limit_edges
    )

    for idx, c in enumerate(candidates, start=1):
        ie = IncidentEdge(
            incident_id=inc.id,
            edge_id=c["edge_id"],
            rank=idx,
            match_dist_m=float(c["dist_m"]) if c.get("dist_m") is not None else None,
            overlap_m=float(c["overlap_m"]) if c.get("overlap_m") is not None else None,
        )
        session.add(ie)

    return inc


def fetch_match_and_save_incidents(
    session: Session,
    *,
    buffer_m: float = 45.0,
    limit_edges: int = 8,
    category_filter: str | None = None,
    t: str | None = None,
) -> tuple[datetime, str | None, str, int, int]:
    settings = get_settings()
    bbox = settings.tomtom_incident_bbox
    language = settings.tomtom_incident_language
    time_validity = settings.tomtom_incident_time_validity_filter

    # A pragmatic fields set (enough for UI, still small-ish)
    fields = (
        "{incidents{type,geometry{type,coordinates},properties{"
        "id,iconCategory,magnitudeOfDelay,events{description,code,iconCategory},"
        "startTime,endTime,from,to,length,delay,roadNumbers,timeValidity,"
        "probabilityOfOccurrence,numberOfReports,lastReportTime"
        "}}}"
    )

    incidents, traffic_model_id = fetch_tomtom_incident_details(
        bbox=bbox,
        fields=fields,
        language=language,
        time_validity_filter=time_validity,
        t=t,
        category_filter=category_filter,
    )

    fetched_at = datetime.now(timezone.utc)
    total_received = len(incidents)
    total_saved = 0

    for it in incidents:
        inc = upsert_incident_and_edges(
            session,
            tomtom_incident=it,
            traffic_model_id_t=traffic_model_id,
            bbox_used=bbox,
            buffer_m=buffer_m,
            limit_edges=limit_edges,
            fetched_at=fetched_at,
        )
        if inc is not None:
            total_saved += 1

    # NOTE: Không xóa dữ liệu cũ — giữ lại để hỗ trợ xem lại lịch sử theo thời gian.
    # Dữ liệu được dọn dẹp theo tây sau (nếu cần) hoặc giữ tọi 30 ngày.
    session.commit()
    return fetched_at, traffic_model_id, bbox, total_received, total_saved


def list_recent_incidents_with_edges_geojson(
    session: Session,
    *,
    limit: int = 50,
    fetched_before: datetime | None = None,
    fetched_after: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Returns incidents + matched edges with GeoJSON geometry for FE.
    Có thể lọc theo khoảng thời gian fetch.
    Nếu không truyền khoảng thời gian, mặc định chỉ lấy mẻ (batch) sự cố mới nhất.
    """
    if not fetched_before and not fetched_after:
        latest_fetched = session.execute(
            text("SELECT MAX(fetched_at) FROM incidents")
        ).scalar()
        if latest_fetched:
            # Nhóm các incident thuộc cùng 1 phút với lần fetch mới nhất để lấy trọn vẹn cả batch
            fetched_after = latest_fetched.replace(second=0, microsecond=0)
            from datetime import timedelta as td
            fetched_before = fetched_after + td(minutes=1)

    where_clauses = []
    params: dict[str, Any] = {"limit": limit}

    if fetched_before:
        where_clauses.append("i.fetched_at <= :fetched_before")
        params["fetched_before"] = fetched_before
    if fetched_after:
        where_clauses.append("i.fetched_at >= :fetched_after")
        params["fetched_after"] = fetched_after

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""


    rows = session.execute(
        text(
            f"""
WITH inc AS (
  SELECT *
  FROM incidents i
  {where_sql}
  ORDER BY i.fetched_at DESC
  LIMIT :limit
),
edges_geo AS (
  SELECT
    ie.incident_id,
    ie.edge_id,
    ie.rank,
    ie.match_dist_m,
    ie.overlap_m,
    ST_AsGeoJSON(e.geom)::jsonb AS edge_geom
  FROM incident_edges ie
  JOIN edges e ON e.id = ie.edge_id
  WHERE ie.incident_id IN (SELECT id FROM inc)
)
SELECT
  i.id,
  i.tomtom_incident_id,
  i.fetched_at,
  i.icon_category,
  i.magnitude_of_delay,
  i.delay_seconds,
  i.time_validity,
  i.start_time,
  i.end_time,
  i.events_json,
  i.raw_properties_json,
  ST_AsGeoJSON(i.geom)::jsonb AS incident_geom,
  COALESCE(
    jsonb_agg(
      jsonb_build_object(
        'edge_id', eg.edge_id,
        'rank', eg.rank,
        'match_dist_m', eg.match_dist_m,
        'overlap_m', eg.overlap_m,
        'geometry', eg.edge_geom
      )
      ORDER BY eg.rank NULLS LAST
    ) FILTER (WHERE eg.edge_id IS NOT NULL),
    '[]'::jsonb
  ) AS matched_edges
FROM inc i
LEFT JOIN edges_geo eg ON eg.incident_id = i.id
GROUP BY
  i.id, i.tomtom_incident_id, i.fetched_at, i.icon_category, i.magnitude_of_delay,
  i.delay_seconds, i.time_validity, i.start_time, i.end_time, i.events_json, i.raw_properties_json, i.geom
ORDER BY i.fetched_at DESC
"""
        ),
        params,
    ).mappings().all()

    out: list[dict[str, Any]] = []
    for r in rows:
        icon_cat = r.get("icon_category")
        icon_label = ICON_CATEGORY_LABELS.get(int(icon_cat)) if icon_cat is not None else None
        matched_edges = r.get("matched_edges")
        if isinstance(matched_edges, str):
            matched_edges = json.loads(matched_edges)
        # Đảm bảo datetime có timezone UTC để Frontend hiển thị đúng (giờ VN = UTC+7)
        fetched_at = r["fetched_at"]
        if fetched_at and fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)

        start_time = r.get("start_time")
        if start_time and start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        end_time = r.get("end_time")
        if end_time and end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        out.append(
            {
                "id": r["id"],
                "tomtom_incident_id": r["tomtom_incident_id"],
                "fetched_at": fetched_at,
                "icon_category": icon_cat,
                "icon_category_label": icon_label,
                "magnitude_of_delay": r.get("magnitude_of_delay"),
                "delay_seconds": r.get("delay_seconds"),
                "time_validity": r.get("time_validity"),
                "start_time": start_time,
                "end_time": end_time,
                "events": r.get("events_json"),
                "raw_properties": r.get("raw_properties_json"),
                "geometry": r.get("incident_geom"),
                "matched_edges": matched_edges or [],
            }
        )
    return out


def get_incident_fetch_sessions(
    session: Session,
    *,
    hours: int = 24,
) -> list[dict[str, Any]]:
    """
    Trả về danh sách các thời điểm fetch trong N giờ gần nhất.
    Mỗi entry là 1 'session' fetch (nhom theo phút).
    """
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows = session.execute(
        text("""
            SELECT
                DATE_TRUNC('minute', fetched_at) AS session_time,
                COUNT(*) AS incident_count
            FROM incidents
            WHERE fetched_at >= :since
            GROUP BY DATE_TRUNC('minute', fetched_at)
            ORDER BY session_time DESC
            LIMIT 500
        """),
        {"since": since},
    ).mappings().all()

    out = []
    for r in rows:
        t = r["session_time"]
        if t and t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        out.append({"session_time": t, "incident_count": r["incident_count"]})
    return out


def get_incidents_near_time(
    session: Session,
    *,
    target_dt: datetime,
    window_minutes: int = 15,
    limit: int = 100,
) -> tuple[datetime | None, list[dict[str, Any]]]:
    """
    Tìm lô incidents được fetch gần nhất với target_dt (trong khoảng ±window_minutes).
    Trả về (actual_fetched_at, incidents_list).
    """
    from datetime import timedelta
    start = target_dt - timedelta(minutes=window_minutes)
    end = target_dt + timedelta(minutes=window_minutes)

    # Tìm fetched_at gần nhất
    row = session.execute(
        text("""
            SELECT fetched_at
            FROM incidents
            WHERE fetched_at BETWEEN :start AND :end
            ORDER BY ABS(EXTRACT(EPOCH FROM (fetched_at - :target))) ASC
            LIMIT 1
        """),
        {"start": start, "end": end, "target": target_dt},
    ).mappings().first()

    if not row:
        return None, []

    actual_fetched_at = row["fetched_at"]
    if actual_fetched_at and actual_fetched_at.tzinfo is None:
        actual_fetched_at = actual_fetched_at.replace(tzinfo=timezone.utc)

    # Lấy incidents có fetched_at trong cùng phút
    minute_start = actual_fetched_at.replace(second=0, microsecond=0)
    from datetime import timedelta as td
    minute_end = minute_start + td(minutes=1)

    incidents = list_recent_incidents_with_edges_geojson(
        session,
        limit=limit,
        fetched_after=minute_start,
        fetched_before=minute_end,
    )
    return actual_fetched_at, incidents

