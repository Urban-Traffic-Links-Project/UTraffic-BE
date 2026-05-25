"""
src/modules/forecast/router.py

Forecast API — dự báo tương quan tại T+h dùng DMFM model.

Endpoints:
  GET /api/v1/forecast/snapshots
      → Trả về danh sách (date, slot) có dữ liệu DMFM và các horizon khả dụng.

  GET /api/v1/forecast/node/{osm_node_id}
      ?date=2024-08-26
      &slot=Slot_1100
      &horizon=3          # 0..9 (tương ứng 0..135 phút, bước 15p)
      &max_dist_m=1000    # optional
      &min_corr=0.3       # optional
      → Dự báo top-K neighbors có tương quan cao nhất tại T+horizon×15p.

Cache: Redis DB 2 (redis_pred), TTL 60 giây.
Key pattern: forecast:{osm_node_id}:{date}:{slot}:h{horizon}:d{dist|all}:c{corr|0}
"""
import hashlib

from fastapi import APIRouter, Query

from src.api.dependencies import DbSession
from src.integrations.cache_helpers import get_json_cache, set_json_cache
from src.integrations.redis_client import get_redis_pred
from src.modules.forecast import service

router = APIRouter(prefix="/forecast", tags=["Forecast"])

FORECAST_CACHE_TTL = 60  # 60 giây — dữ liệu tĩnh (pre-computed bundles)
SNAPSHOTS_CACHE_TTL = 300  # 5 phút


@router.get("/snapshots")
async def get_forecast_snapshots():
    """
    Trả về danh sách (date, slot) có trong DMFM dataset và horizon labels.

    Response:
    ```json
    {
      "dates": ["2024-08-26", "2024-08-27", ...],
      "slots": ["Slot_0815", "Slot_0830", ...],
      "total": 64,
      "available_horizons": [0, 1, 2, ..., 9],
      "horizon_labels": {
        "0": "Tại T (0p)",
        "1": "T+1 (+15p)",
        ...
        "9": "T+9 (+135p)"
      }
    }
    ```
    """
    redis = get_redis_pred()
    cache_key = "forecast:snapshots"

    cached = await get_json_cache(redis, cache_key)
    if cached is not None:
        return cached

    result = service.list_forecast_snapshots()
    await set_json_cache(redis, cache_key, result, SNAPSHOTS_CACHE_TTL)
    return result


@router.get("/node/{osm_node_id}")
async def get_forecast_node(
    osm_node_id: int,
    session: DbSession,
    date: str = Query(..., description="Ngày tại T, VD: '2024-08-26'"),
    slot: str = Query(..., description="Slot tại T, VD: 'Slot_1100'"),
    horizon: int = Query(
        default=1,
        ge=0,
        le=9,
        description="Horizon dự báo: 0=Tại T, 1=+15p, 2=+30p, ..., 9=+135p",
    ),
    max_dist_m: float | None = Query(default=None, description="Giới hạn khoảng cách (m)"),
    min_corr: float | None = Query(default=None, ge=0.0, le=1.0, description="|corr| tối thiểu"),
    top_k: int = Query(default=20, ge=1, le=50, description="Số lượng neighbors tối đa"),
):
    """
    Dự báo tương quan tại T+horizon×15p cho node được chọn.

    - **horizon=0**: Trả về tương quan thực tế tại T (không predict)
    - **horizon=1..9**: DMFM online predict từ R_origin tại T
    
    Nhãn horizon:
    | horizon | Thời gian |
    |---------|-----------|
    | 0       | Tại T (0p) |
    | 1       | +15p |
    | 2       | +30p |
    | 3       | +45p |
    | 4       | +60p |
    | 5       | +75p |
    | 6       | +90p |
    | 7       | +105p |
    | 8       | +120p |
    | 9       | +135p |
    """
    redis = get_redis_pred()
    dist_key = int(max_dist_m * 10) if max_dist_m is not None else "all"
    corr_key = int((min_corr or 0.0) * 100)
    cache_key = f"forecast:{osm_node_id}:{date}:{slot}:h{horizon}:d{dist_key}:c{corr_key}:k{top_k}"

    cached = await get_json_cache(redis, cache_key)
    if cached is not None:
        return cached

    result = service.get_forecast_for_node(
        session=session,
        osm_node_id=osm_node_id,
        date=date,
        slot=slot,
        horizon=horizon,
        max_dist_m=max_dist_m,
        min_corr=min_corr,
        top_k=top_k,
    )

    await set_json_cache(redis, cache_key, result, FORECAST_CACHE_TTL)
    return result
