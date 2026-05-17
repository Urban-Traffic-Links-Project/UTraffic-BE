"""
src/integrations/cache_helpers.py
Tiện ích JSON cache chung — dùng với mọi Redis pool trong hệ thống.

Pattern chuẩn:
  1. Gọi get_json_cache(redis, key) → trả về dict nếu hit, None nếu miss
  2. Nếu miss: tính toán result
  3. Gọi set_json_cache(redis, key, result, ttl) để lưu cache
  4. Trả về result
"""
import json
from typing import Any

from redis.asyncio import Redis


async def get_json_cache(redis: Redis, key: str) -> Any | None:
    """
    Lấy dữ liệu JSON từ Redis.
    Trả về Python object (dict/list) nếu cache hit, None nếu cache miss hoặc lỗi.
    """
    try:
        val = await redis.get(key)
        if val is None:
            return None
        return json.loads(val)
    except Exception:
        # Cache miss do lỗi kết nối hoặc deserialize — fallback sang DB
        return None


async def set_json_cache(redis: Redis, key: str, data: Any, ttl: int) -> bool:
    """
    Lưu dữ liệu JSON vào Redis với TTL (giây).
    Trả về True nếu lưu thành công, False nếu lỗi (không ảnh hưởng luồng chính).

    default=str: tự convert datetime, UUID, Enum thành chuỗi
    """
    try:
        serialized = json.dumps(data, default=str, ensure_ascii=False)
        await redis.setex(key, ttl, serialized)
        return True
    except Exception:
        return False


async def delete_cache(redis: Redis, *keys: str) -> int:
    """
    Xóa một hoặc nhiều key khỏi Redis (dùng cho cache invalidation).
    Trả về số key đã bị xóa.
    """
    try:
        if not keys:
            return 0
        return await redis.delete(*keys)
    except Exception:
        return 0


async def delete_pattern(redis: Redis, pattern: str) -> int:
    """
    Xóa tất cả key khớp với pattern (ví dụ: "corr:{node_id}:*").
    Dùng SCAN để tránh block Redis — an toàn với production.
    Trả về số key đã xóa.
    """
    deleted = 0
    try:
        async for key in redis.scan_iter(match=pattern, count=100):
            await redis.delete(key)
            deleted += 1
    except Exception:
        pass
    return deleted
