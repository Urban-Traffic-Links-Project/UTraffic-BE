"""
scripts/seed_edge_correlation.py
=================================
Nạp ma trận tương quan EDGE (segment-level) từ DMFM ML model vào PostgreSQL.

Đây là script SEED ĐỘC LẬP — chỉ xử lý bảng edge_correlations.
Dùng khi:
  - Muốn nạp lại edge_correlations mà không reset node_correlations/snapshots.
  - Snapshot đã tồn tại trong DB (chạy seed_correlation.py trước).
  - Test riêng logic edge correlation.

Pipeline:
  1. Đọc toàn bộ correlation_snapshots từ DB (đã được seed_correlation.py tạo)
  2. Với mỗi snapshot → tìm horizon + pred_idx → load R_t từ file
  3. Tính top-K edge correlation từ R_t thô (không qua bridge)
  4. Insert vào edge_correlations, bỏ qua snapshot đã có data

DATA_ROOT mặc định: BE/ml_workspace/data/dmfm_predictions/test/
  └── h1/
  │   ├── R_pred_series.npy
  │   ├── segment_ids.npy
  │   └── R_pred_meta.csv
  └── h3/ h6/ h9/ (nếu có)

Cần chạy seed_graph.py + seed_correlation.py TRƯỚC.

Chạy:
  cd BE/
  uv run python scripts/seed_edge_correlation.py

Override:
  uv run python scripts/seed_edge_correlation.py \\
    --data-root  "ml_workspace/data/dmfm_predictions/test" \\
    --graph-structure "ml_workspace/data/graph_structure_*.npz" \\
    --top-k 50 \\
    --reset
"""

from src.core.config import get_settings
import argparse
import io
import logging
import re
import time
import uuid
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

# ── Cấu hình đường dẫn ────────────────────────────────────────
S3_DATA_ROOT     = "ml-data/dmfm_predictions/test"
S3_GRAPH_PATTERN = "ml-data/graph_structure_*.npz"

LOCAL_DATA_ROOT     = Path("ml_workspace/data/dmfm_predictions/test")
LOCAL_GRAPH_PATTERN = "ml_workspace/data/graph_structure_*.npz"

HORIZON_MAP = {"h1": 1, "h3": 3, "h6": 6, "h9": 9}

# Regex parse method → horizon  (vd: "dmfm_bridge_h3" → 3)
METHOD_HORIZON_RE = re.compile(r"h(\d+)$")


# ═════════════════════════════════════════════════════════════════════════════
# Tái dùng helpers từ seed_correlation (import trực tiếp)
# ═════════════════════════════════════════════════════════════════════════════

def _import_helpers():
    """Lazy import — chỉ gọi sau khi sys.path đúng."""
    from scripts.seed_correlation import (
        load_s3_or_local_npy_mmap,
        load_s3_or_local_npy,
        load_s3_or_local_csv,
        load_s3_or_local_npz,
        discover_horizon_dirs,
        find_graph_structure,
    )
    return {
        "load_npy_mmap": load_s3_or_local_npy_mmap,
        "load_npy":      load_s3_or_local_npy,
        "load_csv":      load_s3_or_local_csv,
        "load_npz":      load_s3_or_local_npz,
        "discover":      discover_horizon_dirs,
        "find_gs":       find_graph_structure,
    }


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 1: Fetch DB lookups
# ═════════════════════════════════════════════════════════════════════════════

def fetch_all_snapshots(session: Session) -> list[dict]:
    """
    Trả về danh sách tất cả snapshots kèm horizon từ method field.
    [{"snapshot_id": UUID, "mode": "...", "horizon": int, "method": "..."}]
    """
    rows = session.execute(
        text("SELECT id, method, mode FROM correlation_snapshots ORDER BY method, mode")
    ).fetchall()

    results = []
    for r in rows:
        m = METHOD_HORIZON_RE.search(str(r[1]))
        horizon = int(m.group(1)) if m else 1
        results.append({
            "snapshot_id": uuid.UUID(str(r[0])),
            "method":      r[1],
            "mode":        r[2],
            "horizon":     horizon,
        })

    log.info(f"  ✓ Tìm thấy {len(results)} snapshots trong DB")
    return results


def fetch_segment_to_edge_map(
    session: Session,
    seg_ids: np.ndarray,
    gs_s3_key: str,
    gs_local_path: Path,
    load_npz_fn = None,
) -> dict[int, uuid.UUID]:
    """
    Build map: segment_pos (vị trí trong seg_ids) → OSM edge UUID.

    Dùng model_node_osm_u_id/v_id từ graph_structure.npz để lookup:
      seg_ids[pos] = model_node_id → (u_osm, v_osm) → edges.id
    Không phụ thuộc vào segment_edge_mappings.
    """
    # Load graph_structure — dùng helper S3/local nếu được truyền vào
    if load_npz_fn is not None:
        gs_raw = load_npz_fn(gs_s3_key, gs_local_path)
    else:
        gs_raw = dict(np.load(str(gs_local_path), allow_pickle=True))

    model_u = gs_raw["model_node_osm_u_id"]
    model_v = gs_raw["model_node_osm_v_id"]

    edge_rows = session.execute(
        text("""
            SELECT e.id, n_src.osm_node_id AS u, n_tgt.osm_node_id AS v
            FROM edges e
            JOIN nodes n_src ON n_src.id = e.source_node_id
            JOIN nodes n_tgt ON n_tgt.id = e.target_node_id
        """)
    ).fetchall()

    uv_to_edge: dict[tuple[int, int], uuid.UUID] = {}
    for edge_uuid, u_osm, v_osm in edge_rows:
        uv_to_edge[(int(u_osm), int(v_osm))] = uuid.UUID(str(edge_uuid))

    pos_to_edge: dict[int, uuid.UUID] = {}
    for pos, model_node_id in enumerate(seg_ids):
        mn = int(model_node_id)
        if mn >= len(model_u):
            continue
        u = int(model_u[mn])
        v = int(model_v[mn])
        edge_uuid = uv_to_edge.get((u, v))
        if edge_uuid is not None:
            pos_to_edge[pos] = edge_uuid

    matched = len(pos_to_edge)
    total   = len(seg_ids)
    log.info(
        f"  ✓ segment→edge map: {matched}/{total} segments có OSM edge "
        f"({100*matched//total if total else 0}%)"
    )
    return pos_to_edge


def already_has_edge_corr(session: Session, snapshot_id: uuid.UUID) -> bool:
    """Kiểm tra snapshot đã có edge_correlations chưa."""
    count = session.execute(
        text("SELECT COUNT(1) FROM edge_correlations WHERE snapshot_id = :sid LIMIT 1"),
        {"sid": str(snapshot_id)},
    ).scalar()
    return (count or 0) > 0


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 2: Insert edge correlations cho 1 snapshot
# ═════════════════════════════════════════════════════════════════════════════

def insert_edge_correlations(
    session:     Session,
    snapshot_id: uuid.UUID,
    R_t:         np.ndarray,           # (n_segs, n_segs)
    pos_to_edge: dict[int, uuid.UUID], # segment_pos → edge UUID
    top_k:       int,
) -> int:
    """
    Insert top-K edge correlations từ R_t thô vào bảng edge_correlations.
    Trả về số rows đã insert.
    """
    from datetime import datetime, timezone

    n_segs = R_t.shape[0]
    now = datetime.now(timezone.utc)
    edge_batch: list[dict] = []

    for seg_a in range(n_segs):
        corr_row = R_t[seg_a].copy().astype(np.float32)
        corr_row[seg_a] = -2.0  # loại self-correlation

        top_indices = np.argsort(-np.abs(corr_row))[:top_k]

        for rank, seg_b in enumerate(top_indices):
            val = float(corr_row[seg_b])
            if val <= -2.0:
                continue

            edge_batch.append({
                "id":                str(uuid.uuid4()),
                "snapshot_id":       str(snapshot_id),
                "segment_a_pos":     int(seg_a),
                "segment_b_pos":     int(seg_b),
                "edge_a_id":         str(pos_to_edge[seg_a]) if seg_a in pos_to_edge else None,
                "edge_b_id":         str(pos_to_edge[seg_b]) if seg_b in pos_to_edge else None,
                "correlation_value": round(val, 6),
                "rank_from_a":       rank + 1,
                "created_at":        now,
            })

    BATCH = 5000
    for i in range(0, len(edge_batch), BATCH):
        session.execute(
            text("""
                INSERT INTO edge_correlations
                  (id, snapshot_id, segment_a_pos, segment_b_pos,
                   edge_a_id, edge_b_id,
                   correlation_value, rank_from_a, created_at)
                VALUES
                  (:id, :snapshot_id, :segment_a_pos, :segment_b_pos,
                   :edge_a_id, :edge_b_id,
                   :correlation_value, :rank_from_a, :created_at)
            """),
            edge_batch[i: i + BATCH],
        )

    session.flush()
    return len(edge_batch)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main(args: argparse.Namespace) -> None:
    log.info("=" * 70)
    log.info("🚀 seed_edge_correlation.py — Edge Correlation Seeder (standalone)")
    log.info("=" * 70)

    # Resolve data root
    data_root_str = str(args.data_root) if args.data_root else None
    if data_root_str and data_root_str.startswith("s3://"):
        s3_data_root   = data_root_str.replace("s3://", "")
        local_data_root = Path("invalid_fallback")
    elif data_root_str:
        s3_data_root   = "invalid_fallback"
        local_data_root = Path(data_root_str)
    else:
        s3_data_root   = S3_DATA_ROOT
        local_data_root = LOCAL_DATA_ROOT

    # Resolve graph-structure
    gs_str = str(args.graph_structure) if args.graph_structure else None
    if gs_str and gs_str.startswith("s3://"):
        s3_gs_pattern   = gs_str.replace("s3://", "")
        local_gs_pattern = "invalid_fallback"
    elif gs_str:
        s3_gs_pattern   = "invalid_fallback"
        local_gs_pattern = gs_str
    else:
        s3_gs_pattern   = S3_GRAPH_PATTERN
        local_gs_pattern = str(LOCAL_GRAPH_PATTERN)

    # Import helpers từ seed_correlation
    h = _import_helpers()

    log.info("🔍 Phát hiện horizon folders...")
    horizon_dirs = h["discover"](s3_data_root, local_data_root)
    if not horizon_dirs:
        raise RuntimeError("Không tìm thấy horizon folder nào (h1/h3/h6/h9).")

    log.info("🔍 Tìm graph_structure.npz...")
    gs_s3_key, gs_local_path = h["find_gs"](s3_gs_pattern, local_gs_pattern)
    log.info(f"  ✓ graph_structure: S3={gs_s3_key or 'N/A'} | Local={gs_local_path}")

    # Build horizon→folder map
    horizon_to_dir: dict[int, tuple[str, Path]] = {
        hz: (fk, fp) for hz, fk, fp in horizon_dirs
    }

    # Connect DB
    log.info("🔌 Connecting DB...")
    engine = create_engine(args.db_url, echo=False)

    with Session(engine) as session:
        # Tùy chọn: reset toàn bộ edge_correlations trước
        if args.reset:
            log.info("🧹 --reset: Xóa toàn bộ edge_correlations cũ...")
            deleted = session.execute(text("DELETE FROM edge_correlations")).rowcount
            session.commit()
            log.info(f"  ✓ Đã xóa {deleted:,} rows")

        # Lấy danh sách snapshots từ DB
        log.info("📋 Fetch danh sách snapshots từ DB...")
        snapshots = fetch_all_snapshots(session)
        if not snapshots:
            raise RuntimeError(
                "Không có snapshot nào trong DB. Chạy seed_correlation.py trước!"
            )

        # Build segment→edge map (dùng chung — seg_ids của h1 làm base)
        # Nếu các horizon có seg_ids khác nhau, map sẽ được build lại per-horizon
        log.info("🔗 Fetch segment→edge mapping từ DB...")
        # Load seg_ids của horizon đầu tiên để khởi tạo map
        first_hz, first_fk, first_fp = horizon_dirs[0]
        seg_ids_init = h["load_npy"](f"{first_fk}segment_ids.npy", first_fp / "segment_ids.npy")
        pos_to_edge_cache: dict[int, dict[int, uuid.UUID]] = {}

        def get_pos_to_edge(horizon: int) -> dict[int, uuid.UUID]:
            if horizon not in pos_to_edge_cache:
                fk, fp = horizon_to_dir[horizon]
                seg_ids_h = h["load_npy"](f"{fk}segment_ids.npy", fp / "segment_ids.npy")
                pos_to_edge_cache[horizon] = fetch_segment_to_edge_map(
                    session       = session,
                    seg_ids       = seg_ids_h,
                    gs_s3_key     = gs_s3_key,
                    gs_local_path = gs_local_path,
                    load_npz_fn   = h["load_npz"],
                )
            return pos_to_edge_cache[horizon]

        total_rows   = 0
        skipped      = 0
        grand_t0     = time.time()

        for snap in snapshots:
            snap_id  = snap["snapshot_id"]
            mode     = snap["mode"]
            horizon  = snap["horizon"]

            # Skip nếu đã có data và không có --reset
            if not args.reset and already_has_edge_corr(session, snap_id):
                log.info(f"  ⏭ Bỏ qua snapshot {str(snap_id)[:8]}... ({mode}) — đã có edge_corr")
                skipped += 1
                continue

            if horizon not in horizon_to_dir:
                log.warning(f"  ⚠ Horizon h{horizon} không có trong data dirs, bỏ qua {mode}")
                skipped += 1
                continue

            folder_key, folder_path = horizon_to_dir[horizon]

            t0 = time.time()
            log.info(f"  📐 [{mode}] h{horizon} — load R_pred_series...")

            # Parse pred_idx từ meta CSV
            meta_df = h["load_csv"](
                f"{folder_key}R_pred_meta.csv",
                folder_path / "R_pred_meta.csv",
            )
            # Chuẩn hóa mode_key
            if "time_set_id" in meta_df.columns:
                meta_df["slot_label"] = meta_df["time_set_id"].astype(str)
            elif "slot_index" in meta_df.columns:
                meta_df["slot_label"] = meta_df["slot_index"].apply(lambda x: f"Slot_{x:04d}")
            else:
                meta_df["slot_label"] = meta_df["pred_idx"].apply(lambda x: f"Slot_{x:04d}")

            if "date" in meta_df.columns:
                meta_df["date_str"] = pd.to_datetime(meta_df["date"]).dt.strftime("%Y-%m-%d")
            else:
                meta_df["date_str"] = "unknown"

            meta_df["mode_key"] = meta_df["date_str"] + "_" + meta_df["slot_label"]

            match_row = meta_df[meta_df["mode_key"] == mode]
            if match_row.empty:
                log.warning(f"  ⚠ Không tìm thấy mode_key='{mode}' trong meta CSV của h{horizon}")
                skipped += 1
                continue

            pred_idx = int(match_row.iloc[0]["pred_idx"])

            # Load R_t tại pred_idx
            R_pred_series = h["load_npy_mmap"](
                f"{folder_key}R_pred_series.npy",
                folder_path / "R_pred_series.npy",
            )
            R_t = np.array(R_pred_series[pred_idx], dtype=np.float32)

            # Lấy pos_to_edge cho horizon này
            pos_to_edge = get_pos_to_edge(horizon)

            # Insert edge correlations
            n_rows = insert_edge_correlations(
                session     = session,
                snapshot_id = snap_id,
                R_t         = R_t,
                pos_to_edge = pos_to_edge,
                top_k       = args.top_k,
            )
            session.commit()
            total_rows += n_rows

            log.info(
                f"    ✓ {str(snap_id)[:8]}... | {n_rows:,} edge_corr rows | "
                f"{time.time()-t0:.1f}s"
            )

        grand_total = time.time() - grand_t0
        log.info("")
        log.info("=" * 70)
        log.info("✅ Hoàn tất!")
        log.info(f"   ✔ {len(snapshots) - skipped} snapshots đã xử lý")
        log.info(f"   ⏭ {skipped} snapshots bỏ qua")
        log.info(f"   ✔ {total_rows:,} rows → edge_correlations")
        log.info(f"   ⏱ Tổng: {grand_total:.0f}s = {grand_total/60:.1f} phút")
        log.info("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed edge correlations (segment-level) từ R_t thô vào PostgreSQL"
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help=(
            "Thư mục gốc chứa h1/, h3/... (S3 URL hoặc local path). "
            "Mặc định tự động thử S3 trước, fallback về local."
        ),
    )
    parser.add_argument(
        "--graph-structure",
        default=None,
        help="Path tới graph_structure_*.npz. Mặc định thử S3 trước, fallback local.",
    )
    parser.add_argument(
        "--db-url",
        default=get_settings().database_url,
        help="SQLAlchemy DB URL",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=50,
        help="Số neighbors lưu per segment per snapshot (default: 50)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Xóa toàn bộ edge_correlations cũ trước khi seed lại. "
            "Nếu không có --reset, bỏ qua snapshot đã có data."
        ),
    )
    args = parser.parse_args()
    main(args)
