"""
src/storage/aws/s3_client.py
Wrapper đơn giản cho boto3 S3.

Dùng ở 2 nơi:
  1. seed scripts  — download .npz/.npy/.csv từ S3 về memory để xử lý
  2. API endpoint  — tạo presigned URL cho user upload/download file
"""
import io
import logging
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from src.core.config import get_settings

logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self):
        settings = get_settings()
        self._bucket = settings.s3_bucket_name
        self._client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

    # ── Download ──────────────────────────────────────────────

    def download_bytes(self, s3_key: str) -> bytes:
        """
        Download file từ S3 → trả về bytes (không lưu disk).
        Dùng cho seed scripts đọc .npz/.npy trực tiếp vào memory.

        Ví dụ:
            raw = s3.download_bytes("ml-data/graph_structure_latest.npz")
            gs  = np.load(io.BytesIO(raw))
        """
        logger.info(f"S3 download: s3://{self._bucket}/{s3_key}")
        obj = self._client.get_object(Bucket=self._bucket, Key=s3_key)
        return obj["Body"].read()

    def download_to_file(self, s3_key: str, local_path: str | Path) -> None:
        """Download file từ S3 → lưu ra disk (dùng khi file lớn cần cache local)."""
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"S3 download to disk: {s3_key} → {local_path}")
        self._client.download_file(self._bucket, s3_key, str(local_path))

    def download_text(self, s3_key: str, encoding: str = "utf-8") -> str:
        """Download file text (CSV, JSON) từ S3 → trả về string."""
        return self.download_bytes(s3_key).decode(encoding)

    # ── Upload ────────────────────────────────────────────────

    def upload_bytes(self, data: bytes, s3_key: str, content_type: str = "application/octet-stream") -> str:
        """
        Upload bytes lên S3.
        Trả về s3_key để lưu vào DB.
        """
        self._client.put_object(
            Bucket=self._bucket,
            Key=s3_key,
            Body=data,
            ContentType=content_type,
        )
        logger.info(f"S3 upload: {len(data):,} bytes → s3://{self._bucket}/{s3_key}")
        return s3_key

    def upload_file(self, local_path: str | Path, s3_key: str) -> str:
        """Upload file từ disk lên S3."""
        self._client.upload_file(str(local_path), self._bucket, s3_key)
        logger.info(f"S3 upload file: {local_path} → {s3_key}")
        return s3_key

    # ── Presigned URL ─────────────────────────────────────────

    def presigned_upload_url(self, s3_key: str, expires_in: int = 300, content_type: str | None = None) -> str:
        """
        Tạo presigned URL để frontend upload thẳng lên S3 (không qua server).
        expires_in: số giây URL còn hiệu lực (default 5 phút).

        Flow:
          Frontend → GET /api/v1/storage/upload-url?filename=abc.jpg
          Backend  → trả presigned URL
          Frontend → PUT presigned URL với file bytes
          S3       → lưu file
        """
        params: dict = {"Bucket": self._bucket, "Key": s3_key}
        if content_type:
            params["ContentType"] = content_type

        url = self._client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=expires_in,
        )
        return url

    def presigned_download_url(self, s3_key: str, expires_in: int = 3600) -> str:
        """
        Tạo presigned URL để download file (default 1 giờ).
        Dùng cho file private — không public bucket.
        """
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": s3_key},
            ExpiresIn=expires_in,
        )

    # ── Utilities ─────────────────────────────────────────────

    def list_keys(self, prefix: str) -> list[str]:
        """Liệt kê tất cả S3 keys với prefix nhất định."""
        paginator = self._client.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def key_exists(self, s3_key: str) -> bool:
        """Kiểm tra file có tồn tại trên S3 không."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def get_latest_key(self, prefix: str, suffix: str = ".npz") -> str | None:
        """
        Lấy key mới nhất (theo LastModified) trong 1 prefix.
        Dùng để luôn đọc file NPZ mới nhất mà không cần hardcode tên.

        Ví dụ:
            key = s3.get_latest_key("ml-data/", ".npz")
            # → "ml-data/graph_structure_20260427_152321.npz"
        """
        paginator = self._client.get_paginator("list_objects_v2")
        candidates = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(suffix):
                    candidates.append((obj["LastModified"], obj["Key"]))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]


@lru_cache(maxsize=1)
def get_s3_client() -> S3Client:
    """Singleton — tạo 1 lần, dùng lại cho toàn app."""
    return S3Client()