# 🚦 UTraffic Backend

## Overview

UTraffic Backend là hệ thống API trung tâm phục vụ phân tích, dự đoán và
cung cấp dữ liệu giao thông theo thời gian thực. Hệ thống được xây dựng
theo hướng **production-grade**, tích hợp pipeline dữ liệu, machine
learning và caching để đảm bảo hiệu năng cao.

------------------------------------------------------------------------

## 🏗️ Architecture Highlights

-   **FastAPI**: RESTful API hiệu năng cao
-   **PostgreSQL + TimescaleDB**: lưu trữ metadata và time-series
    traffic
-   **Redis (Cache-Aside)**: tối ưu truy vấn
-   **Kafka Pipeline**: ingest & streaming
-   **ML Core (T-GCN)**: dự đoán tình trạng giao thông
-   **MLflow**: quản lý experiment & model version
-   **Prometheus + Grafana**: monitoring & observability

------------------------------------------------------------------------

## 📁 Project Structure

    utraffic-backend/
    │
    ├── src/                         # 🚀 Main application source
    │   │
    │   ├── auth/                   # 🔐 Authentication Domain
    │   │   ├── router.py           # POST /login, /logout, /refresh
    │   │   ├── service.py          # JWT logic, authentication flow
    │   │   ├── models.py           # User (PostgreSQL)
    │   │   ├── schemas.py          # LoginRequest, TokenResponse
    │   │   ├── dependencies.py     # get_current_user (shared)
    │   │   └── exceptions.py       # InvalidCredentials, TokenExpired
    │   │
    │   ├── traffic/                # 🚦 Traffic Domain
    │   │   ├── router.py           # /nodes/{id}/speed, /map/heatmap
    │   │   ├── service.py          # TimescaleDB + Redis cache
    │   │   ├── schemas.py          # TrafficNode, SpeedReading
    │   │   └── constants.py        # CACHE_TTL, NODE_RADIUS
    │   │
    │   ├── prediction/             # 🤖 Prediction Domain (ML Inference)
    │   │   ├── router.py           # /predict/{node_id}, /jam-forecast
    │   │   ├── service.py          # inference + caching
    │   │   ├── schemas.py          # PredictionRequest, Response
    │   │   ├── model_loader.py     # load .pth from registry
    │   │   └── exceptions.py       # ModelNotFound, InferenceFailed
    │   │
    │   ├── correlation/            # 🔗 Correlation Analysis Domain
    │   │   ├── router.py           # /correlations/node/{id}
    │   │   ├── service.py          # compute + cache
    │   │   ├── schemas.py          # CorrelationResult
    │   │   └── tasks.py            # APScheduler batch jobs
    │   │
    │   ├── db/                     # 🗄️ Infrastructure - Data Layer
    │   │   ├── postgres.py         # SQLAlchemy engine
    │   │   ├── timescale.py        # TimescaleDB helper
    │   │   ├── redis.py            # Redis client (multi-db)
    │   │   └── base.py             # Base ORM model
    │   │
    │   ├── monitoring/             # 📊 Observability Layer
    │   │   ├── metrics.py          # Prometheus metrics
    │   │   ├── middleware.py       # request logging + latency
    │   │   └── health.py           # /health check (DB, Redis, ML)
    │   │
    │   ├── ml/                     # 🧠 ML Core Layer
    │   │   ├── mlflow_client.py    # connect MLflow
    │   │   ├── tgcn.py             # T-GCN model definition
    │   │   └── inference.py        # preprocess → predict → postprocess
    │   │
    │   ├── config.py               # ⚙️ Environment config (.env)
    │   ├── dependencies.py         # shared DI (db, redis)
    │   ├── exceptions.py           # global exception handlers
    │   └── main.py                 # FastAPI entrypoint
    │
    ├── tests/                      # 🧪 Testing Layer
    │   ├── test_auth.py
    │   ├── test_traffic.py
    │   └── test_prediction.py
    │
    ├── alembic/                    # 🔄 Database migrations
    │
    ├── docker-compose.yml          # 🐳 Multi-service orchestration
    ├── Dockerfile                  # 🐳 App container
    ├── .env.example                # 🔐 Environment template
    └── pyproject.toml            # 📦 Dependencies

------------------------------------------------------------------------

## ⚙️ Setup & Run

### 1. Clone project

``` bash
git clone <repo_url>
cd utraffic-backend
```

## 🛠️ Hướng dẫn cài đặt và sử dụng

Dự án này khuyến khích sử dụng [**uv**](https://docs.astral.sh/uv/) - trình quản lý package cực nhanh viết bằng Rust.

### Bước 1: Cài đặt công cụ
Nếu bạn chưa cài đặt `uv`, hãy mở Terminal và chạy:

```bash
# Trên macOS/Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Trên Windows (PowerShell):
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Bước 2: Tải dự án và cài đặt thư viện
Di chuyển vào thư mục dự án và chạy lệnh đồng bộ:
```bash
uv sync
```
*Lệnh này sẽ tự động đọc `pyproject.toml`, tạo môi trường ảo (virtual environment) và cài đặt toàn bộ các thư viện (bao gồm cả dev-dependencies).*

### Bước 3: Cấu hình biến môi trường
Tạo một file `.env` ở thư mục gốc của dự án và điền các thông tin kết nối Database của bạn:
```env
DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/db_name
SECRET_KEY=your_super_secret_key
```

### Bước 4: Cập nhật Database (Migration)
Chạy Alembic để tạo các bảng trong PostgreSQL:
```bash
uv run alembic upgrade head
```

### Bước 5: Chạy Server
Khởi động ứng dụng FastAPI ở chế độ phát triển (tự động reload khi sửa code):
```bash
uv run fastapi dev main.py
```
*(Lưu ý: Thay `main.py` bằng tên file chứa instance `app` của bạn).*

Truy cập tài liệu API tự động tại: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🧹 Quản lý Code (Code Quality)

Trong quá trình lập trình, hãy chạy các lệnh sau để giữ code luôn sạch đẹp:

**1. Kiểm tra và tự động dọn dẹp lỗi code (Ruff):**
```bash
uv run ruff check --fix .
```

**2. Tự động căn lề, ngắt dòng chuẩn Python (Ruff Formatter):**
```bash
uv run ruff format .
```

**3. Chạy kiểm tra kiểu dữ liệu (Mypy):**
```bash
uv run mypy .
```

**4. Chạy Unit Test (Pytest):**
```bash
uv run pytest
```

------------------------------------------------------------------------

## 🔐 Authentication Flow

-   JWT-based authentication
-   Redis hỗ trợ:
    -   Token blacklist
    -   Session caching

Endpoints: - `POST /login` - `POST /refresh` - `POST /logout`

------------------------------------------------------------------------

## 🚗 Traffic Service

-   Truy vấn dữ liệu traffic theo node
-   Heatmap visualization

Cache: - TTL: 60s - Strategy: Cache-aside

------------------------------------------------------------------------

## 🤖 Prediction Service

-   Sử dụng mô hình T-GCN
-   Load model từ Model Registry / MLflow
-   Cache kết quả dự đoán

Endpoints: - `/predict/{node_id}` - `/jam-forecast`

------------------------------------------------------------------------

## 🔗 Correlation Service

-   Phân tích mối quan hệ giữa các node giao thông
-   Batch job chạy định kỳ

------------------------------------------------------------------------

## 📊 Monitoring

-   Prometheus metrics:
    -   Request count
    -   Latency
-   Health check:
    -   `/health`

------------------------------------------------------------------------

## 🧠 ML Pipeline

-   Data → Feature → Model
-   MLflow:
    -   Track experiments
    -   Version control model
-   Model lưu tại S3 / Registry (.pth)

------------------------------------------------------------------------

## 🚀 Future Improvements

-   Feature Store (Feast)
-   CI/CD pipeline
-   Distributed tracing (Jaeger)
-   Model serving scaling

------------------------------------------------------------------------

## 👨‍💻 Author

UTraffic Team
