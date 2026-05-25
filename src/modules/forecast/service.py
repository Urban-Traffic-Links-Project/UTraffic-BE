"""
src/modules/forecast/service.py

DMFM Forecast Service — dự báo ma trận tương quan tại T+h.

Luồng:
  1. load_dmfm_model()       → load dmfm_model.npz (lru_cache, 1 lần)
  2. load_dmfm_meta()        → đọc h1/R_pred_meta.csv → dict (date, slot) → pred_idx
  3. get_forecast_for_node() → lấy R_origin tại T từ bundle h1,
                               gọi dmfm_predict(model, R_origin, lag=horizon)
                               → trả về neighbors có tương quan cao nhất với node

Horizon 0  → lag=0  → A^0=I → trả về R_origin (tương quan thực tế tại T)
Horizon h  → lag=h  → A^h   → dự báo tương quan tại T + h×15p
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import Session

from src.core.config import get_settings


# ─── Helpers (ported từ ML script) ────────────────────────────────────────────

def _nearest_corr(R: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Ép ma trận về PSD correlation hợp lệ."""
    A = np.asarray(R, dtype=np.float64)
    A = 0.5 * (A + A.T)
    np.fill_diagonal(A, 1.0)
    vals, vecs = np.linalg.eigh(A)
    vals = np.clip(vals, eps, None)
    A_psd = (vecs * vals) @ vecs.T
    d = np.sqrt(np.clip(np.diag(A_psd), eps, None))
    A_corr = A_psd / np.outer(d, d)
    A_corr = np.clip(A_corr, -1.0, 1.0)
    A_corr = 0.5 * (A_corr + A_corr.T)
    np.fill_diagonal(A_corr, 1.0)
    return A_corr.astype(np.float32)


def _corr_to_vec(R: np.ndarray) -> np.ndarray:
    """Lấy tam giác trên của ma trận tương quan thành vector."""
    iu = np.triu_indices(R.shape[0], k=1)
    return np.asarray(R, dtype=np.float32)[iu]


def _vec_to_corr(vec: np.ndarray, n: int) -> np.ndarray:
    """Phục hồi ma trận đối xứng từ vector tam giác trên."""
    iu = np.triu_indices(n, k=1)
    R = np.eye(n, dtype=np.float32)
    R[iu] = vec.astype(np.float32)
    R[(iu[1], iu[0])] = vec.astype(np.float32)
    return _nearest_corr(R)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R_earth = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2 * R_earth * math.asin(math.sqrt(a))


# ─── Model loading ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_dmfm_model() -> dict[str, Any] | None:
    """
    Load dmfm_model.npz từ ml_workspace/data/dmfm_model/dmfm_model.npz.
    Cached vào memory — chỉ load 1 lần khi server khởi động.

    Cấu trúc NPZ:
        n           → [N] số segment
        rank        → [k] số factor
        mean_vec    → (P,) mean của upper-triangular corr vector
        components  → (P, k) latent components
        A           → (k, k) VAR(1) transition matrix
        segment_ids → (N,) model segment IDs

    Predict: vec_pred = mean_vec + (corr_to_vec(R_t) - mean_vec) @ components @ A^h @ components.T
    """
    settings = get_settings()
    base_dir = Path(settings.ml_workspace_path).resolve()
    if base_dir.name != "data":
        base_dir = base_dir / "data"

    # Tìm dmfm_model.npz
    matches = list(base_dir.rglob("dmfm_model.npz"))
    if not matches:
        print("[DMFM] ⚠️ dmfm_model.npz không tìm thấy trong ml_workspace")
        return None

    model_path = matches[0]
    print(f"[DMFM] 📂 Loading model: {model_path}")

    try:
        npz = np.load(model_path, allow_pickle=False)
        N = int(npz["n"][0]) if npz["n"].ndim > 0 else int(npz["n"])
        k = int(npz["rank"][0]) if npz["rank"].ndim > 0 else int(npz["rank"])
        mean_vec    = npz["mean_vec"].astype(np.float32)
        components  = npz["components"].astype(np.float32)   # (P, k)
        A           = npz["A"].astype(np.float32)             # (k, k)
        segment_ids = npz["segment_ids"].astype(np.int64)

        # Pre-compute A^h cho h=0..9 để tránh tính lại mỗi request
        A_pows: dict[int, np.ndarray] = {}
        for h in range(10):
            A_pows[h] = np.linalg.matrix_power(A.astype(np.float64), h).astype(np.float32)

        print(f"[DMFM] ✅ Model loaded: N={N}, rank={k}, A cached h=0..9")
        return {
            "N": N,
            "k": k,
            "mean_vec": mean_vec,
            "components": components,
            "A": A,
            "A_pows": A_pows,
            "segment_ids": segment_ids,
            "n_segments": N,  # alias để tương thích vec_to_corr
        }
    except Exception as e:
        print(f"[DMFM] ❌ Load error: {e}")
        return None


@lru_cache(maxsize=1)
def load_dmfm_meta() -> dict[tuple[str, str], int] | None:
    """
    Đọc h1/R_pred_meta.csv → dict { (date, slot) → pred_idx }.
    Dùng h1 làm reference vì cùng R_origin cho mọi horizon.

    Ví dụ: ("2024-08-26", "Slot_1100") → 0
    """
    settings = get_settings()
    base_dir = Path(settings.ml_workspace_path).resolve()
    if base_dir.name != "data":
        base_dir = base_dir / "data"

    meta_matches = list(base_dir.rglob("dmfm_model/h1/R_pred_meta.csv"))
    if not meta_matches:
        print("[DMFM] ⚠️ h1/R_pred_meta.csv không tìm thấy")
        return None

    meta_path = meta_matches[0]
    df = pd.read_csv(meta_path)
    result: dict[tuple[str, str], int] = {}
    for _, row in df.iterrows():
        date_str = str(row["date"])       # "2024-08-26"
        slot_str = str(row["time_set_id"])  # "Slot_1100"
        pred_idx = int(row["pred_idx"])
        result[(date_str, slot_str)] = pred_idx

    print(f"[DMFM] ✅ Meta loaded: {len(result)} (date, slot) entries")
    return result


def _get_available_dates_slots() -> dict:
    """Trả về danh sách dates và slots có trong meta."""
    meta = load_dmfm_meta()
    if not meta:
        return {"dates": [], "slots": [], "snapshots": [], "total": 0}

    dates_set: set[str] = set()
    slots_set: set[str] = set()
    snapshots = []
    for (date, slot) in meta.keys():
        dates_set.add(date)
        slots_set.add(slot)
        snapshots.append({
            "date": date,
            "slot": slot,
            "mode": f"{date}_{slot}"
        })

    return {
        "dates": sorted(dates_set),
        "slots": sorted(slots_set),
        "snapshots": snapshots,
        "total": len(meta),
        "available_horizons": list(range(0, 10)),  # h=0..9 (0..135p bước 15p)
        "horizon_labels": {
            0: "Tại T (0p)",
            1: "T+1 (+15p)",
            2: "T+2 (+30p)",
            3: "T+3 (+45p)",
            4: "T+4 (+60p)",
            5: "T+5 (+75p)",
            6: "T+6 (+90p)",
            7: "T+7 (+105p)",
            8: "T+8 (+120p)",
            9: "T+9 (+135p)",
        },
    }


# ─── Horizon → bundle mapping ─────────────────────────────────────────────────

# Horizon có pre-computed bundle
PRECOMPUTED_HORIZONS = {1, 3, 6, 9}

@lru_cache(maxsize=1)
def _load_r_series_test(base_dir_str: str) -> np.ndarray | None:
    """
    Load R_series test split (ma trận gốc tại T) để lấy R_origin.
    Dùng memory-mapped array (mmap_mode='r') để tránh load 1.6GB+ vào RAM.
    """
    base_dir = Path(base_dir_str)
    # Tìm R_series.npy của test split
    candidates = list(base_dir.rglob("05_branchA_prepare_segment_segment_rt/test/R_series.npy"))
    if not candidates:
        # Search upwards for the ML directory (for development workspace where ML is a sibling of BE)
        curr = base_dir
        for _ in range(5):
            if (curr / "ML").exists():
                candidates = list((curr / "ML").rglob("05_branchA_prepare_segment_segment_rt/test/R_series.npy"))
                if candidates:
                    break
            curr = curr.parent
    if not candidates:
        return None
    path = candidates[0]
    print(f"[DMFM] 📂 Memory-mapping R_series test: {path}")
    return np.load(path, mmap_mode="r")


def _load_r_bundle(base_dir: Path, horizon: int, pred_idx: int) -> np.ndarray | None:
    """
    Load ma trận R_pred từ bundle đã pre-computed.
    Dùng cho horizon ∈ {1, 3, 6, 9}.
    """
    bundle_name = f"dmfm_pred_test_h{horizon}_idx{pred_idx:06d}.npz"
    bundle_path = base_dir / "dmfm_model" / f"h{horizon}" / "bundles" / bundle_name
    if not bundle_path.exists():
        return None
    npz = np.load(bundle_path, allow_pickle=False)
    return npz["R_pred"].astype(np.float32)  # (N, N) float32


def _load_r_origin(base_dir: Path, source_sample_id: int) -> np.ndarray | None:
    """
    Load ma trận tương quan R tại T (nguồn) từ h1 bundles.
    Dùng bundle h1 tại source_sample_id—đây chính là R_origin (R tại T trước khi predict).
    Thực ra ta dùng source_sample_id để tìm đúng bundle h1.

    Cách khác: đọc trực tiếp từ R_series.npy test split bằng memory-map.
    Nhưng để đơn giản, ta dùng bundle h1 chứa R tại T+1 và revert bằng model.
    """
    # Thực ra, bundle h1 đã chứa R_pred tại T+1, không phải R_origin.
    # R_origin = R_series_test[source_sample_id].
    # Ta dùng memory-mapped R_series để lấy R_origin chính xác.
    r_series = _load_r_series_test(str(base_dir))
    if r_series is None or source_sample_id >= r_series.shape[0]:
        return None
    return np.asarray(r_series[source_sample_id], dtype=np.float32)



def _dmfm_predict_online(model: dict, R_origin: np.ndarray, lag: int) -> np.ndarray:
    """
    Predict ma trận tương quan tại T+lag từ R_origin (ma trận tại T).

    lag=0 → A^0=I → trả về R_origin (không predict, chỉ decode lại)
    lag>0 → predict online dùng factor model

    Công thức:
        vec_t = corr_to_vec(R_origin)
        score_t = (vec_t - mean_vec) @ components          # (1, k)
        score_t+h = score_t @ A^h                          # (1, k)
        vec_t+h = mean_vec + score_t+h @ components.T      # (P,)
        R_t+h = vec_to_corr(vec_t+h)
    """
    N = model["n_segments"]
    mean_vec   = model["mean_vec"]    # (P,)
    components = model["components"]  # (P, k)
    A_pows     = model["A_pows"]

    vec_t = _corr_to_vec(R_origin)             # (P,)
    score_t = (vec_t - mean_vec) @ components   # (k,)

    A_pow = A_pows.get(lag, np.linalg.matrix_power(model["A"].astype(np.float64), lag).astype(np.float32))
    score_th = score_t @ A_pow                  # (k,)

    vec_th = mean_vec + score_th @ components.T  # (P,)
    return _vec_to_corr(vec_th, N)


def _get_node_spatial(session: Session, osm_node_id: int) -> dict | None:
    """Lấy tọa độ + tên đường của node từ DB."""
    row = session.execute(
        text("SELECT id, node_index, lat, lon, street_name FROM nodes WHERE osm_node_id = :osm_id"),
        {"osm_id": osm_node_id},
    ).mappings().first()
    if not row:
        return None
    return {
        "node_id":     str(row["id"]),
        "osm_node_id": osm_node_id,
        "node_index":  row["node_index"],
        "lat":         float(row["lat"]),
        "lon":         float(row["lon"]),
        "street_name": (row["street_name"] or "").strip() or f"Node #{row['node_index']}",
    }


def _get_all_nodes_spatial(session: Session) -> dict[int, dict]:
    """Lấy toàn bộ nodes từ DB → dict osm_node_id → spatial info."""
    rows = session.execute(
        text("SELECT osm_node_id, node_index, lat, lon, street_name FROM nodes")
    ).mappings().all()
    return {
        row["osm_node_id"]: {
            "osm_node_id": row["osm_node_id"],
            "node_index":  row["node_index"],
            "lat":         float(row["lat"]),
            "lon":         float(row["lon"]),
            "street_name": (row["street_name"] or "").strip() or f"Node #{row['node_index']}",
        }
        for row in rows
    }


def _get_adjacent_set(session: Session) -> set[tuple[int, int]]:
    """Lấy tập các cặp (osm_u, osm_v) có edge trực tiếp."""
    rows = session.execute(
        text("""
            SELECT n_src.osm_node_id as u, n_tgt.osm_node_id as v
            FROM edges e
            JOIN nodes n_src ON n_src.id = e.source_node_id
            JOIN nodes n_tgt ON n_tgt.id = e.target_node_id
        """)
    ).mappings().all()
    adj: set[tuple[int, int]] = set()
    for r in rows:
        adj.add((r["u"], r["v"]))
        adj.add((r["v"], r["u"]))
    return adj


def _slot_to_next(slot: str, horizon: int) -> str:
    """
    Tính slot tại T + horizon×15 phút.
    Ví dụ: slot="Slot_1100", horizon=3 → "Slot_1145"
    """
    code = slot.replace("Slot_", "")  # "1100"
    h = int(code[:2])
    m = int(code[2:])
    total_min = h * 60 + m + horizon * 15
    # Clip về 0–23:59
    total_min = total_min % (24 * 60)
    nh = total_min // 60
    nm = total_min % 60
    return f"Slot_{nh:02d}{nm:02d}"


# ─── Public API ────────────────────────────────────────────────────────────────

def list_forecast_snapshots() -> dict:
    """Trả về danh sách (date, slot) có thể dự báo."""
    return _get_available_dates_slots()


def get_forecast_for_node(
    session: Session,
    osm_node_id: int,
    date: str,          # "2024-08-26"
    slot: str,          # "Slot_1100"
    horizon: int,       # 0..9
    max_dist_m: float | None = None,
    min_corr: float | None = None,
    top_k: int = 20,
) -> dict:
    """
    Dự báo tương quan tại T+horizon×15p cho node osm_node_id.

    horizon=0 → trả về ma trận tương quan thực tế tại T (R_origin, lag=0)
    horizon>0 → predict online: dmfm_predict(model, R_origin, lag=horizon)
    """
    # 1. Kiểm tra model và meta
    model = load_dmfm_model()
    if model is None:
        raise HTTPException(status_code=503, detail="DMFM model chưa được load. Kiểm tra ml_workspace.")

    meta = load_dmfm_meta()
    if meta is None:
        raise HTTPException(status_code=503, detail="DMFM meta chưa được load.")

    # 2. Tìm pred_idx cho (date, slot)
    key = (date, slot)
    if key not in meta:
        raise HTTPException(
            status_code=404,
            detail=f"Không có dữ liệu DMFM cho {date} {slot}. Xem /api/v1/forecast/snapshots.",
        )
    pred_idx = meta[key]

    # 3. Tìm vị trí node trong segment_ids
    seg_ids = model["segment_ids"]   # (N,) model segment indices (là edge/node ids trong ML)
    N = model["N"]

    # 4. Load base_dir
    settings = get_settings()
    base_dir = Path(settings.ml_workspace_path).resolve()
    if base_dir.name != "data":
        base_dir = base_dir / "data"

    # 5. Đọc source_sample_id từ meta CSV (cần để lấy R_origin)
    #    Meta của h1: pred_idx → source_sample_id (index trong R_series test)
    meta_path_h1 = list(base_dir.rglob("dmfm_model/h1/R_pred_meta.csv"))[0]
    meta_df = pd.read_csv(meta_path_h1)
    row_info = meta_df[meta_df["pred_idx"] == pred_idx]
    source_sample_id = int(row_info["source_sample_id"].iloc[0]) if len(row_info) > 0 else pred_idx

    # 6. Quyết định R_pred theo horizon
    #    - horizon=0         → R_origin (tương quan thực tế tại T, từ R_series test)
    #    - horizon ∈ {1,3,6,9} → Pre-computed bundle (nhanh, chính xác)
    #    - horizon ∈ {2,4,5,7,8} → Online predict từ R_origin
    N = model["N"]

    if horizon == 0:
        # Lấy R_origin từ R_series test (memory-mapped)
        R_origin = _load_r_origin(base_dir, source_sample_id)
        if R_origin is None:
            raise HTTPException(
                status_code=503,
                detail="Không tìm thấy R_series test split. Cần file: 05_branchA_prepare_segment_segment_rt/test/R_series.npy",
            )
        # Sử dụng mô hình DMFM với lag = 0 để khử nhiễu SVD cho dữ liệu thực tế tại T
        R_pred = _dmfm_predict_online(model, R_origin, lag=0)
        source = "historical_rseries_denoised"
    elif horizon in PRECOMPUTED_HORIZONS:
        # Dùng bundle đã pre-computed
        R_pred = _load_r_bundle(base_dir, horizon, pred_idx)
        if R_pred is None:
            raise HTTPException(
                status_code=404,
                detail=f"Bundle h{horizon}/bundles/dmfm_pred_test_h{horizon}_idx{pred_idx:06d}.npz không tìm thấy.",
            )
        source = f"precomputed_h{horizon}"
    else:
        # Online predict từ R_origin
        R_origin = _load_r_origin(base_dir, source_sample_id)
        if R_origin is None:
            raise HTTPException(
                status_code=503,
                detail="Không tìm thấy R_series test split để predict online.",
            )
        R_pred = _dmfm_predict_online(model, R_origin, horizon)
        source = "dmfm_online"

    if R_pred.shape[0] != N:
        raise HTTPException(
            status_code=500,
            detail=f"R_pred shape {R_pred.shape} không khớp N={N}.",
        )


    # segment_ids trong model là edge model indices, ta cần ánh xạ sang DB osm_node_id
    # Cách: dùng matrix_axis.csv để biết segment_id[i] là edge nào,
    # sau đó dùng DB để lấy osm_node_id
    # Để đơn giản hóa: ta lấy row tương quan của node theo node_index trong model

    # Lấy spatial info của tất cả nodes từ DB
    node_a_info = _get_node_spatial(session, osm_node_id)
    if not node_a_info:
        raise HTTPException(status_code=404, detail=f"Node osm_node_id={osm_node_id} không tồn tại trong DB.")

    all_nodes_spatial = _get_all_nodes_spatial(session)
    adj_set = _get_adjacent_set(session)

    # 7. Dùng node_index làm matrix index
    node_a_idx = node_a_info["node_index"]
    if node_a_idx >= N:
        raise HTTPException(
            status_code=400,
            detail=f"node_index={node_a_idx} vượt quá kích thước model N={N}.",
        )

    # Lấy hàng tương quan của node A từ R_pred
    corr_row = R_pred[node_a_idx, :]  # (N,)

    # 8. Build neighbors
    lat_a = node_a_info["lat"]
    lon_a = node_a_info["lon"]

    neighbors = []
    for nb_osm_id, nb_info in all_nodes_spatial.items():
        if nb_osm_id == osm_node_id:
            continue
        nb_idx = nb_info["node_index"]
        if nb_idx >= N:
            continue

        corr_val = float(corr_row[nb_idx])
        abs_corr = abs(corr_val)

        if min_corr is not None and abs_corr < min_corr:
            continue

        dist_m = _haversine_m(lat_a, lon_a, nb_info["lat"], nb_info["lon"])
        if max_dist_m is not None and dist_m > max_dist_m:
            continue

        is_adj = (osm_node_id, nb_osm_id) in adj_set

        neighbors.append({
            "osm_node_id": nb_osm_id,
            "node_index":  nb_idx,
            "lat":         nb_info["lat"],
            "lon":         nb_info["lon"],
            "street_name": nb_info["street_name"],
            "corr":        round(corr_val, 6),
            "dist_m":      round(dist_m, 1),
            "is_adjacent": is_adj,
        })

    # Sắp xếp theo |corr| giảm dần
    neighbors.sort(key=lambda x: -abs(x["corr"]))

    # Rank
    for i, nb in enumerate(neighbors):
        nb["rank"] = i + 1

    neighbors = neighbors[:top_k]

    # 9. Tính predicted_time
    predicted_slot = _slot_to_next(slot, horizon)

    return {
        "base_time":       f"{date}_{slot}",
        "base_date":       date,
        "base_slot":       slot,
        "predicted_time":  f"{date}_{predicted_slot}",
        "predicted_slot":  predicted_slot,
        "horizon":         horizon,
        "horizon_minutes": horizon * 15,
        "source":          source,
        "pred_idx":        pred_idx,
        "selected_node":   node_a_info,
        "neighbors":       neighbors,
        "total":           len(neighbors),
    }
