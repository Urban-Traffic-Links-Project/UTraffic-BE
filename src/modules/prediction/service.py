from __future__ import annotations
import uuid
import numpy as np
import pickle
from pathlib import Path
from sqlalchemy import text
from sqlmodel import Session
from fastapi import HTTPException
from functools import lru_cache
from src.core.config import get_settings
from src.storage.aws.s3_client import get_s3_client

@lru_cache()    
def load_prediction_model():
    settings = get_settings()
    # Đảm bảo đường dẫn tới ml_workspace/data chính xác
    base_dir = Path(settings.ml_workspace_path).resolve()
    if base_dir.name != "data":
        base_dir = base_dir / "data"

    # Sử dụng rglob để tự động tìm file ở local bất kể cấp thư mục lồng nhau
    pkl_matches = list(base_dir.rglob("sparse_tvpvar_g_model.pkl"))
    seg_matches = list(base_dir.rglob("segment_ids.npy"))
    
    pkl_path = pkl_matches[0] if pkl_matches else base_dir / "sparse_tvpvar_gt_model" / "sparse_tvpvar_g_model.pkl"
    seg_path = seg_matches[0] if seg_matches else base_dir / "sparse_tvpvar_gt_model" / "segment_ids.npy"
    
    # S3 Keys
    S3_MODEL_KEY = "ml-data/sparse_tvpvar_gt_model/sparse_tvpvar_g_model.pkl"
    S3_SEG_KEY   = "ml-data/sparse_tvpvar_gt_model/segment_ids.npy"
    S3_GS_PREFIX = "ml-data/"

    # Tự động tải từ S3 nếu thiếu file hoàn toàn ở local
    if settings.aws_access_key_id and not pkl_matches:
        try:
            s3 = get_s3_client()
            if not pkl_path.exists():
                print(f"📥 Downloading model from S3...")
                s3.download_to_file(S3_MODEL_KEY, pkl_path)
            if not seg_path.exists():
                print(f"📥 Downloading segments from S3...")
                s3.download_to_file(S3_SEG_KEY, seg_path)
        except Exception as e:
            print(f"⚠️ S3 Download failed: {e}")

    # Tìm file graph_structure động
    gs_matches = sorted(base_dir.glob("graph_structure_*.npz"))
    if not gs_matches:
         gs_matches = sorted(base_dir.rglob("graph_structure_*.npz"))
    gs_path = gs_matches[-1] if gs_matches else None
    
    # Nếu vẫn thiếu GS, thử tải cái mới nhất từ S3
    if not gs_path and settings.aws_access_key_id:
        try:
            s3 = get_s3_client()
            latest_gs_key = s3.get_latest_key(S3_GS_PREFIX, ".npz")
            if latest_gs_key:
                gs_path = base_dir / latest_gs_key.split("/")[-1]
                print(f"📥 Downloading graph structure from S3...")
                s3.download_to_file(latest_gs_key, gs_path)
        except:
            pass

    if not pkl_path.exists() or not gs_path or not seg_path.exists():
        # Log lỗi chi tiết
        print(f"[Model Load Error] Missing files after S3 check:")
        print(f"  PKL: {pkl_path.exists()} (at {pkl_path})")
        print(f"  Seg: {seg_path.exists()} (at {seg_path})")
        print(f"  GS:  {bool(gs_path)} (found {len(gs_matches)} files)")
        return None
        
    with open(pkl_path, "rb") as f:
        model = pickle.load(f)
        
    gs = np.load(gs_path)
    seg_ids = np.load(seg_path)
    
    return {
        "model": model,
        "u_arr": gs["model_node_osm_u_id"],
        "v_arr": gs["model_node_osm_v_id"],
        "seg_ids": seg_ids,
        "N": model["n_segments"]
    }

def get_influence_matrix(model_data, horizon: int):
    model = model_data["model"]
    N = model_data["N"]
    
    h_data = model["coef_by_h"].get(horizon)
    if not h_data:
        h_data = model["coef_by_h"][1]
        
    mean = model["mean"]
    comps = model["components"]
    intercept = h_data["intercept"]
    
    # G_h = mean + intercept @ components
    x_pred = mean + intercept @ comps
    G = x_pred.reshape(N, N)
    np.fill_diagonal(G, 0.0)
    return G

def get_db_edge_mapping(session: Session):
    q = text("""
        SELECT e.id as edge_id,
               n_src.osm_node_id as u_osm,
               n_tgt.osm_node_id as v_osm,
               (n_src.lat + n_tgt.lat) / 2.0 as lat,
               (n_src.lon + n_tgt.lon) / 2.0 as lng,
               COALESCE(n_src.street_name, n_tgt.street_name, '') as street_name
        FROM edges e
        JOIN nodes n_src ON n_src.id = e.source_node_id
        JOIN nodes n_tgt ON n_tgt.id = e.target_node_id
    """)
    rows = session.execute(q).mappings().all()
    uv_to_edge = {}
    edge_details = {}
    for r in rows:
        uv = (r["u_osm"], r["v_osm"])
        uv_to_edge[uv] = r["edge_id"]
        street = (r["street_name"] or "").strip() or "Không rõ tên đường"
        edge_details[r["edge_id"]] = {
            "name": street,
            "lat": float(r["lat"]),
            "lng": float(r["lng"])
        }
    return uv_to_edge, edge_details

def predict_spread_for_incident(session: Session, incident_id: uuid.UUID, horizon: int = 1, mode: str = "spread", radius: float = 3.0): 
    model_data = load_prediction_model()
    if not model_data:
        raise HTTPException(status_code=500, detail="Prediction model not found on server")
        
    q = text("SELECT edge_id FROM incident_edges WHERE incident_id = :inc_id")
    edge_rows = session.execute(q, {"inc_id": str(incident_id)}).mappings().all()
    
    if not edge_rows:
        # Fallback: Find the nearest edge globally using nodes lat/lng
        fallback_q = text("""
            SELECT e.id as edge_id
            FROM edges e
            JOIN nodes n_src ON n_src.id = e.source_node_id
            JOIN nodes n_tgt ON n_tgt.id = e.target_node_id,
                 incidents i
            WHERE i.id = :inc_id
            ORDER BY ST_MakeLine(
                ST_SetSRID(ST_MakePoint(n_src.lon, n_src.lat), 4326),
                ST_SetSRID(ST_MakePoint(n_tgt.lon, n_tgt.lat), 4326)
            )::geography <-> i.geom::geography
            LIMIT 1
        """)
        edge_rows = session.execute(fallback_q, {"inc_id": str(incident_id)}).mappings().all()
        
    if not edge_rows:
        return [], None
        
    incident_edge_ids = [r["edge_id"] for r in edge_rows]
    
    uv_to_edge, edge_details = get_db_edge_mapping(session)
    edge_to_uv = {v: k for k, v in uv_to_edge.items()}

    # Lấy vị trí thực tế của sự cố để trùng với Marker trên UI (sử dụng ST_Centroid nếu là LineString)
    pos_q = text("SELECT ST_Y(ST_Centroid(geom)) as lat, ST_X(ST_Centroid(geom)) as lng FROM incidents WHERE id = :inc_id")
    pos_row = session.execute(pos_q, {"inc_id": str(incident_id)}).mappings().first()
    
    def get_val(row, key, default):
        if row and row[key] is not None: return float(row[key])
        return default

    center_lat = get_val(pos_row, "lat", (edge_details[incident_edge_ids[0]]["lat"] if incident_edge_ids and incident_edge_ids[0] in edge_details else 10.77))
    center_lng = get_val(pos_row, "lng", (edge_details[incident_edge_ids[0]]["lng"] if incident_edge_ids and incident_edge_ids[0] in edge_details else 106.69))

    
    G = get_influence_matrix(model_data, horizon)
    seg_ids = model_data["seg_ids"]
    u_arr = model_data["u_arr"]
    v_arr = model_data["v_arr"]
    
    ml_to_edge = {}
    edge_to_ml = {}
    for pos, s_id in enumerate(seg_ids):
        u = u_arr[s_id]
        v = v_arr[s_id]
        if (u, v) in uv_to_edge:
            e_id = uv_to_edge[(u, v)]
            ml_to_edge[pos] = e_id
            edge_to_ml[e_id] = pos
            
    source_ml_indices = [edge_to_ml[e] for e in incident_edge_ids if e in edge_to_ml]
    if not source_ml_indices:
        return [], None
        
    influence = np.zeros(model_data["N"], dtype=np.float32)
    for src in source_ml_indices:
        if mode == "spread":
            # Cột src: src tác động lên ai?
            influence += G[:, src]
        else:
            # Hàng src: ai tác động lên src?
            influence += G[src, :]
        
    for src in source_ml_indices:
        influence[src] = 0.0
        
    top_indices = np.argsort(influence)[::-1][:20]
    
    affected_items = []
    arrows = []
    
    # Xóa các biến center_lat cũ ở đây

    
    for idx in top_indices:
        score = float(influence[idx])
        if score <= 0.01:
            break
            
        if idx in ml_to_edge:
            e_id = ml_to_edge[idx]
            det = edge_details[e_id]
            
            # Filter by distance (dynamic radius) to keep it realistic
            dist = ((det["lat"] - center_lat)**2 + (det["lng"] - center_lng)**2)**0.5 * 111 # rough km
            if dist > radius:
                continue

            level = "Cao" if score > 0.5 else "Trung bình" if score > 0.2 else "Thấp"
            affected_items.append({
                "segmentId": str(e_id),
                "name": det["name"],
                "level": level,
                "score": round(score, 4),
                "lat": det["lat"],
                "lng": det["lng"],
            })
            
            if mode == "spread":
                arrows.append({
                    "from": {"lat": center_lat, "lng": center_lng},
                    "to": {"lat": det["lat"], "lng": det["lng"]},
                    "weight": min(1.0, score),
                    "mode": "spread"
                })
            else:
                # Nguyên nhân: mũi tên hướng VÀO điểm kết xe
                arrows.append({
                    "from": {"lat": det["lat"], "lng": det["lng"]},
                    "to": {"lat": center_lat, "lng": center_lng},
                    "weight": min(1.0, score),
                    "mode": "cause"
                })
            
    spread_map_data = {
        "center": {"lat": center_lat, "lng": center_lng},
        "rings": [
            {"radiusKm": 0.5, "intensity": 0.8},
            {"radiusKm": 1.0, "intensity": 0.5},
            {"radiusKm": 2.0, "intensity": 0.2}
        ],
        "arrows": arrows
    }
    
    return affected_items, spread_map_data
