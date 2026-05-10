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
         + edge_correlations rows (top-K per segment, trực tiếp từ R_t)

DATA_ROOT mặc định: BE/ml_workspace/data/dmfm_predictions/test/
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
    --data-root  "ml_workspace/data/dmfm_predictions/test" \\
    --graph-structure "ml_workspace/data/graph_structure_20260427_152321.npz" \\
    --top-k      50

Trong tương lai (AWS S3):
  uv run python scripts/seed_correlation.py --data-root "s3://bucket/data/dmfm_predictions/test"
"""

from src.core.config import get_settings
import argparse
import io
import logging
import tempfile
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

# ── Cấu hình đường dẫn ────────────────────────────────────────
# S3 keys
S3_DATA_ROOT      = "ml-data/dmfm_predictions/test"
S3_GRAPH_PATTERN  = "ml-data/graph_structure_*.npz"

# Local fallback (tương đối với thư mục BE/)
LOCAL_DATA_ROOT     = Path("ml_workspace/data/dmfm_predictions/test")
LOCAL_GRAPH_PATTERN = "ml_workspace/data/graph_structure_*.npz"

# Mapping tên folder → horizon int
HORIZON_MAP = {"h1": 1, "h3": 3, "h6": 6, "h9": 9}


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS: Load S3 vs Local
# ═════════════════════════════════════════════════════════════════════════════

def get_s3_cache_path(s3_key: str) -> Path:
    tmp_dir = Path(tempfile.gettempdir()) / "dmfm_s3_cache"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / s3_key.replace("/", "_")

def load_s3_or_local_npz(s3_key: str, local_path: Path):
    settings = get_settings()
    if s3_key and settings.aws_access_key_id:
        try:
            from src.storage.aws.s3_client import get_s3_client
            log.info(f"📥 Download từ S3: {s3_key}")
            raw = get_s3_client().download_bytes(s3_key)
            return np.load(io.BytesIO(raw), allow_pickle=True)
        except Exception as e:
            log.warning(f"S3 failed ({e}), fallback về local")

    if not local_path.exists():
        raise FileNotFoundError(f"❌ Không tìm thấy: S3={s3_key}, Local={local_path}")
    log.info(f"📂 Load local: {local_path}")
    return np.load(str(local_path), allow_pickle=True)

def load_s3_or_local_npy_mmap(s3_key: str, local_path: Path):
    settings = get_settings()
    if s3_key and settings.aws_access_key_id:
        try:
            from src.storage.aws.s3_client import get_s3_client
            s3 = get_s3_client()
            tmp_path = get_s3_cache_path(s3_key)
            if not tmp_path.exists():
                log.info(f"📥 Download từ S3 → disk cache: {s3_key}")
                s3.download_to_file(s3_key, tmp_path)
            else:
                log.info(f"📂 Dùng cache S3: {tmp_path}")
            return np.load(tmp_path, mmap_mode="r")
        except Exception as e:
            log.warning(f"S3 failed ({e}), fallback về local")

    if not local_path.exists():
        raise FileNotFoundError(f"❌ Không tìm thấy: S3={s3_key}, Local={local_path}")
    log.info(f"📂 Load local (mmap): {local_path}")
    return np.load(str(local_path), mmap_mode="r")

def load_s3_or_local_npy(s3_key: str, local_path: Path):
    settings = get_settings()
    if s3_key and settings.aws_access_key_id:
        try:
            from src.storage.aws.s3_client import get_s3_client
            log.info(f"📥 Download từ S3: {s3_key}")
            raw = get_s3_client().download_bytes(s3_key)
            return np.load(io.BytesIO(raw))
        except Exception as e:
            log.warning(f"S3 failed ({e}), fallback về local")

    if not local_path.exists():
        raise FileNotFoundError(f"❌ Không tìm thấy: S3={s3_key}, Local={local_path}")
    log.info(f"📂 Load local: {local_path}")
    return np.load(str(local_path))

def load_s3_or_local_csv(s3_key: str, local_path: Path):
    settings = get_settings()
    if s3_key and settings.aws_access_key_id:
        try:
            from src.storage.aws.s3_client import get_s3_client
            log.info(f"📥 Download từ S3: {s3_key}")
            text = get_s3_client().download_text(s3_key)
            return pd.read_csv(io.StringIO(text))
        except Exception as e:
            log.warning(f"S3 failed ({e}), fallback về local")

    if not local_path.exists():
        raise FileNotFoundError(f"❌ Không tìm thấy: S3={s3_key}, Local={local_path}")
    log.info(f"📂 Load local: {local_path}")
    return pd.read_csv(local_path)


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 0: Auto-discover nguồn dữ liệu
# ═════════════════════════════════════════════════════════════════════════════

def discover_horizon_dirs(s3_root: str, local_root: Path) -> list[tuple[int, str, Path]]:
    """
    Quét và trả về danh sách (horizon_int, s3_folder_key, local_folder_path).
    """
    results = []
    settings = get_settings()

    if settings.aws_access_key_id:
        try:
            from src.storage.aws.s3_client import get_s3_client
            s3 = get_s3_client()
            base_key = s3_root if s3_root.endswith("/") else f"{s3_root}/"
            
            s3_found = False
            for folder_name, horizon_int in sorted(HORIZON_MAP.items()):
                folder_key = f"{base_key}{folder_name}/"
                r_pred   = f"{folder_key}R_pred_series.npy"
                seg_ids  = f"{folder_key}segment_ids.npy"
                meta_csv = f"{folder_key}R_pred_meta.csv"
                
                if s3.key_exists(r_pred) and s3.key_exists(seg_ids) and s3.key_exists(meta_csv):
                    results.append((horizon_int, folder_key, local_root / folder_name))
                    log.info(f"  ✓ Tìm thấy horizon h{horizon_int} trên S3: {folder_key}")
                    s3_found = True
                else:
                    log.warning(f"  ⚠ Bỏ qua S3 {folder_key}: thiếu file bắt buộc")
            if s3_found:
                return results
            else:
                log.warning("Không tìm thấy dữ liệu trên S3 hợp lệ, fallback về local...")
        except Exception as e:
            log.warning(f"S3 failed ({e}), fallback về local...")

    for folder_name, horizon_int in sorted(HORIZON_MAP.items()):
        folder = local_root / folder_name
        if not folder.exists():
            continue
        r_pred   = folder / "R_pred_series.npy"
        seg_ids  = folder / "segment_ids.npy"
        meta_csv = folder / "R_pred_meta.csv"
        if not (r_pred.exists() and seg_ids.exists() and meta_csv.exists()):
            log.warning(f"  ⚠ Bỏ qua local {folder}: thiếu file bắt buộc")
            continue
        results.append((horizon_int, f"{s3_root}/{folder_name}/", folder))
        log.info(f"  ✓ Tìm thấy horizon h{horizon_int} ở local: {folder}")
        
    return results


def find_graph_structure(s3_pattern: str, local_pattern: str) -> tuple[str, Path]:
    settings = get_settings()
    if settings.aws_access_key_id:
        try:
            from src.storage.aws.s3_client import get_s3_client
            s3 = get_s3_client()
            prefix = s3_pattern.split("*")[0]
            latest_key = s3.get_latest_key(prefix, ".npz")
            if latest_key:
                log.info(f"  ✓ Tìm thấy graph_structure trên S3: {latest_key}")
                local_fallback = Path(local_pattern.replace("*", "latest"))
                return latest_key, local_fallback
            log.warning(f"Không tìm thấy S3 key cho prefix {prefix}, fallback về local...")
        except Exception as e:
            log.warning(f"S3 failed ({e}), fallback về local...")

    matches = sorted(Path(".").glob(local_pattern))
    if not matches:
        raise FileNotFoundError(
            f"Không tìm thấy graph_structure.npz (S3: {s3_pattern}, Local: {local_pattern})"
        )
    return "", matches[-1]


# ═════════════════════════════════════════════════════════════════════════════
# BƯỚC 1: Load dữ liệu cho 1 horizon
# ═════════════════════════════════════════════════════════════════════════════

def load_horizon_data(folder_key: str, folder_path: Path, gs_s3_key: str, gs_local_path: Path) -> dict:
    log.info(f"📦 Loading R_pred_series.npy ...")
    R_pred_series = load_s3_or_local_npy_mmap(f"{folder_key}R_pred_series.npy", folder_path / "R_pred_series.npy")
    log.info(f"  ✓ shape={R_pred_series.shape}, dtype={R_pred_series.dtype}")

    log.info(f"📦 Loading segment_ids.npy ...")
    seg_ids = load_s3_or_local_npy(f"{folder_key}segment_ids.npy", folder_path / "segment_ids.npy")
    log.info(f"  ✓ shape={seg_ids.shape}")

    log.info(f"📦 Loading R_pred_meta.csv ...")
    meta_df = load_s3_or_local_csv(f"{folder_key}R_pred_meta.csv", folder_path / "R_pred_meta.csv")

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
    gs_raw = load_s3_or_local_npz(gs_s3_key, gs_local_path)
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


def fetch_segment_to_edge_map(
    session: Session,
    seg_ids: np.ndarray,
    gs_s3_key: str,
    gs_local_path: Path,
) -> dict[int, uuid.UUID]:
    """
    Build map: segment_pos (vị trí trong seg_ids) → OSM edge UUID.

    Mapping logic (không dùng tomtom_segments — bảng đó thường trống):
      seg_ids[pos] = model_node_id (0..N-1)
      graph_structure['model_node_osm_u_id'][model_node_id] = u_osm
      graph_structure['model_node_osm_v_id'][model_node_id] = v_osm
      DB: edges JOIN nodes → (u_osm, v_osm) → edge UUID

    Trả về {pos: edge_uuid} — chỉ các pos có OSM edge tương ứng.
    """
    # 1. Load graph_structure để lấy (u_osm, v_osm) cho mỗi model_node_id
    gs_raw = load_s3_or_local_npz(gs_s3_key, gs_local_path)
    model_u = gs_raw["model_node_osm_u_id"]   # shape (N_model_segs,)
    model_v = gs_raw["model_node_osm_v_id"]   # shape (N_model_segs,)

    # 2. Lấy toàn bộ (u_osm, v_osm) → edge UUID từ DB
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

    # 3. Xây dựng map pos → edge UUID
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
) -> tuple[uuid.UUID, int]:
    """Insert 1 correlation_snapshot + node_correlations rows. Trả về (snapshot_id, n_corr_rows)."""
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
    return snapshot_id, len(corr_batch)


def insert_edge_correlations(
    session:       Session,
    snapshot_id:   uuid.UUID,
    R_t:           np.ndarray,          # (n_segs, n_segs) — R_t thô
    pos_to_edge:   dict[int, uuid.UUID], # segment_pos → edge UUID
    top_k:         int,
) -> int:
    """
    Insert top-K edge correlations từ R_t thô.
    Mỗi segment A → top-K segment B theo |corr| giảm dần.
    Bỏ qua self-correlation (A=B).
    Trả về số rows đã insert.
    """
    n_segs = R_t.shape[0]
    now = datetime.now(timezone.utc)
    edge_batch: list[dict] = []

    for seg_a in range(n_segs):
        corr_row = R_t[seg_a].copy().astype(np.float32)
        corr_row[seg_a] = -2.0   # loại self

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
    import time

    log.info("=" * 70)
    log.info("🚀 seed_correlation.py — DMFM Multi-Horizon Seeder")
    log.info("=" * 70)

    if args.data_root:
        data_root_str = str(args.data_root)
        if data_root_str.startswith("s3://"):
            s3_data_root = data_root_str.replace("s3://", "")
            local_data_root = Path("invalid_fallback")
        else:
            s3_data_root = "invalid_fallback"
            local_data_root = Path(data_root_str)
    else:
        s3_data_root = S3_DATA_ROOT
        local_data_root = LOCAL_DATA_ROOT

    if args.graph_structure:
        gs_str = str(args.graph_structure)
        if gs_str.startswith("s3://"):
            s3_gs_pattern = gs_str.replace("s3://", "")
            local_gs_pattern = "invalid_fallback"
        else:
            s3_gs_pattern = "invalid_fallback"
            local_gs_pattern = gs_str
    else:
        s3_gs_pattern = S3_GRAPH_PATTERN
        local_gs_pattern = str(LOCAL_GRAPH_PATTERN)

    # Tìm file graph_structure mới nhất
    gs_s3_key, gs_local_path = find_graph_structure(s3_gs_pattern, local_gs_pattern)
    log.info(f"📐 Graph structure: S3={gs_s3_key} | Local={gs_local_path}")

    # Auto-discover horizon folders
    log.info(f"🔍 Scanning DATA_ROOT: S3={s3_data_root} | Local={local_data_root}")
    horizon_dirs = discover_horizon_dirs(s3_data_root, local_data_root)
    if not horizon_dirs:
        raise RuntimeError(f"Không tìm thấy horizon folder nào. Cần ít nhất 1 trong: h1, h3, h6, h9")

    log.info(f"📊 Sẽ xử lý {len(horizon_dirs)} horizon(s): "
             f"{[f'h{h}' for h, _, _ in horizon_dirs]}")

    # Connect DB
    log.info("🔌 Connecting DB...")
    engine = create_engine(args.db_url, echo=False)

    with Session(engine) as session:
        # ── Dọn sạch toàn bộ dữ liệu cũ ─────────────────────────────────────
        log.info("🧹 Xóa dữ liệu correlation cũ...")
        deleted_ec = session.execute(text("DELETE FROM edge_correlations")).rowcount
        deleted_nc = session.execute(text("DELETE FROM node_correlations")).rowcount
        deleted_cs = session.execute(text("DELETE FROM correlation_snapshots")).rowcount
        session.commit()
        log.info(f"  ✓ Đã xóa {deleted_cs} snapshots, {deleted_nc:,} node_correlations, {deleted_ec:,} edge_correlations rows")

        # Load DB lookups (dùng chung cho tất cả horizons)
        osm_to_uuid, adj_set = fetch_db_lookups(session)

        total_snapshots      = 0
        total_corr_rows      = 0
        total_edge_corr_rows = 0
        grand_t0 = time.time()
        first_snapshot   = True   # snapshot đầu tiên trên tất cả horizons → is_active

        for horizon_int, folder_key, folder_path in horizon_dirs:
            log.info("")
            log.info(f"{'─'*70}")
            log.info(f"📂 Horizon h{horizon_int} — S3:{folder_key} | Local:{folder_path}")
            log.info(f"{'─'*70}")

            data = load_horizon_data(folder_key, folder_path, gs_s3_key, gs_local_path)
            R_pred_series = data["R_pred_series"]
            seg_ids       = data["seg_ids"]
            meta_df       = data["meta_df"]
            gs            = data["gs"]

            n_snapshots = R_pred_series.shape[0]

            # Build incident matrix (1 lần per horizon, seg_ids có thể khác)
            W, _, osm_node_ids = build_incident_matrix(gs, seg_ids)

            # Build segment → edge map (1 lần per horizon)
            pos_to_edge = fetch_segment_to_edge_map(
                session       = session,
                seg_ids       = seg_ids,
                gs_s3_key     = gs_s3_key,
                gs_local_path = gs_local_path,
            )

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

                snap_id, n_node_rows = insert_one_snapshot(
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

                n_edge_rows = insert_edge_correlations(
                    session     = session,
                    snapshot_id = snap_id,
                    R_t         = R_t,
                    pos_to_edge = pos_to_edge,
                    top_k       = args.top_k,
                )

                session.commit()
                total_snapshots      += 1
                total_corr_rows      += n_node_rows
                total_edge_corr_rows += n_edge_rows

                corr_vals = node_corr[node_corr < 1.0]
                log.info(
                    f"    ✓ {str(snap_id)[:8]}... | "
                    f"node_corr={n_node_rows:,} rows | "
                    f"edge_corr={n_edge_rows:,} rows | "
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
        log.info(f"   ✔ {total_corr_rows:,} rows → node_correlations")
        log.info(f"   ✔ {total_edge_corr_rows:,} rows → edge_correlations")
        log.info(f"   ⏱ Tổng: {grand_total:.0f}s = {grand_total/60:.1f} phút")
        log.info(f"   🌟 Active snapshot: pred_idx=0 của h{horizon_dirs[0][0]}")
        log.info("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed DMFM correlation từ ml_workspace/data/dmfm_predictions/test vào PostgreSQL"
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
        help="Path tới graph_structure_*.npz (S3 URL hoặc local). Mặc định thử S3 trước, fallback local.",
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