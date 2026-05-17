import pickle
from pathlib import Path

pkl_path = Path("ml_workspace/data/sparse_tvpvar_gt_model/sparse_tvpvar_gt_model/sparse_tvpvar_g_model.pkl")
if pkl_path.exists():
    with open(pkl_path, "rb") as f:
        model = pickle.load(f)
    print("Available horizons:", list(model["coef_by_h"].keys()))
    print("N segments:", model["n_segments"])
else:
    print("Model not found")
