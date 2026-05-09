"""
src/modules/storage/router.py

GET  /api/v1/storage/upload-url   → presigned URL để frontend upload file lên S3
GET  /api/v1/storage/download-url → presigned URL để download file từ S3

Flow upload từ frontend:
  1. Frontend gọi GET /upload-url?filename=report.pdf&content_type=application/pdf
  2. Backend trả về { upload_url, s3_key }
  3. Frontend PUT file lên upload_url (không qua server)
  4. Frontend lưu s3_key để tham chiếu sau
"""
import uuid
from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import SQLModel

from src.api.dependencies import CurrentUser
from src.storage.aws.s3_client import get_s3_client
from src.core.config import get_settings

router = APIRouter(prefix="/storage", tags=["Storage"])
settings = get_settings()

ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/webp",
    "application/pdf",
    "text/csv",
    "application/octet-stream",
}

MAX_FILENAME_LENGTH = 200


class UploadUrlResponse(SQLModel):
    upload_url: str     # PUT URL — frontend upload thẳng lên đây
    s3_key: str         # Lưu lại để tham chiếu file sau
    expires_in: int     # Số giây URL còn hiệu lực


class DownloadUrlResponse(SQLModel):
    download_url: str
    expires_in: int


def _check_s3_configured():
    if not settings.aws_access_key_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="S3 chưa được cấu hình. Thêm AWS credentials vào .env",
        )


@router.get("/upload-url", response_model=UploadUrlResponse)
def get_upload_url(
    current_user: CurrentUser,
    filename: str = Query(..., max_length=MAX_FILENAME_LENGTH),
    content_type: str = Query(default="application/octet-stream"),
):
    """
    Tạo presigned URL để frontend upload file lên S3.
    File sẽ được lưu tại: user-uploads/{user_id}/{uuid}_{filename}
    """
    _check_s3_configured()

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Content type không được hỗ trợ: {content_type}",
        )

    # Tạo key duy nhất — tránh collision và path traversal
    safe_filename = filename.replace("/", "_").replace("..", "_")
    s3_key = f"user-uploads/{current_user.id}/{uuid.uuid4().hex}_{safe_filename}"

    s3 = get_s3_client()
    upload_url = s3.presigned_upload_url(
        s3_key=s3_key,
        expires_in=300,         # 5 phút
        content_type=content_type,
    )

    return UploadUrlResponse(upload_url=upload_url, s3_key=s3_key, expires_in=300)


@router.get("/download-url", response_model=DownloadUrlResponse)
def get_download_url(
    current_user: CurrentUser,
    s3_key: str = Query(..., max_length=500),
):
    """
    Tạo presigned URL để download file từ S3 (1 giờ).
    Chỉ cho phép download file của chính user.
    """
    _check_s3_configured()

    # Bảo mật: chỉ cho phép user download file của mình
    expected_prefix = f"user-uploads/{current_user.id}/"
    if not s3_key.startswith(expected_prefix):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Không có quyền truy cập file này",
        )

    s3 = get_s3_client()
    if not s3.key_exists(s3_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File không tồn tại",
        )

    download_url = s3.presigned_download_url(s3_key=s3_key, expires_in=3600)
    return DownloadUrlResponse(download_url=download_url, expires_in=3600)
