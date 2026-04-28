"""
scripts/seed_graph.py
Nạp 305 nodes và 429 edges từ graph_structure.npz vào PostgreSQL.
Chạy 1 lần: uv run python scripts/seed_graph.py
"""
import sys
import math
from pathlib import Path

import numpy as np

# Thêm src vào path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import get_settings
from src.storage.database import engine
from src.storage import models  # noqa — đăng ký tất cả models

import uuid
from datetime import datetime
from sqlmodel import Session, select, text

settings = get_settings()

NPZ_PATH = Path("ml_workspace/data/graph_structure_20260328_121113.npz")


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Tính khoảng cách giữa 2 tọa độ (mét)."""
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def main():
    print("=" * 60)
    print("SEED GRAPH — nodes + edges từ graph_structure.npz")
    print("=" * 60)

    if not NPZ_PATH.exists():
        print(f"❌ Không tìm thấy file: {NPZ_PATH}")
        print("   Đảm bảo file nằm ở ml_workspace/data/")
        sys.exit(1)

    # ── Load NPZ ──────────────────────────────────────────────
    print(f"\n📂 Đọc file: {NPZ_PATH}")
    gs = np.load(str(NPZ_PATH), allow_pickle=True)

    osm_node_ids = gs['osm_node_ids']          # [305] int64
    coordinates  = gs['coordinates']            # [305, 2] float (lat, lon)
    edge_index   = gs['edge_index']             # [2, 429] int64
    node_feats   = gs['node_features']          # [305, 4]
    edge_feats   = gs['edge_features_temporal'] # [429, 744, 11]
    feat_names   = list(gs['edge_feature_names'])

    N = len(osm_node_ids)   # 305
    E = edge_index.shape[1] # 429

    print(f"   Nodes: {N}, Edges: {E}")

    # Feature indices
    len_idx = feat_names.index('osm_length_m')
    hw_idx  = feat_names.index('osm_highway_type')

    with Session(engine) as session:

        # ── Xóa data cũ (để chạy lại được) ──────────────────
        print("\n🗑️  Xóa data cũ...")
        session.exec(text("DELETE FROM segment_edge_mappings"))
        session.exec(text("DELETE FROM traffic_observations"))
        session.exec(text("DELETE FROM node_correlations"))
        session.exec(text("DELETE FROM node_correlation_cache"))
        session.exec(text("DELETE FROM correlation_snapshots"))
        session.exec(text("DELETE FROM edges"))
        session.exec(text("DELETE FROM nodes"))
        session.commit()

        # ── Insert NODES ──────────────────────────────────────
        print(f"\n📍 Insert {N} nodes...")

        # normalize lat/lon về [0,1]
        lats = coordinates[:, 0]
        lons = coordinates[:, 1]
        lat_min, lat_max = lats.min(), lats.max()
        lon_min, lon_max = lons.min(), lons.max()

        node_uuid_map = {}  # node_index → UUID (để dùng khi insert edges)

        from src.storage.models.graph import Node
        nodes_to_insert = []
        for i in range(N):
            uid = uuid.uuid4()
            node_uuid_map[i] = uid

            lat = float(coordinates[i, 0])
            lon = float(coordinates[i, 1])
            degree = float(node_feats[i, 0])
            betweenness = float(node_feats[i, 1])
            lat_norm = (lat - lat_min) / (lat_max - lat_min) if lat_max > lat_min else 0.0
            lon_norm = (lon - lon_min) / (lon_max - lon_min) if lon_max > lon_min else 0.0

            nodes_to_insert.append(Node(
                id=uid,
                osm_node_id=int(osm_node_ids[i]),
                node_index=i,
                lat=lat,
                lon=lon,
                degree=degree,
                betweenness_norm=betweenness,
                lat_norm=lat_norm,
                lon_norm=lon_norm,
                created_at=datetime.utcnow(),
            ))

        session.add_all(nodes_to_insert)
        session.commit()
        print(f"   ✅ Inserted {N} nodes")

        # ── Insert EDGES ──────────────────────────────────────
        print(f"\n🔗 Insert {E} edges...")

        from src.storage.models.graph import Edge
        edges_to_insert = []
        for eid in range(E):
            src_idx = int(edge_index[0, eid])
            tgt_idx = int(edge_index[1, eid])

            length_m   = float(edge_feats[eid, 0, len_idx])
            hw_type    = int(edge_feats[eid, 0, hw_idx])

            edges_to_insert.append(Edge(
                id=uuid.uuid4(),
                source_node_id=node_uuid_map[src_idx],
                target_node_id=node_uuid_map[tgt_idx],
                source_index=src_idx,
                target_index=tgt_idx,
                length_m=length_m,
                highway_type=hw_type,
                created_at=datetime.utcnow(),
            ))

        session.add_all(edges_to_insert)
        session.commit()
        print(f"   ✅ Inserted {E} edges")

    print("\n" + "=" * 60)
    print("✅ SEED GRAPH HOÀN THÀNH")
    print(f"   {N} nodes và {E} edges đã có trong DB")
    print("   Chạy tiếp: uv run python scripts/seed_correlation.py")
    print("=" * 60)


if __name__ == "__main__":
    main()