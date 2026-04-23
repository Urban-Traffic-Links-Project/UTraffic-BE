# Modern FastAPI App 🚀

Hệ thống API hiện đại được xây dựng với các công nghệ và tiêu chuẩn mới nhất của hệ sinh thái Python (FastAPI, SQLModel, Psycopg 3). Dự án này được cấu hình để tối ưu hóa hiệu năng, bảo mật và trải nghiệm lập trình (Developer Experience).

**Tác giả:** Brozic (quockhanh.nguyen290804@gmail.com)  
**Yêu cầu:** Python >= 3.11

---

## 📚 Giải thích Tech Stack (Các thư viện sử dụng)

Dự án sử dụng file `pyproject.toml` để quản lý dependency, thay thế cho cách tiếp cận cũ bằng `requirements.txt` hay `setup.py`.

### 1. Core Framework & Web
* **`fastapi[standard]`**: Framework chính để xây dựng API. Hậu tố `[standard]` tự động đi kèm với `Uvicorn` (máy chủ web ASGI siêu tốc) và các công cụ tiêu chuẩn, giúp bạn không cần cài đặt lẻ tẻ.
* **`python-multipart`**: Thư viện hỗ trợ FastAPI xử lý các request gửi lên dưới dạng Form Data (ví dụ: các form đăng nhập truyền thống) hoặc khi người dùng tải file (Upload files) lên server.

### 2. Database & ORM (PostgreSQL)
* **`sqlmodel`**: Thư viện ORM thế hệ mới do chính tác giả FastAPI viết. Nó là sự kết hợp hoàn hảo giữa `SQLAlchemy` (giao tiếp DB) và `Pydantic` (kiểm tra dữ liệu), giúp bạn chỉ cần viết 1 Class dùng chung cho cả API và Database.
* **`psycopg[binary,pool]`**: Trình điều khiển (driver) thế hệ thứ 3 để kết nối với PostgreSQL. Hỗ trợ bất đồng bộ (async/await) và connection pool (quản lý luồng kết nối), giúp tăng tốc độ xử lý lên gấp nhiều lần so với bản cũ.
* **`alembic`**: Công cụ quản lý "di cư" cơ sở dữ liệu (Database Migrations). Giúp mình dễ dàng thêm, sửa, xóa các cột/bảng trong CSDL mà không làm mất dữ liệu cũ.

### 3. Security (Bảo mật & Xác thực)
* **`pyjwt`**: Thư viện dùng để tạo và giải mã JSON Web Tokens (JWT). Đây là cốt lõi của hệ thống xác thực, giúp cấp "thẻ bài" cho người dùng sau khi đăng nhập.
* **`argon2-cffi`**: Công cụ băm (hashing) mật khẩu tiên tiến nhất hiện nay, thay thế cho bcrypt. Nó thiết kế để chống lại các cuộc tấn công bẻ khóa bằng card đồ họa (GPU).
* **`pydantic-settings`**: Giúp nạp và quản lý các biến môi trường từ file `.env` một cách an toàn. Có khả năng tự động kiểm tra xem bạn có bị thiếu biến quan trọng nào không trước khi app khởi động.

### 4. Development Tools (Công cụ hỗ trợ viết code)
* **`ruff`**: Công cụ Linter và Formatter siêu tốc viết bằng Rust. Dùng để dọn dẹp code, tự động căn lề (88 ký tự/dòng), tìm lỗi logic và tự động sắp xếp thư viện (Isort).
* **`pytest`**: Thư viện viết Unit Test chuẩn mực nhất của Python, giúp đảm bảo API hoạt động đúng như thiết kế.
* **`mypy`**: Công cụ kiểm tra kiểu dữ liệu tĩnh. Nó quét code của bạn để tìm ra các lỗi liên quan đến sai kiểu dữ liệu (ví dụ: truyền chữ vào hàm yêu cầu số) trước cả khi bạn chạy app.

---

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
uv run fastapi dev main.p
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
