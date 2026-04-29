"""
scripts/seed_correlation.py
============================
Nạp ma trận tương quan từ DMFM ML model vào PostgreSQL.

Pipeline:
  1. Auto-discover tất cả horizon folders (h1, h3, h6, h9) dưới DATA_ROOT
  2. Load R_pred_series.npy + segment_ids.npy + R_pred_meta.csv cho mỗi horizon
  3. Load graph_structure.npz → incident matrix W (1 lần)
  4. Với mỗi pred_idx:
       R_t (3696×3696) → bridge → node_corr (1980×1980)
       → insert correlation_snapshot (mode = "YYYY-MM-DD_Slot_HHMM")
         + node_correlations rows (top-K per node)

DATA_ROOT mặc định: BE/ml_workspace/data/test/
  └── h1/
  │   ├── R_pred_series.npy
  │   ├── segment_ids.npy
  │   └── R_pred_meta.csv
  ├── h3/  (nếu có)
  ├── h6/  (nếu có)
  └── h9/  (nếu có)

GRAPH_STRUCTURE: ml_workspace/data/graph_structure_*.npz (tự tìm file mới nhất)

Cần chạy seed_graph.py TRƯỚC.

Chạy (đơn giản — không cần tham số):
  cd BE/
  uv run python scripts/seed_correlation.py

Hoặc override nguồn dữ liệu:
  uv run python scripts/seed_correlation.py \\
    --data-root  "ml_workspace/data/test" \\
    --graph-structure "ml_workspace/data/graph_structure_20260427_152321.npz" \\
    --top-k      50

Trong tương lai (AWS S3):
  uv run python scripts/seed_correlation.py --data-root "s3://bucket/data/test"
"""

from src.core.config import get_settings
import argparse
import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Thư mục gốc mặc định (tương đối với thư mục BE/)
DEFAULT_DATA_ROOT     = Path("ml_workspace/data/test")
DEFAULT_GRAPH_PATTERN = "ml_workspace/data/graph_structure_*.npz"

# Mapping tên folder → horizon int
HORIZON_MAP = {"h1": 1, "h3": 3, "h6": 6, "h9": 9}


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 0: Auto-discover nguồn dữ liệu
# ═════════════════════════════════════════════════════════════════════════════

def discover_horizon_dirs(data_root: Path) -> list[tuple[int, Path]]:
    """
    Quét data_root và trả về danh sách (horizon_int, folder_path) theo thứ tự.
    Bỏ qua folder nếu thiếu 3 file bắt buộc.
    """
    results = []
    for folder_name, horizon_int in sorted(HORIZON_MAP.items()):
        folder = data_root / folder_name
        if not folder.exists():
            continue
        r_pred   = folder / "R_pred_series.npy"
        seg_ids  = folder / "segment_ids.npy"
        meta_csv = folder / "R_pred_meta.csv"
        if not (r_pred.exists() and seg_ids.exists() and meta_csv.exists()):
            log.warning(f"  ⚠ Bỏ qua {folder}: thiếu file bắt buộc")
            continue
        results.append((horizon_int, folder))
        log.info(f"  ✓ Tìm thấy horizon h{horizon_int}: {folder}")
    return results


def find_graph_structure(pattern: str) -> Path:
    """Tìm file graph_structure_*.npz mới nhất theo glob pattern."""
    matches = sorted(Path(".").glob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"Không tìm thấy file graph_structure.npz (pattern: {pattern}). "
            "Hãy chắc chắn đang chạy từ thư mục BE/."
        )
    # Lấy file mới nhất (tên sắp xếp theo timestamp)
    return matches[-1]


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 1: Load dữ liệu cho 1 horizon
# ═════════════════════════════════════════════════════════════════════════════

def load_horizon_data(folder: Path, graph_structure_path: Path) -> dict:
    """
    Load dữ liệu cho 1 horizon folder.

    Trả về dict:
      R_pred_series : numpy mmap (N, 3696, 3696) float16
      seg_ids       : (3696,) int64
      meta_df       : DataFrame — pred_idx → date, slot_label, mode_key
      gs            : dict — arrays từ graph_structure.npz
    """
    log.info(f"📦 Loading R_pred_series.npy (mmap)...")
    R_pred_series = np.load(folder / "R_pred_series.npy", mmap_mode="r")
    log.info(f"  ✓ shape={R_pred_series.shape}, dtype={R_pred_series.dtype}")

    log.info(f"📦 Loading segment_ids.npy ...")
    seg_ids = np.load(folder / "segment_ids.npy")
    log.info(f"  ✓ shape={seg_ids.shape}")

    log.info(f"📦 Loading R_pred_meta.csv ...")
    meta_df = pd.read_csv(folder / "R_pred_meta.csv")

    # Chuẩn hóa slot_label (time-of-day)
    if "time_set_id" in meta_df.columns:
        meta_df["slot_label"] = meta_df["time_set_id"].astype(str)
    elif "slot_index" in meta_df.columns:
        meta_df["slot_label"] = meta_df["slot_index"].apply(lambda x: f"Slot_{x:04d}")
    else:
        meta_df["slot_label"] = meta_df["pred_idx"].apply(lambda x: f"Slot_{x:04d}")

    # Chuẩn hóa date (YYYY-MM-DD)
    if "date" in meta_df.columns:
        meta_df["date_str"] = pd.to_datetime(meta_df["date"]).dt.strftime("%Y-%m-%d")
    else:
        # Fallback: dùng pred_idx nếu không có date
        meta_df["date_str"] = "unknown"

    # mode_key = "YYYY-MM-DD_Slot_HHMM" — phân biệt cả ngày lẫn giờ
    meta_df["mode_key"] = meta_df["date_str"] + "_" + meta_df["slot_label"]

    log.info(
        f"  ✓ {len(meta_df)} rows | "
        f"dates={sorted(meta_df['date_str'].unique().tolist())} | "
        f"sample_modes={meta_df['mode_key'].tolist()[:2]} ... {meta_df['mode_key'].tolist()[-1]}"
    )

    log.info(f"📦 Loading graph_structure.npz ...")
    gs_raw = np.load(graph_structure_path, allow_pickle=True)
    gs = {
        "osm_node_ids":     gs_raw["osm_node_ids"],
        "coordinates":      gs_raw["coordinates"],
        "model_node_osm_u": gs_raw["model_node_osm_u_id"],
        "model_node_osm_v": gs_raw["model_node_osm_v_id"],
    }
    log.info(f"  ✓ {len(gs['osm_node_ids'])} intersection nodes")

    return {
        "R_pred_series": R_pred_series,
        "seg_ids":       seg_ids,
        "meta_df":       meta_df,
        "gs":            gs,
    }


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 2: Build incident matrix W
# ═════════════════════════════════════════════════════════════════════════════

def build_incident_matrix(gs: dict, seg_ids: np.ndarray) -> tuple:
    """
    Xây dựng normalized incident matrix W (n_nodes × n_segs).

    node_corr = W @ R_t @ W.T   (n_nodes × n_nodes)
    Mean aggregation: node_corr[A,B] = mean của R_t[i,j] với i∈incident(A), j∈incident(B)
    """
    log.info("🔄 Building incident matrix W...")

    osm_node_ids = gs["osm_node_ids"]
    u_ids = gs["model_node_osm_u"]
    v_ids = gs["model_node_osm_v"]
    n_nodes = len(osm_node_ids)
    n_segs  = len(seg_ids)

    osm_to_ni = {int(nid): ni for ni, nid in enumerate(osm_node_ids)}

    incident_pos: defaultdict[int, list[int]] = defaultdict(list)
    for pos, seg_model_idx in enumerate(seg_ids):
        seg_model_idx = int(seg_model_idx)
        u = int(u_ids[seg_model_idx])
        v = int(v_ids[seg_model_idx])
        if u in osm_to_ni:
            incident_pos[osm_to_ni[u]].append(pos)
        if v in osm_to_ni:
            incident_pos[osm_to_ni[v]].append(pos)

    W_raw = np.zeros((n_nodes, n_segs), dtype=np.float32)
    for ni in range(n_nodes):
        positions = incident_pos.get(ni, [])
        if positions:
            W_raw[ni, positions] = 1.0

    inc_count = W_raw.sum(axis=1)
    W = W_raw / np.maximum(inc_count, 1.0)[:, None]

    isolated = (inc_count == 0).sum()
    if isolated > 0:
        log.warning(f"  ⚠ {isolated} nodes không có incident segment nào")

    log.info(
        f"  ✓ W={n_nodes}×{n_segs} | avg_incident={inc_count[inc_count > 0].mean():.2f} | "
        f"max={int(inc_count.max())} | covered={int((inc_count > 0).sum())}/{n_nodes}"
    )
    return W, inc_count, osm_node_ids


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 3: Bridge R_t → node_corr
# ═════════════════════════════════════════════════════════════════════════════

def bridge_edge_to_node(R_t: np.ndarray, W: np.ndarray) -> np.ndarray:
    """node_corr = W @ R_t @ W.T, clip [-1,1], diag=1"""
    mid = W @ R_t.astype(np.float32)
    node_corr = mid @ W.T
    node_corr = np.clip(node_corr, -1.0, 1.0)
    np.fill_diagonal(node_corr, 1.0)
    return node_corr


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 4: Utilities
# ═════════════════════════════════════════════════════════════════════════════

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def fetch_db_lookups(session: Session) -> tuple:
    node_rows = session.execute(
        text("SELECT id, osm_node_id FROM nodes")
    ).fetchall()
    osm_to_uuid = {int(r[1]): uuid.UUID(str(r[0])) for r in node_rows}

    if not osm_to_uuid:
        raise RuntimeError("Bảng nodes rỗng — hãy chạy seed_graph.py trước!")

    uuid_to_osm = {v: k for k, v in osm_to_uuid.items()}
    edge_rows = session.execute(
        text("SELECT source_node_id, target_node_id FROM edges")
    ).fetchall()
    adj_set: set[tuple[int, int]] = set()
    for src_uid, tgt_uid in edge_rows:
        s = uuid_to_osm.get(uuid.UUID(str(src_uid)))
        t = uuid_to_osm.get(uuid.UUID(str(tgt_uid)))
        if s and t:
            adj_set.add((s, t))
            adj_set.add((t, s))

    log.info(f"  ✓ {len(osm_to_uuid)} nodes, {len(adj_set)//2} edges loaded from DB")
    return osm_to_uuid, adj_set


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 5: Insert 1 snapshot
# ═════════════════════════════════════════════════════════════════════════════

def insert_one_snapshot(
    session:      Session,
    mode_key:     str,          # "2024-08-27_Slot_0900"
    horizon:      int,
    node_corr:    np.ndarray,
    osm_node_ids: np.ndarray,
    osm_to_uuid:  dict[int, uuid.UUID],
    adj_set:      set[tuple[int, int]],
    top_k:        int,
    is_active:    bool,
) -> uuid.UUID:
    """Insert 1 correlation_snapshot + node_correlations rows."""
    n_nodes = len(osm_node_ids)
    now = datetime.now(timezone.utc)
    snapshot_id = uuid.uuid4()

    upper_tri = node_corr[np.triu_indices(n_nodes, k=1)]
    mean_corr = float(np.mean(upper_tri))
    std_corr  = float(np.std(upper_tri))

    session.execute(
        text("""
            INSERT INTO correlation_snapshots
              (id, method, mode, num_nodes, mean_corr, std_corr, is_active, computed_at)
            VALUES
              (:id, :method, :mode, :num_nodes, :mean_corr, :std_corr, :is_active, :computed_at)
        """),
        {
            "id":          str(snapshot_id),
            "method":      f"dmfm_bridge_h{horizon}",
            "mode":        mode_key,    # "2024-08-27_Slot_0900"
            "num_nodes":   n_nodes,
            "mean_corr":   mean_corr,
            "std_corr":    std_corr,
            "is_active":   is_active,
            "computed_at": now,
        },
    )

    # Build node_correlations rows
    corr_batch: list[dict] = []

    for ni in range(n_nodes):
        osm_id_a = int(osm_node_ids[ni])
        node_uuid_a = osm_to_uuid.get(osm_id_a)
        if node_uuid_a is None:
            continue

        corr_row = node_corr[ni].copy()
        corr_row[ni] = -2.0

        top_indices = np.argsort(-np.abs(corr_row))[:top_k]

        for rank, nj in enumerate(top_indices):
            if corr_row[nj] <= -2.0:
                continue
            osm_id_b = int(osm_node_ids[nj])
            node_uuid_b = osm_to_uuid.get(osm_id_b)
            if node_uuid_b is None:
                continue

            corr_batch.append({
                "id":                str(uuid.uuid4()),
                "snapshot_id":       str(snapshot_id),
                "node_a_id":         str(node_uuid_a),
                "node_b_id":         str(node_uuid_b),
                "correlation_value": round(float(corr_row[nj]), 6),
                "rank_from_a":       rank + 1,
                "is_adjacent":       (osm_id_a, osm_id_b) in adj_set,
                "created_at":        now,
            })

    BATCH = 5000
    for i in range(0, len(corr_batch), BATCH):
        session.execute(
            text("""
                INSERT INTO node_correlations
                  (id, snapshot_id, node_a_id, node_b_id,
                   correlation_value, rank_from_a, is_adjacent, created_at)
                VALUES
                  (:id, :snapshot_id, :node_a_id, :node_b_id,
                   :correlation_value, :rank_from_a, :is_adjacent, :created_at)
            """),
            corr_batch[i: i + BATCH],
        )

    session.flush()
    return snapshot_id


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main(args: argparse.Namespace) -> None:
    import time

    log.info("=" * 70)
    log.info("🚀 seed_correlation.py — DMFM Multi-Horizon Seeder")
    log.info("=" * 70)

    data_root = Path(args.data_root)
    if not data_root.exists():
        raise FileNotFoundError(f"DATA_ROOT không tồn tại: {data_root.resolve()}")

    # Tìm file graph_structure mới nhất
    graph_structure_path = Path(args.graph_structure) if args.graph_structure else find_graph_structure(DEFAULT_GRAPH_PATTERN)
    log.info(f"📐 Graph structure: {graph_structure_path}")

    # Auto-discover horizon folders
    log.info(f"🔍 Scanning DATA_ROOT: {data_root.resolve()}")
    horizon_dirs = discover_horizon_dirs(data_root)
    if not horizon_dirs:
        raise RuntimeError(f"Không tìm thấy horizon folder nào trong {data_root}. "
                           "Cần ít nhất 1 trong: h1, h3, h6, h9")

    log.info(f"📊 Sẽ xử lý {len(horizon_dirs)} horizon(s): "
             f"{[f'h{h}' for h, _ in horizon_dirs]}")

    # Connect DB
    log.info("🔌 Connecting DB...")
    engine = create_engine(args.db_url, echo=False)

    with Session(engine) as session:
        # ── Dọn sạch toàn bộ dữ liệu cũ ─────────────────────────────────────
        log.info("🧹 Xóa dữ liệu correlation cũ...")
        deleted_nc = session.execute(text("DELETE FROM node_correlations")).rowcount
        deleted_cs = session.execute(text("DELETE FROM correlation_snapshots")).rowcount
        session.commit()
        log.info(f"  ✓ Đã xóa {deleted_cs} snapshots, {deleted_nc:,} node_correlations rows")

        # Load DB lookups (dùng chung cho tất cả horizons)
        osm_to_uuid, adj_set = fetch_db_lookups(session)

        total_snapshots  = 0
        total_corr_rows  = 0
        grand_t0 = time.time()
        first_snapshot   = True   # snapshot đầu tiên trên tất cả horizons → is_active

        for horizon_int, folder in horizon_dirs:
            log.info("")
            log.info(f"{'─'*70}")
            log.info(f"📂 Horizon h{horizon_int} — {folder}")
            log.info(f"{'─'*70}")

            data = load_horizon_data(folder, graph_structure_path)
            R_pred_series = data["R_pred_series"]
            seg_ids       = data["seg_ids"]
            meta_df       = data["meta_df"]
            gs            = data["gs"]

            n_snapshots = R_pred_series.shape[0]

            # Build incident matrix (1 lần per horizon, seg_ids có thể khác)
            W, _, osm_node_ids = build_incident_matrix(gs, seg_ids)

            horizon_t0 = time.time()

            for pred_idx in range(n_snapshots):
                row = meta_df[meta_df["pred_idx"] == pred_idx]
                if row.empty:
                    log.warning(f"  ⚠ pred_idx={pred_idx} không có trong meta_csv, bỏ qua")
                    continue

                mode_key   = str(row.iloc[0]["mode_key"])    # "2024-08-27_Slot_0900"
                is_active  = first_snapshot
                first_snapshot = False

                t0 = time.time()
                log.info(f"  [{pred_idx+1:03d}/{n_snapshots}] h{horizon_int} | {mode_key} ...")

                R_t = np.array(R_pred_series[pred_idx], dtype=np.float32)
                node_corr = bridge_edge_to_node(R_t, W)

                snap_id = insert_one_snapshot(
                    session      = session,
                    mode_key     = mode_key,
                    horizon      = horizon_int,
                    node_corr    = node_corr,
                    osm_node_ids = osm_node_ids,
                    osm_to_uuid  = osm_to_uuid,
                    adj_set      = adj_set,
                    top_k        = args.top_k,
                    is_active    = is_active,
                )

                session.commit()
                total_snapshots += 1
                total_corr_rows += args.top_k * len(osm_to_uuid)

                corr_vals = node_corr[node_corr < 1.0]
                log.info(
                    f"    ✓ {str(snap_id)[:8]}... | "
                    f"mean={corr_vals.mean():.4f} | "
                    f"{time.time()-t0:.1f}s"
                    + (" 🌟 (active)" if is_active else "")
                )

            log.info(f"  ⏱ h{horizon_int} hoàn tất trong {time.time()-horizon_t0:.0f}s")

        grand_total = time.time() - grand_t0
        log.info("")
        log.info("=" * 70)
        log.info("✅ Hoàn tất!")
        log.info(f"   ✔ {total_snapshots} snapshots → correlation_snapshots")
        log.info(f"   ✔ ~{total_corr_rows:,} rows → node_correlations")
        log.info(f"   ⏱ Tổng: {grand_total:.0f}s = {grand_total/60:.1f} phút")
        log.info(f"   📐 Data root: {data_root.resolve()}")
        log.info(f"   🌟 Active snapshot: pred_idx=0 của h{horizon_dirs[0][0]}")
        log.info("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed DMFM correlation từ ml_workspace/data/test vào PostgreSQL"
    )
    parser.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help=(
            f"Thư mục gốc chứa h1/, h3/, h6/, h9/ (default: {DEFAULT_DATA_ROOT}). "
            "Trong tương lai có thể là s3://bucket/data/test"
        ),
    )
    parser.add_argument(
        "--graph-structure",
        default=None,
        help="Path tới graph_structure_*.npz (default: tự tìm file mới nhất trong ml_workspace/data/)",
    )
    parser.add_argument(
        "--db-url",
        default=get_settings().database_url,
        help="SQLAlchemy DB URL",
    )
    parser.add_argument(
        "--top-k", type=int, default=50,
        help="Số neighbors lưu per node per snapshot (default: 50)",
    )
    args = parser.parse_args()
    main(args)