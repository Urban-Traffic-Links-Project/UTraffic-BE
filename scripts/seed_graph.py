"""
scripts/seed_graph.py
=====================
Nạp 1980 intersection nodes (ngã ba/ngã tư thật) và edges vào bảng
`nodes` + `edges` trong PostgreSQL.

Nguồn dữ liệu (theo thứ tự ưu tiên):
  ┌─────────────────────────────────────────────────────────────────┐
  │  Trường          │  Nguồn                                       │
  ├─────────────────────────────────────────────────────────────────┤
  │  osm_node_id     │  gs['osm_node_ids']  (1980 matched nodes)    │
  │  lat, lon        │  og['coordinates']   (tọa độ thật từ OSM)    │
  │  degree (thật)   │  graphml G.degree()  (in+out, không norm)    │
  │  betweenness     │  og['node_features'][:, 3]  (đã norm)        │
  │  street_name     │  graphml edge attrs 'name'  (phổ biến nhất)  │
  │  edges           │  og['edge_index'] filter cả 2 đầu ∈ gs_set  │
  └─────────────────────────────────────────────────────────────────┘

Tại sao KHÔNG dùng graph_structure cho nodes?
  - graph_structure['osm_node_ids'] = 1980 intersection nodes ✓ (cùng tập)
  - NHƯNG node_features['degree'] là giá trị normalized [0,1] → hiển thị sai
  - graph_structure model_node_mid_lat/lon là midpoint đoạn đường → tọa độ sai

Chạy:
  cd BE/
  uv run python scripts/seed_graph.py \
    --graph-structure ml_workspace/data/graph_structure_20260427_152321.npz \
    --osm-graph       ml_workspace/data/osm_graph_20260427_152259.npz \
    --graphml         ml_workspace/data/osm_10_7600_106_7150_10_8050_106_6750.graphml \
    --db-url          postgresql://user:pass@localhost:5432/utraffic

Script idempotent — an toàn để chạy lại (ON CONFLICT DO UPDATE).
Chạy script này TRƯỚC seed_correlation.py.
"""

import argparse
import logging
import math
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.core.config import get_settings

# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 1: Load dữ liệu
# ═════════════════════════════════════════════════════════════════════════════

def load_sources(gs_path: str, og_path: str, graphml_path: str) -> dict:
    """
    Load và validate 3 nguồn dữ liệu. Trả về dict hợp nhất.

    Đầu ra:
      nodes: list[dict] — 1980 intersection nodes đầy đủ thông tin
      edges: list[dict] — các directed edges giữa các nodes trong tập 1980
    """
    for p in [gs_path, og_path, graphml_path]:
        if not Path(p).exists():
            raise FileNotFoundError(f"Không tìm thấy file: {p}")

    # ── Load NPZ ──────────────────────────────────────────────────────────────
    log.info("📦 Loading graph_structure.npz ...")
    gs = np.load(gs_path, allow_pickle=True)
    gs_node_ids = gs["osm_node_ids"]              # (1980,) osm IDs đã map-match
    log.info(f"  ✓ {len(gs_node_ids)} matched intersection nodes")

    log.info("📦 Loading osm_graph.npz ...")
    og = np.load(og_path, allow_pickle=True)
    og_node_ids     = og["osm_node_ids"]          # (2692,)
    og_coordinates  = og["coordinates"]           # (2692, 2) — [lat, lon]
    og_node_feats   = og["node_features"]         # (2692, 4) — lat_n, lon_n, deg_n, btwn_n
    og_edge_index   = og["edge_index"]            # (2, 5878)
    og_edge_lengths = og["edge_lengths"]          # (5878,)
    og_edge_maxspeed = og["edge_maxspeed"]        # (5878,)
    og_edge_lanes   = og["edge_lanes"]            # (5878,)
    og_edge_highway = og["edge_highway_type"]     # (5878,)
    log.info(f"  ✓ {len(og_node_ids)} OSM nodes, {og_edge_index.shape[1]} directed edges")

    # ── Build fast lookups ────────────────────────────────────────────────────
    og_lookup   = {int(nid): i for i, nid in enumerate(og_node_ids)}
    gs_set      = {int(nid) for nid in gs_node_ids}

    # ── Load graphml (degree thật + street name) ──────────────────────────────
    log.info("📦 Loading graphml (networkx) ...")
    G = nx.read_graphml(graphml_path)
    log.info(f"  ✓ {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # ── Tổng hợp thông tin cho 1980 nodes ────────────────────────────────────
    log.info("🔄 Aggregating node attributes ...")

    # street_name: tên đường xuất hiện nhiều nhất trong các edge kề với node
    #
    # OSMnx encode đường có nhiều tên thành string dạng "['Tên A', 'Tên B']"
    # thay vì list thật. Cần parse ra trước khi đếm.
    import ast

    def _parse_osm_name(raw: str) -> list[str]:
        """Parse OSMnx name field — có thể là string đơn hoặc list-as-string."""
        raw = raw.strip()
        if raw.startswith("["):
            try:
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, list):
                    return [s.strip() for s in parsed if isinstance(s, str) and s.strip()]
            except (ValueError, SyntaxError):
                pass
        return [raw] if raw else []

    node_street: dict[int, list[str]] = defaultdict(list)
    for u, v, attrs in G.edges(data=True):
        name = attrs.get("name", None)
        if name and isinstance(name, str):
            for parsed_name in _parse_osm_name(name):
                try:
                    node_street[int(u)].append(parsed_name)
                    node_street[int(v)].append(parsed_name)
                except (ValueError, TypeError):
                    pass

    nodes: list[dict] = []
    for node_index, osm_id_raw in enumerate(gs_node_ids):
        osm_id = int(osm_id_raw)
        nstr   = str(osm_id)

        # Tọa độ thật từ osm_graph (không dùng midpoint của graph_structure)
        og_idx = og_lookup[osm_id]                    # chắc chắn tồn tại (đã validate)
        lat    = float(og_coordinates[og_idx, 0])
        lon    = float(og_coordinates[og_idx, 1])

        # Degree THẬT (số cạnh in+out) từ graphml — không normalized
        G_undirected = G.to_undirected()
        degree_real = int(G_undirected.degree(nstr)) if nstr in G_undirected else 0

        # Betweenness normalized từ osm_graph (đã tính sẵn)
        betweenness_norm = float(og_node_feats[og_idx, 3])

        # lat_norm, lon_norm từ osm_graph
        lat_norm = float(og_node_feats[og_idx, 0])
        lon_norm = float(og_node_feats[og_idx, 1])

        # Street name (tên phổ biến nhất trong các edges kề)
        names = node_street.get(osm_id, [])
        street_name = Counter(names).most_common(1)[0][0] if names else None

        nodes.append({
            "node_index":       node_index,          # 0..1979 — index trong tập 1980
            "osm_node_id":      osm_id,
            "lat":              lat,
            "lon":              lon,
            "degree":           degree_real,          # INT thật: 1..10
            "betweenness_norm": betweenness_norm,
            "lat_norm":         lat_norm,
            "lon_norm":         lon_norm,
            "street_name":      street_name,
        })

    log.info(
        f"  ✓ {len(nodes)} nodes built | "
        f"degree: min={min(n['degree'] for n in nodes)}, "
        f"max={max(n['degree'] for n in nodes)}, "
        f"mean={sum(n['degree'] for n in nodes)/len(nodes):.2f}"
    )

    # ── Tổng hợp edges (chỉ lấy edges mà cả 2 endpoint ∈ gs_set) ────────────
    log.info("🔄 Filtering edges (both endpoints in 1980-node set) ...")

    # Lookup tên đường cho edge từ graphml
    graphml_edge_name: dict[tuple[int, int], str | None] = {}
    for u, v, attrs in G.edges(data=True):
        try:
            graphml_edge_name[(int(u), int(v))] = attrs.get("name", None)
        except (ValueError, TypeError):
            pass

    edges: list[dict] = []
    for i, (src_idx, tgt_idx) in enumerate(zip(og_edge_index[0], og_edge_index[1])):
        src_osm_id = int(og_node_ids[src_idx])
        tgt_osm_id = int(og_node_ids[tgt_idx])

        # Chỉ giữ edge nếu cả 2 endpoint thuộc tập 1980 matched nodes
        if src_osm_id not in gs_set or tgt_osm_id not in gs_set:
            continue

        edges.append({
            "source_osm_id":  src_osm_id,
            "target_osm_id":  tgt_osm_id,
            "source_idx":     int(np.where(gs_node_ids == src_osm_id)[0][0]),
            "target_idx":     int(np.where(gs_node_ids == tgt_osm_id)[0][0]),
            "length_m":       float(og_edge_lengths[i]) if og_edge_lengths[i] > 0 else None,
            "maxspeed_kmh":   float(og_edge_maxspeed[i]) if og_edge_maxspeed[i] > 0 else None,
            "lanes":          float(og_edge_lanes[i]) if og_edge_lanes[i] > 0 else None,
            "highway_type":   int(og_edge_highway[i]),
        })

    log.info(
        f"  ✓ {len(edges)} edges retained "
        f"({og_edge_index.shape[1] - len(edges)} filtered out — endpoint outside gs_set)"
    )

    return {"nodes": nodes, "edges": edges, "gs_node_ids": gs_node_ids}


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 2: Upsert GraphSnapshot
# ═════════════════════════════════════════════════════════════════════════════

def upsert_graph_snapshot(session: Session, n_nodes: int, n_edges: int) -> uuid.UUID:
    """
    Tạo hoặc cập nhật graph_snapshot is_active=True.
    Deactivate tất cả snapshot cũ trước.
    """
    session.execute(
        text("UPDATE graph_snapshots SET is_active = FALSE WHERE is_active = TRUE")
    )
    snapshot_id = uuid.uuid4()
    session.execute(
        text("""
            INSERT INTO graph_snapshots
              (id, version, num_nodes, num_edges, is_active, created_at)
            VALUES
              (:id, :version, :num_nodes, :num_edges, TRUE, :created_at)
        """),
        {
            "id":         str(snapshot_id),
            "version":    "v1.0_intersection_nodes",
            "num_nodes":  n_nodes,
            "num_edges":  n_edges,
            "created_at": datetime.now(timezone.utc),
        },
    )
    session.flush()
    log.info(f"  ✓ GraphSnapshot {snapshot_id} created")
    return snapshot_id


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 3: Upsert Nodes → trả về osm_id → UUID map
# ═════════════════════════════════════════════════════════════════════════════

def upsert_nodes(session: Session, nodes: list[dict]) -> dict[int, uuid.UUID]:
    """
    Upsert 1980 intersection nodes vào bảng `nodes`.
    Trả về mapping osm_node_id → DB UUID (dùng cho bước seed edges).
    """
    log.info(f"💾 Upserting {len(nodes)} nodes ...")
    now = datetime.now(timezone.utc)
    osm_to_uuid: dict[int, uuid.UUID] = {}

    for n in nodes:
        new_id = str(uuid.uuid4())
        row = session.execute(
            text("""
                INSERT INTO nodes
                  (id, osm_node_id, node_index, lat, lon,
                   degree, betweenness_norm, lat_norm, lon_norm,
                   street_name, created_at)
                VALUES
                  (:id, :osm_node_id, :node_index, :lat, :lon,
                   :degree, :betweenness_norm, :lat_norm, :lon_norm,
                   :street_name, :created_at)
                ON CONFLICT (osm_node_id) DO UPDATE SET
                  node_index       = EXCLUDED.node_index,
                  lat              = EXCLUDED.lat,
                  lon              = EXCLUDED.lon,
                  degree           = EXCLUDED.degree,
                  betweenness_norm = EXCLUDED.betweenness_norm,
                  lat_norm         = EXCLUDED.lat_norm,
                  lon_norm         = EXCLUDED.lon_norm,
                  street_name      = EXCLUDED.street_name
                RETURNING id, osm_node_id
            """),
            {
                "id":               new_id,
                "osm_node_id":      n["osm_node_id"],
                "node_index":       n["node_index"],
                "lat":              n["lat"],
                "lon":              n["lon"],
                "degree":           n["degree"],
                "betweenness_norm": n["betweenness_norm"],
                "lat_norm":         n["lat_norm"],
                "lon_norm":         n["lon_norm"],
                "street_name":      n["street_name"],
                "created_at":       now,
            },
        ).fetchone()
        osm_to_uuid[row[1]] = uuid.UUID(str(row[0]))

    session.flush()
    log.info(f"  ✓ {len(osm_to_uuid)} nodes upserted")
    return osm_to_uuid

settings = get_settings()
# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 4: Upsert Edges
# ═════════════════════════════════════════════════════════════════════════════

def upsert_edges(
    session: Session,
    edges: list[dict],
    osm_to_uuid: dict[int, uuid.UUID],
    batch_size: int = 500,
) -> None:
    """
    Upsert edges vào bảng `edges`.
    Bỏ qua edges có endpoint không có UUID (không nên xảy ra).
    Dùng batch để tránh statement quá lớn.
    """
    log.info(f"💾 Upserting {len(edges)} edges ...")
    now = datetime.now(timezone.utc)
    skipped = 0
    inserted = 0

    for batch_start in range(0, len(edges), batch_size):
        batch = edges[batch_start: batch_start + batch_size]
        for e in batch:
            src_uuid = osm_to_uuid.get(e["source_osm_id"])
            tgt_uuid = osm_to_uuid.get(e["target_osm_id"])
            if src_uuid is None or tgt_uuid is None:
                skipped += 1
                continue

            session.execute(
                text("""
                    INSERT INTO edges
                      (id, source_node_id, target_node_id,
                       source_index, target_index,
                       length_m, maxspeed_kmh, lanes, highway_type,
                       created_at)
                    VALUES
                      (:id, :src_id, :tgt_id,
                       :src_idx, :tgt_idx,
                       :length_m, :maxspeed_kmh, :lanes, :highway_type,
                       :created_at)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "id":           str(uuid.uuid4()),
                    "src_id":       str(src_uuid),
                    "tgt_id":       str(tgt_uuid),
                    "src_idx":      e["source_idx"],
                    "tgt_idx":      e["target_idx"],
                    "length_m":     e["length_m"],
                    "maxspeed_kmh": e["maxspeed_kmh"],
                    "lanes":        e["lanes"],
                    "highway_type": e["highway_type"],
                    "created_at":   now,
                },
            )
            inserted += 1

        session.flush()
        log.info(f"  → batch {batch_start + len(batch)} / {len(edges)}")

    session.commit()
    log.info(f"  ✓ {inserted} edges inserted, {skipped} skipped (missing node UUID)")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main(args: argparse.Namespace) -> None:
    log.info("=" * 65)
    log.info("🚀 seed_graph.py — Intersection Nodes & Edges Seeder")
    log.info("=" * 65)

    # Bước 1: Load & aggregate
    data = load_sources(args.graph_structure, args.osm_graph, args.graphml)
    nodes  = data["nodes"]
    edges  = data["edges"]

    log.info(f"📊 Summary trước khi insert:")
    log.info(f"   Nodes  : {len(nodes)} intersection nodes (ngã ba/ngã tư)")
    log.info(f"   Edges  : {len(edges)} directed edges")
    log.info(f"   Degree : min={min(n['degree'] for n in nodes)}, "
             f"max={max(n['degree'] for n in nodes)}, "
             f"mean={sum(n['degree'] for n in nodes)/len(nodes):.1f}")
    log.info(f"   Street : {sum(1 for n in nodes if n['street_name'])} nodes có tên đường")

    # Bước 2–4: Insert vào DB
    log.info(f"🔌 Connecting: {args.db_url[:50]}...")
    engine = create_engine(args.db_url, echo=False)

    with Session(engine) as session:
        upsert_graph_snapshot(session, len(nodes), len(edges))
        osm_to_uuid = upsert_nodes(session, nodes)
        upsert_edges(session, edges, osm_to_uuid)

    log.info("=" * 65)
    log.info("✅ seed_graph.py hoàn tất!")
    log.info(f"   ✔ {len(nodes)} intersection nodes đã nạp vào bảng `nodes`")
    log.info(f"   ✔ {len(edges)} directed edges đã nạp vào bảng `edges`")
    log.info(f"   → Tiếp theo: chạy seed_correlation.py để nạp tương quan")
    log.info("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed intersection nodes & edges vào PostgreSQL"
    )
    parser.add_argument(
        "--graph-structure",
        default="ml_workspace/data/graph_structure_20260427_152321.npz",
        help="Path tới graph_structure_*.npz",
    )
    parser.add_argument(
        "--osm-graph",
        default="ml_workspace/data/osm_graph_20260427_152259.npz",
        help="Path tới osm_graph_*.npz",
    )
    parser.add_argument(
        "--graphml",
        default="ml_workspace/data/osm_10.7600_106.7150_10.8050_106.6750.graphml",
        help="Path tới file .graphml của OSM",
    )
    parser.add_argument(
        "--db-url",
        default=settings.database_url,
        help="SQLAlchemy DB URL (default: postgresql://postgres:postgres@localhost:5432/utraffic)",
    )
    args = parser.parse_args()
    main(args)