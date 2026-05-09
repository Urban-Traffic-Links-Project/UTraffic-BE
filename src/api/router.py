"""
src/api/router.py
Router tổng — gom tất cả module router vào 1 chỗ, mount vào main.py.
 
Khi thêm module mới (traffic, correlation, prediction):
  1. from src.modules.xxx import router as xxx_router
  2. api_router.include_router(xxx_router)
  Chỉ vậy thôi — không cần sửa main.py.
"""
from fastapi import APIRouter

from src.modules.auth.router import router as auth_router
from src.modules.traffic.router import router as traffic_router
from src.modules.correlation.router import router as correlation_router
from src.modules.storage.router import router as storage_router

api_router = APIRouter(prefix="/api/v1")

# Đăng ký các module router
api_router.include_router(auth_router)
api_router.include_router(traffic_router)
api_router.include_router(correlation_router)
api_router.include_router(storage_router)
# api_router.include_router(prediction_router)