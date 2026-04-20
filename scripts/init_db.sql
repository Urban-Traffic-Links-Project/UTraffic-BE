-- Script này chạy TỰ ĐỘNG khi PostgreSQL container khởi động lần đầu
-- Kích hoạt các extension cần thiết

-- PostGIS: hỗ trợ kiểu dữ liệu địa lý (GEOMETRY, GIST index)
CREATE extension IF NOT EXISTS postgis;

-- TimescaleDB: tối ưu time-series (hypertable, continuous aggregate)
CREATE extension IF NOT EXISTS timescaledb;

-- pgcrypto: hỗ trợ gen_random_uuid() nếu cần
CREATE extension IF NOT EXISTS pgcrypto;