"""
src/integrations/redis_client.py
Redis connection pool — Singleton per logical DB.

Kiến trúc 5 vùng cache tách biệt:
  DB 0 — Auth Store     : JWT blacklist, OTP hash
  DB 1 — Corr Cache     : Kết quả correlation (TTL = batch interval)
  DB 2 — Pred Cache     : Kết quả DMFM prediction (TTL 30s)
  DB 3 — API Cache      : nodes/edges tĩnh (TTL 60s)
  DB 4 — Inference Cache: Kết quả spread/cause TVP-VAR (TTL 30s)

Dùng redis.asyncio (fully async) để tương thích với FastAPI async lifespan.
"""
from functools import lru_cache

import redis.asyncio as aioredis

from src.core.config import get_settings

settings = get_settings()

# ────────────────────────────────────────────────────────────
# Pool factories — mỗi DB là 1 pool riêng, khởi tạo lazy qua lru_cache
# ────────────────────────────────────────────────────────────

@lru_cache
def get_redis_auth() -> aioredis.Redis:
    """
    DB 0 — Auth Store.
    Keys:
      blacklist:{jti}   → "1"  (TTL = thời gian còn lại của access token)
      otp:{email}       → "{hash}:{expires_iso}"  (TTL 10 phút)
    """
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db_auth,
        decode_responses=True,
    )


@lru_cache
def get_redis_corr() -> aioredis.Redis:
    """
    DB 1 — Correlation Cache.
    Keys:
      corr:{node_id}:{snapshot}:{params_hash8}  → JSON  (TTL = batch interval)
    """
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db_corr,
        decode_responses=True,
    )


@lru_cache
def get_redis_pred() -> aioredis.Redis:
    """
    DB 2 — DMFM Prediction Cache.
    Keys:
      pred:{node_id}:h{horizon}  → JSON  (TTL 30s)
    """
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db_pred,
        decode_responses=True,
    )


@lru_cache
def get_redis_api() -> aioredis.Redis:
    """
    DB 3 — API Static Cache.
    Keys:
      nodes:all  → JSON  (TTL 60s)
      edges:all  → JSON  (TTL 60s)
    """
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db_api,
        decode_responses=True,
    )


@lru_cache
def get_redis_inference() -> aioredis.Redis:
    """
    DB 4 — TVP-VAR Inference Cache.
    Keys:
      spread:{incident_id}:h{horizon}:r{radius_x10}  → JSON  (TTL 30s)
      cause:{incident_id}:h{horizon}:r{radius_x10}   → JSON  (TTL 30s)
    """
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db_inference,
        decode_responses=True,
    )


# ────────────────────────────────────────────────────────────
# Health check & Cleanup
# ────────────────────────────────────────────────────────────

async def ping_redis() -> dict[str, bool]:
    """
    Ping tất cả Redis pool khi server khởi động.
    Trả về dict {db_name: is_ok} để log status.
    """
    pools = {
        "redis_auth":      get_redis_auth(),
        "redis_corr":      get_redis_corr(),
        "redis_pred":      get_redis_pred(),
        "redis_api":       get_redis_api(),
        "redis_inference": get_redis_inference(),
    }
    results: dict[str, bool] = {}
    for name, pool in pools.items():
        try:
            await pool.ping()
            results[name] = True
        except Exception:
            results[name] = False
    return results


async def close_redis_pools() -> None:
    """
    Đóng tất cả Redis connection pool khi server shutdown.
    Gọi trong lifespan của FastAPI (sau yield).
    """
    for pool_fn in [
        get_redis_auth,
        get_redis_corr,
        get_redis_pred,
        get_redis_api,
        get_redis_inference,
    ]:
        try:
            await pool_fn().aclose()
        except Exception:
            pass
