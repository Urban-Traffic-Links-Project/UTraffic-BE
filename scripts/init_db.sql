-- Script này chạy TỰ ĐỘNG khi PostgreSQL container khởi động lần đầu
-- Kích hoạt các extension cần thiết

-- TimescaleDB: tối ưu time-series (hypertable, continuous aggregate)
CREATE extension IF NOT EXISTS timescaledb;

-- PostGIS: cần cho kiểu dữ liệu geometry (POINT, 4326) 
CREATE extension IF NOT EXISTS postgis;

-- pgcrypto: hỗ trợ gen_random_uuid() nếu cần
CREATE extension IF NOT EXISTS pgcrypto;
