"""
scripts/seed_correlation.py
Tạo mock correlation edge→edge rồi aggregate về node→node.
Insert vào node_correlations + node_correlation_cache.

Khi bạn bạn có file edge corr thật:
  → Thay hàm _build_edge_corr_matrix() để đọc file CSV/NPZ thật
  → Phần còn lại giữ nguyên

Chạy: uv run python scripts/seed_correlation.py
"""
import sys
import math
import json
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session, select, text
from src.storage.database import engine
from src.storage import models  # noqa
from src.storage.models.graph import Node, Edge
from src.storage.models.correlation import (
    CorrelationSnapshot, NodeCorrelation, NodeCorrelationCache
)

NPZ_PATH = Path("ml_workspace/data/graph_structure_20260328_121113.npz")
SEED = 20260328   # seed cố định → kết quả ổn định mỗi lần chạy


# ════════════════════════════════════════════════════════════
# BƯỚC 1: Tạo mock edge correlation [429 × 429]
# Khi có file thật: thay hàm này để đọc CSV/NPZ từ bạn bạn
# ════════════════════════════════════════════════════════════
def _build_edge_corr_matrix(n_edges: int) -> np.ndarray:
    """
    Tạo ma trận edge correlation [E × E] mock.
    Symmetric, diagonal = 1.0, values trong [-1, 1].

    ĐỂ THAY BẰNG DỮ LIỆU THẬT:
        import pandas as pd
        df = pd.read_csv("ml_workspace/data/edge_correlation.csv", index_col=0)
        return df.values  # shape phải là [429, 429]
    """
    rng = np.random.default_rng(SEED)
    raw = rng.uniform(-1, 1, (n_edges, n_edges))
    # Làm symmetric
    mat = (raw + raw.T) / 2
    np.fill_diagonal(mat, 1.0)
    # Clip để đảm bảo [-1, 1]
    return np.clip(mat, -1.0, 1.0)


# ════════════════════════════════════════════════════════════
# BƯỚC 2: Aggregate edge corr → node corr [305 × 305]
# corr(node_A, node_B) = mean( corr(edge_i, edge_j) )
#   với edge_i thuộc node_A, edge_j thuộc node_B
# ════════════════════════════════════════════════════════════
def _aggregate_to_node_corr(
    edge_corr: np.ndarray,
    edge_index: np.ndarray,
    n_nodes: int,
) -> np.ndarray:
    """
    edge_corr   : [E, E]
    edge_index  : [2, E] — edge_index[0] = src nodes, edge_index[1] = tgt nodes
    n_nodes     : 305
    returns     : [N, N] node correlation matrix
    """
    E = edge_corr.shape[0]

    # Map node_idx → list of edge_ids
    node_to_edges: dict[int, list[int]] = {i: [] for i in range(n_nodes)}
    for eid in range(E):
        src = int(edge_index[0, eid])
        tgt = int(edge_index[1, eid])
        node_to_edges[src].append(eid)
        node_to_edges[tgt].append(eid)

    node_corr = np.zeros((n_nodes, n_nodes), dtype=np.float32)

    for i in range(n_nodes):
        edges_i = node_to_edges[i]
        if not edges_i:
            continue
        for j in range(n_nodes):
            if i == j:
                node_corr[i, j] = 1.0
                continue
            edges_j = node_to_edges[j]
            if not edges_j:
                continue
            # Lấy sub-matrix [edges_i × edges_j] rồi tính mean
            sub = edge_corr[np.ix_(edges_i, edges_j)]
            node_corr[i, j] = float(sub.mean())

    return node_corr


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def main():
    print("=" * 60)
    print("SEED CORRELATION — mock edge corr → node corr")
    print("=" * 60)

    if not NPZ_PATH.exists():
        print(f"❌ File không tồn tại: {NPZ_PATH}")
        sys.exit(1)

    # ── Load NPZ ──────────────────────────────────────────────
    print(f"\n📂 Đọc {NPZ_PATH}...")
    gs      = np.load(str(NPZ_PATH), allow_pickle=True)
    edge_index   = gs['edge_index']    # [2, 429]
    coordinates  = gs['coordinates']   # [305, 2]
    osm_node_ids = gs['osm_node_ids']  # [305]
    N, E = len(osm_node_ids), edge_index.shape[1]
    print(f"   {N} nodes, {E} edges")

    # ── Bước 1: Edge corr matrix ──────────────────────────────
    print(f"\n⚙️  Bước 1: Tạo mock edge corr [{E}×{E}]...")
    edge_corr = _build_edge_corr_matrix(E)
    print(f"   Xong. min={edge_corr.min():.3f}, max={edge_corr.max():.3f}")

    # ── Bước 2: Aggregate → node corr ────────────────────────
    print(f"\n⚙️  Bước 2: Aggregate → node corr [{N}×{N}]...")
    node_corr = _aggregate_to_node_corr(edge_corr, edge_index, N)
    print(f"   Xong. min={node_corr.min():.3f}, max={node_corr.max():.3f}")

    # ── Bước 3: Load nodes từ DB ──────────────────────────────
    print(f"\n📋 Bước 3: Load nodes từ DB...")
    with Session(engine) as session:
        db_nodes = session.exec(
            select(Node).order_by(Node.node_index)
        ).all()

        if len(db_nodes) != N:
            print(f"❌ DB có {len(db_nodes)} nodes, NPZ có {N} nodes")
            print("   Chạy seed_graph.py trước!")
            sys.exit(1)

        # Map node_index → Node object
        idx_to_node = {n.node_index: n for n in db_nodes}
        print(f"   ✅ {len(db_nodes)} nodes loaded")

        # ── Xóa correlation cũ ────────────────────────────────
        print("\n🗑️  Xóa correlation cũ...")
        session.exec(text("DELETE FROM node_correlation_cache"))
        session.exec(text("DELETE FROM node_correlations"))
        session.exec(text("DELETE FROM correlation_snapshots"))
        session.commit()

        # ── Tạo CorrelationSnapshot ───────────────────────────
        print("\n📸 Tạo CorrelationSnapshot...")
        snapshot = CorrelationSnapshot(
            id=uuid.uuid4(),
            # model_version_id bỏ qua vì không dùng ML
            method="mock_edge_aggregate",
            mode="mock",
            num_nodes=N,
            mean_corr=float(node_corr[~np.eye(N, dtype=bool)].mean()),
            std_corr=float(node_corr[~np.eye(N, dtype=bool)].std()),
            is_active=True,
            computed_at=datetime.utcnow(),
        )
        session.add(snapshot)
        session.flush()
        snapshot_id = snapshot.id
        print(f"   snapshot_id = {snapshot_id}")

        # ── Insert NodeCorrelation (tất cả cặp i≠j) ──────────
        print(f"\n📊 Bước 4: Insert NodeCorrelation ({N*(N-1)} rows)...")

        # Build neighbor set từ edge_index (adjacent nodes)
        adjacent: dict[int, set[int]] = {i: set() for i in range(N)}
        for eid in range(E):
            s, t = int(edge_index[0, eid]), int(edge_index[1, eid])
            adjacent[s].add(t)
            adjacent[t].add(s)

        BATCH = 5000
        batch = []
        total = 0

        for i in range(N):
            node_a = idx_to_node[i]
            # Tính rank từ góc nhìn của node_i: sort các j theo |corr| giảm dần
            corr_row = node_corr[i]  # [N]
            sorted_j = np.argsort(-np.abs(corr_row)).tolist()  # index j sorted
            rank_map = {j: rank for rank, j in enumerate(sorted_j) if j != i}

            for j in range(N):
                if i == j:
                    continue
                corr_val = float(node_corr[i, j])
                node_b = idx_to_node[j]
                is_adj = j in adjacent[i]

                batch.append(NodeCorrelation(
                    id=uuid.uuid4(),
                    snapshot_id=snapshot_id,
                    node_a_id=node_a.id,
                    node_b_id=node_b.id,
                    correlation_value=round(corr_val, 6),
                    rank_from_a=rank_map.get(j),
                    is_adjacent=is_adj,
                    created_at=datetime.utcnow(),
                ))

                if len(batch) >= BATCH:
                    session.add_all(batch)
                    session.flush()
                    total += len(batch)
                    batch = []
                    print(f"   ... {total:,} rows inserted")

        if batch:
            session.add_all(batch)
            session.flush()
            total += len(batch)

        session.commit()
        print(f"   ✅ Total: {total:,} NodeCorrelation rows")

        # ── Build NodeCorrelationCache (JSONB per node) ───────
        print(f"\n💾 Bước 5: Build NodeCorrelationCache (1 row per node)...")

        cache_batch = []
        for i in range(N):
            node_a = idx_to_node[i]
            lat_a, lon_a = float(coordinates[i, 0]), float(coordinates[i, 1])

            # Tất cả 304 neighbors, sorted by |corr| desc
            corr_row = node_corr[i]
            sorted_j = sorted(
                [j for j in range(N) if j != i],
                key=lambda j: abs(corr_row[j]),
                reverse=True
            )

            neighbors_json = []
            for rank, j in enumerate(sorted_j):
                node_b = idx_to_node[j]
                lat_b = float(coordinates[j, 0])
                lon_b = float(coordinates[j, 1])
                dist_m = haversine_m(lat_a, lon_a, lat_b, lon_b)
                is_adj = j in adjacent[i]

                neighbors_json.append({
                    "node_index": j,
                    "osm_node_id": int(osm_node_ids[j]),
                    "node_id": str(node_b.id),
                    "lat": round(lat_b, 7),
                    "lon": round(lon_b, 7),
                    "corr": round(float(corr_row[j]), 6),
                    "rank": rank,
                    "dist_m": round(dist_m, 1),
                    "is_adjacent": is_adj,
                })

            cache_batch.append(NodeCorrelationCache(
                node_id=node_a.id,
                snapshot_id=snapshot_id,
                neighbors_json=neighbors_json,
                updated_at=datetime.utcnow(),
            ))

        session.add_all(cache_batch)
        session.commit()
        print(f"   ✅ {len(cache_batch)} cache rows inserted")

    print("\n" + "=" * 60)
    print("✅ SEED CORRELATION HOÀN THÀNH")
    print(f"   {N*(N-1):,} correlation pairs trong DB")
    print(f"   {N} JSONB cache rows sẵn sàng cho API")
    print("=" * 60)


if __name__ == "__main__":
    main()