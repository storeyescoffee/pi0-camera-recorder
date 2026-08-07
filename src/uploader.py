"""S3 upload of recorded videos, with per-file status tracking and retention cleanup."""

import csv
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .api import CACHE_DIR, post_side_video

UPLOAD_STATUS_CSV_PATH = CACHE_DIR / "uploads.csv"
LOCK_PATH = Path(__file__).resolve().parent.parent / ".upload.lock"

_csv_lock = threading.Lock()


class _UploadProgressBar:
    """boto3 upload_file Callback: draws an in-place progress bar on the terminal."""

    _BAR_WIDTH = 30

    def __init__(self, filename: str, total_bytes: int):
        self._filename = filename
        self._total_bytes = total_bytes
        self._seen_bytes = 0
        self._last_pct = -1
        self._lock = threading.Lock()
        self._enabled = sys.stdout.isatty()

    def __call__(self, bytes_amount: int) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._seen_bytes += bytes_amount
            pct = int(self._seen_bytes * 100 / self._total_bytes) if self._total_bytes else 100
            if pct == self._last_pct:
                return
            self._last_pct = pct
            filled = int(self._BAR_WIDTH * pct / 100)
            bar = "#" * filled + "-" * (self._BAR_WIDTH - filled)
            sys.stdout.write(f"\r{self._filename[:30]:<30} [{bar}] {pct:3d}%")
            sys.stdout.flush()
            if pct >= 100:
                sys.stdout.write("\n")
                sys.stdout.flush()


def _read_uploaded_filenames(date: str) -> set[str]:
    """Filenames already recorded as uploaded for date. Caller must hold _csv_lock."""
    if not UPLOAD_STATUS_CSV_PATH.exists():
        return set()
    names = set()
    with open(UPLOAD_STATUS_CSV_PATH, newline="") as f:
        for row in csv.reader(f):
            if len(row) == 3 and row[0] == date:
                names.add(row[1])
    return names


def _append_uploaded(date: str, filename: str, uploaded_at: datetime) -> None:
    """Append a success record. Caller must hold _csv_lock."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(UPLOAD_STATUS_CSV_PATH, "a", newline="") as f:
        csv.writer(f).writerow([date, filename, uploaded_at.isoformat()])


def _acquire_lock() -> bool:
    """Acquire the cross-process upload lock, reclaiming it if the owning PID is dead."""
    if LOCK_PATH.exists():
        pid = None
        try:
            pid = int(LOCK_PATH.read_text().strip())
        except (ValueError, OSError):
            pass
        alive = False
        if pid is not None:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        if alive:
            return False
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(f"{os.getpid()}\n")
    return True


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def upload_videos_for_date(
    date: str,
    base_dir: Path,
    s3_location: str,
    aws_access_key: str,
    aws_secret_key: str,
    aws_region: str,
) -> bool:
    """
    Upload every <date>_*.mp4 in base_dir to S3 that isn't already recorded as
    uploaded. Returns True only if every matched file ends up uploaded
    (already-uploaded files count as success). False if any file fails, or if
    another upload is already in progress.

    After each attempt (success or failure), POSTs the result to
    /device-gw/side-videos so the panel's per-video upload status stays current.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        logging.error(
            "boto3 not installed; cannot upload. Install with: sudo apt install python3-boto3"
        )
        return False

    if not _acquire_lock():
        logging.warning("Another upload is already in progress; skipping this attempt")
        return False

    try:
        files = sorted(base_dir.glob(f"{date}_*.mp4"))
        if not files:
            logging.info("No videos found for %s in %s", date, base_dir)
            return True

        with _csv_lock:
            already_uploaded = _read_uploaded_filenames(date)

        key_root = s3_location[5:] if s3_location.startswith("s3://") else s3_location
        bucket, _, key_prefix = key_root.partition("/")

        client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region,
        )

        iso_date = f"{datetime.strptime(date, '%Y%m%d'):%Y-%m-%d}"

        all_ok = True
        for path in files:
            if path.name in already_uploaded:
                continue

            key = f"{key_prefix}/{date}/{path.name}" if key_prefix else f"{date}/{path.name}"
            local_size = path.stat().st_size
            started = time.monotonic()
            try:
                client.upload_file(
                    str(path), bucket, key, Callback=_UploadProgressBar(path.name, local_size)
                )
                head = client.head_object(Bucket=bucket, Key=key)
                if head["ContentLength"] != local_size:
                    raise ValueError(
                        f"size mismatch after upload: local={local_size} "
                        f"remote={head['ContentLength']}"
                    )
            except (BotoCoreError, ClientError, ValueError, OSError) as e:
                logging.warning("Upload failed for %s: %s", path.name, e)
                all_ok = False
                post_side_video(
                    date=iso_date,
                    name=path.stem,
                    upload_speed=0.0,
                    is_completed=False,
                )
                continue

            elapsed = time.monotonic() - started
            upload_speed = (local_size / 1_000_000 / elapsed) if elapsed > 0 else 0.0
            video_url = f"https://{bucket}.s3.{aws_region}.amazonaws.com/{key}"
            post_side_video(
                date=iso_date,
                name=path.stem,
                upload_speed=upload_speed,
                is_completed=True,
                video_url=video_url,
            )

            with _csv_lock:
                _append_uploaded(date, path.name, datetime.now())
            logging.info("Uploaded %s to s3://%s/%s", path.name, bucket, key)

        return all_ok
    finally:
        _release_lock()


def cleanup_old_uploads(base_dir: Path, retention_days: int) -> None:
    """Delete local files uploaded more than retention_days*24h ago. Keeps the CSV record."""
    if not UPLOAD_STATUS_CSV_PATH.exists():
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    with _csv_lock:
        rows = []
        with open(UPLOAD_STATUS_CSV_PATH, newline="") as f:
            for row in csv.reader(f):
                if len(row) == 3:
                    rows.append(row)

    for _date, filename, uploaded_at in rows:
        try:
            uploaded_ts = datetime.fromisoformat(uploaded_at)
        except ValueError:
            continue
        if uploaded_ts > cutoff:
            continue

        path = base_dir / filename
        if path.exists():
            try:
                path.unlink()
                logging.info("Deleted %s (uploaded %s, past %dd retention)", filename, uploaded_at, retention_days)
            except OSError as e:
                logging.warning("Could not delete %s: %s", filename, e)
