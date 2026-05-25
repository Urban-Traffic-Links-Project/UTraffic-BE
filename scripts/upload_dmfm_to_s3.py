"""
scripts/upload_dmfm_to_s3.py

Upload toàn bộ DMFM model folder lên AWS S3.

Cấu trúc S3 sau khi upload:
  s3://utraffic-data-bk-team/
  └── ml-data/
      └── dmfm_model/
          ├── dmfm_config.json
          ├── dmfm_model.npz          (~650MB)
          ├── segment_ids.npy
          ├── matrix_axis.csv
          ├── h1/
          │   ├── R_pred_meta.csv
          │   ├── segment_ids.npy
          │   ├── matrix_axis.csv
          │   └── bundles/
          │       └── dmfm_pred_test_h1_idx*.npz   (~64 files × 22MB)
          ├── h3/ (tương tự h1)
          ├── h6/ (tương tự h1)
          └── h9/ (tương tự h1)

Cách dùng:
  # Upload toàn bộ (khuyến nghị lần đầu)
  python scripts/upload_dmfm_to_s3.py

  # Upload chỉ model.npz (nếu đã có bundles)
  python scripts/upload_dmfm_to_s3.py --model-only

  # Upload chỉ bundles của 1 horizon
  python scripts/upload_dmfm_to_s3.py --horizon h1

  # Kiểm tra (không upload)
  python scripts/upload_dmfm_to_s3.py --dry-run

Yêu cầu:
  - File .env có AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME
  - uv run python scripts/upload_dmfm_to_s3.py   (hoặc activate venv trước)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Thêm src vào path để dùng config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    print("❌ boto3 chưa được cài. Chạy: pip install boto3")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # dotenv không bắt buộc, dùng env vars trực tiếp


# ─── Config ───────────────────────────────────────────────────────────────────

AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION            = os.getenv("AWS_REGION", "ap-southeast-1")
S3_BUCKET             = os.getenv("S3_BUCKET_NAME", "utraffic-data-bk-team")
S3_PREFIX             = "ml-data/dmfm_model"

# Đường dẫn local tới dmfm_model folder
SCRIPT_DIR  = Path(__file__).resolve().parent
BE_ROOT     = SCRIPT_DIR.parent
LOCAL_DMFM  = BE_ROOT / "ml_workspace" / "data" / "dmfm_model"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def human_size(n_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def get_s3_client():
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        print("❌ Thiếu AWS credentials!")
        print("   Hãy thêm vào file .env:")
        print("   AWS_ACCESS_KEY_ID=your_key")
        print("   AWS_SECRET_ACCESS_KEY=your_secret")
        sys.exit(1)

    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def collect_files(
    local_dir: Path,
    model_only: bool = False,
    horizon: str | None = None,
    skip_large: bool = False,
) -> list[tuple[Path, str]]:
    """
    Thu thập danh sách (local_path, s3_key) cần upload.
    """
    if not local_dir.exists():
        print(f"❌ Không tìm thấy thư mục: {local_dir}")
        sys.exit(1)

    pairs: list[tuple[Path, str]] = []

    if model_only:
        # Chỉ upload file model chính
        for fname in ["dmfm_model.npz", "dmfm_config.json", "segment_ids.npy", "matrix_axis.csv"]:
            p = local_dir / fname
            if p.exists():
                pairs.append((p, f"{S3_PREFIX}/{fname}"))
        return pairs

    if horizon:
        # Chỉ upload 1 horizon
        h_dir = local_dir / horizon
        if not h_dir.exists():
            print(f"❌ Không tìm thấy: {h_dir}")
            sys.exit(1)
        for p in sorted(h_dir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(local_dir)
                pairs.append((p, f"{S3_PREFIX}/{rel.as_posix()}"))
        return pairs

    # Upload tất cả
    for p in sorted(local_dir.rglob("*")):
        if not p.is_file():
            continue
        # Bỏ qua R_pred_series.npy vì rất lớn (~1.6GB) và đã có bundles
        if p.name == "R_pred_series.npy":
            print(f"⏭ Skip (quá lớn, đã có bundles): {p.name}")
            continue
        rel = p.relative_to(local_dir)
        pairs.append((p, f"{S3_PREFIX}/{rel.as_posix()}"))

    return pairs


def upload_file(s3_client, local_path: Path, s3_key: str, dry_run: bool = False) -> bool:
    size = local_path.stat().st_size
    print(f"  📤 {local_path.name:50s} ({human_size(size):>10s})  →  s3://{S3_BUCKET}/{s3_key}")

    if dry_run:
        return True

    t0 = time.time()
    try:
        s3_client.upload_file(str(local_path), S3_BUCKET, s3_key)
        elapsed = time.time() - t0
        speed = size / max(elapsed, 0.001) / (1024 * 1024)
        print(f"     ✅ Done in {elapsed:.1f}s ({speed:.1f} MB/s)")
        return True
    except ClientError as e:
        print(f"     ❌ FAILED: {e}")
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Upload DMFM model to AWS S3")
    ap.add_argument("--model-only", action="store_true", help="Chỉ upload dmfm_model.npz + config")
    ap.add_argument("--horizon", type=str, choices=["h1", "h3", "h6", "h9"], help="Chỉ upload 1 horizon")
    ap.add_argument("--dry-run", action="store_true", help="Xem danh sách file, không upload")
    ap.add_argument("--local-dir", type=str, default=str(LOCAL_DMFM), help="Đường dẫn local tới dmfm_model/")
    args = ap.parse_args()

    local_dir = Path(args.local_dir)

    print("=" * 70)
    print("🚀 DMFM → S3 Upload")
    print(f"   Local : {local_dir}")
    print(f"   S3    : s3://{S3_BUCKET}/{S3_PREFIX}/")
    print(f"   Region: {AWS_REGION}")
    if args.dry_run:
        print("   Mode  : DRY RUN (không thực sự upload)")
    print("=" * 70)

    pairs = collect_files(local_dir, model_only=args.model_only, horizon=args.horizon)

    if not pairs:
        print("⚠ Không có file nào để upload.")
        return

    total_size = sum(p.stat().st_size for p, _ in pairs)
    print(f"\n📦 Tổng: {len(pairs)} files, {human_size(total_size)}\n")

    if not args.dry_run:
        s3 = get_s3_client()
        # Test connection
        try:
            s3.head_bucket(Bucket=S3_BUCKET)
            print(f"✅ Kết nối S3 bucket '{S3_BUCKET}' thành công\n")
        except ClientError as e:
            print(f"❌ Không truy cập được bucket: {e}")
            sys.exit(1)
    else:
        s3 = None

    success = 0
    failed = 0
    for local_path, s3_key in pairs:
        ok = upload_file(s3, local_path, s3_key, dry_run=args.dry_run)
        if ok:
            success += 1
        else:
            failed += 1

    print("\n" + "=" * 70)
    if args.dry_run:
        print(f"✅ [DRY RUN] Sẽ upload {success} files ({human_size(total_size)})")
    else:
        print(f"✅ Upload xong: {success}/{len(pairs)} files thành công")
        if failed:
            print(f"❌ Lỗi: {failed} files")
    print("=" * 70)


if __name__ == "__main__":
    main()
