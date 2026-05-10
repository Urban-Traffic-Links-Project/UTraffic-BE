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

    # Find existing
    existing = (
        session.execute(
            text("SELECT id FROM incidents WHERE tomtom_incident_id = :tid LIMIT 1"),
            {"tid": tomtom_id},
        )
        .mappings()
        .first()
    )

    now = datetime.now(timezone.utc)

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

    inc = None
    if existing and existing.get("id"):
        inc = session.get(Incident, existing["id"])

    if not inc:
        inc = Incident(
            tomtom_incident_id=str(tomtom_id),
        )

    inc.fetched_at = now
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
        )
        if inc is not None:
            total_saved += 1

    session.commit()
    return fetched_at, traffic_model_id, bbox, total_received, total_saved


def list_recent_incidents_with_edges_geojson(
    session: Session,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Returns incidents + matched edges with GeoJSON geometry for FE.
    """
    rows = session.execute(
        text(
            """
WITH inc AS (
  SELECT *
  FROM incidents
  ORDER BY fetched_at DESC
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
        {"limit": limit},
    ).mappings().all()

    out: list[dict[str, Any]] = []
    for r in rows:
        icon_cat = r.get("icon_category")
        icon_label = ICON_CATEGORY_LABELS.get(int(icon_cat)) if icon_cat is not None else None
        matched_edges = r.get("matched_edges")
        if isinstance(matched_edges, str):
            matched_edges = json.loads(matched_edges)
        out.append(
            {
                "id": r["id"],
                "tomtom_incident_id": r["tomtom_incident_id"],
                "fetched_at": r["fetched_at"],
                "icon_category": icon_cat,
                "icon_category_label": icon_label,
                "magnitude_of_delay": r.get("magnitude_of_delay"),
                "delay_seconds": r.get("delay_seconds"),
                "time_validity": r.get("time_validity"),
                "start_time": r.get("start_time"),
                "end_time": r.get("end_time"),
                "events": r.get("events_json"),
                "raw_properties": r.get("raw_properties_json"),
                "geometry": r.get("incident_geom"),
                "matched_edges": matched_edges or [],
            }
        )
    return out
