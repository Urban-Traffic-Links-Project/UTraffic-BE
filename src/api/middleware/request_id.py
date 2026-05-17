"""
src/api/middleware/request_id.py
Request ID Middleware — API Gateway pattern.

Thêm X-Request-ID header vào mọi request và response để:
  1. Trace log dễ dàng (correlation ID)
  2. Debug lỗi theo từng request cụ thể
  3. Phù hợp với pattern API Gateway trong báo cáo

Flow:
  - Nếu client gửi X-Request-ID: dùng lại (client-side tracing)
  - Nếu không có: tự sinh UUID4
  - Luôn echo lại trong response header X-Request-ID
"""
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware thêm X-Request-ID vào mọi request/response.
    Mount vào FastAPI app trong main.py:
        app.add_middleware(RequestIDMiddleware)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Lấy request_id từ client hoặc tự sinh
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Gắn vào request state để route handler có thể truy cập nếu cần
        request.state.request_id = request_id

        response = await call_next(request)

        # Echo lại trong response header
        response.headers["X-Request-ID"] = request_id
        return response
