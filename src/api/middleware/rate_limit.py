"""
src/api/middleware/rate_limit.py
Rate Limiting Middleware — API Gateway pattern.

Giới hạn số lượng request theo IP address dùng Redis-Auth (DB 0).
Phù hợp với thiết kế API Gateway trong báo cáo — kiểm soát truy cập ở mức hệ thống.

Cấu hình:
  RATE_LIMIT_REQUESTS: số request tối đa trong RATE_LIMIT_WINDOW giây
  RATE_LIMIT_WINDOW:   cửa sổ thời gian (giây), default 60

Nếu Redis không khả dụng: bỏ qua rate limiting (graceful degradation).
"""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

# ── Cấu hình Rate Limit ──────────────────────────────────────
RATE_LIMIT_REQUESTS = 120   # Max requests per window
RATE_LIMIT_WINDOW = 60      # Window size (giây)

# Các path không áp dụng rate limit (health checks, docs)
EXEMPT_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    IP-based rate limiting dùng Redis sliding counter.

    Algorithm: Fixed window counter per IP.
    Key: ratelimit:{client_ip}  →  count  (TTL = RATE_LIMIT_WINDOW giây)

    Mount vào FastAPI app TRƯỚC RequestIDMiddleware trong main.py:
        app.add_middleware(RateLimitMiddleware)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Bỏ qua các path exempt
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        client_ip = self._get_client_ip(request)

        try:
            from src.integrations.redis_client import get_redis_auth
            redis_auth = get_redis_auth()

            key = f"ratelimit:{client_ip}"
            count = await redis_auth.incr(key)

            if count == 1:
                # Key vừa được tạo → set TTL
                await redis_auth.expire(key, RATE_LIMIT_WINDOW)

            if count > RATE_LIMIT_REQUESTS:
                # Lấy TTL còn lại để thông báo client
                ttl = await redis_auth.ttl(key)
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Quá nhiều yêu cầu. Vui lòng thử lại sau.",
                        "retry_after_seconds": max(ttl, 1),
                    },
                    headers={"Retry-After": str(max(ttl, 1))},
                )

        except Exception:
            # Redis không khả dụng → bỏ qua rate limit, không block API
            logger.warning("Rate limit Redis unavailable — skipping rate limit check")

        return await call_next(request)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """
        Lấy IP thực của client, hỗ trợ các header phổ biến từ Nginx/Load Balancer.
        """
        # X-Forwarded-For: IP thực khi qua proxy/Nginx
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        # X-Real-IP: header từ Nginx
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

        # Direct connection
        if request.client:
            return request.client.host

        return "unknown"
