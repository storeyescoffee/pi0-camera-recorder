#!/usr/bin/env python3
"""
Pi camera recorder: captures one continuous MP4 per session via picamera2.
Fetches settings from remote API (SIDE_CAMERA, BUSINESS_HOUR).
Uses at(1) to schedule recording within business hours.
"""

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from src.api import fetch_remote_settings, post_side_videos_bulk
from config.config import CONFIG_PATH, load_config
from src.recorder import run_recorder
from src.schedule import (
    is_within_business_hours,
    parse_time,
    schedule_at,
    schedule_upload_retry,
    seconds_until_end_time,
)
from src.uploader import cleanup_old_uploads, upload_videos_for_date

LOG_DIR = Path(__file__).resolve().parent / "logs"


def setup_logging() -> None:
    """Configure logging to logs/YYYY-MM-DD.log and console."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def write_pid_file(pid_path: Path) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    pid_path.write_text(f"{pid}\n", encoding="utf-8")
    logging.info("Wrote pid file: %s (pid=%d)", pid_path, pid)


def business_date(now: datetime):
    """Business date for 'today' with a 03:00 overnight cutoff (used only for a bare --upload)."""
    d = now.date()
    if now.hour < 3:
        d -= timedelta(days=1)
    return d


def parse_args() -> dict:
    parser = argparse.ArgumentParser(description="Record continuous Pi camera video to MP4")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to config file")
    parser.add_argument("--ignore-hours", action="store_true", help="Record at any hour")
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=None,
        help="Path to write pid file (default: <project_dir>/.pid)",
    )
    parser.add_argument(
        "--upload",
        nargs="?",
        const="__today__",
        default=None,
        metavar="YYYYMMDD",
        help="Upload videos for date and exit (no recording). Omit value for today's business date.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    upload_date = args.upload
    if upload_date == "__today__":
        upload_date = f"{business_date(datetime.now()):%Y%m%d}"

    return {
        "base_dir": Path(cfg["base_dir"]).resolve(),
        "flip": cfg["flip"],
        "bitrate": cfg["bitrate"],
        "fps": cfg["fps"],
        "width": cfg["width"],
        "height": cfg["height"],
        "gop": cfg["gop"],
        "ignore_hours": args.ignore_hours or cfg["ignore_hours"],
        "pid_file": args.pid_file,
        "upload_date": upload_date,
    }


def is_recording_hours() -> bool:
    """Return True if current hour is 07:00–22:00 inclusive."""
    return 7 <= datetime.now().hour < 22


def parse_bool(v: object) -> bool | None:
    """Parse booleans that may arrive as bool/int/str; return None if unknown."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _bulk_sender_loop() -> None:
    """Background thread: send bulk at HH:05 every hour, retry every 2 min on failure."""
    while True:
        now = datetime.now()
        if now.minute < 5:
            wait = (5 - now.minute) * 60 - now.second
        else:
            wait = (60 - now.minute + 5) * 60 - now.second
        time.sleep(wait)

        while not post_side_videos_bulk():
            logging.warning("Bulk side-video send failed, retrying in 2 minutes")
            time.sleep(120)
        logging.info("Bulk side-video send succeeded")


def _attempt_upload_or_reschedule(date: str, base_dir: Path, remote: dict | None, script_path: Path) -> None:
    """Upload date's videos; on any failure (or missing settings), schedule a retry via at(1)."""
    if not remote or "AWS" not in remote or "RECORDING" not in remote:
        logging.warning("Missing AWS/RECORDING settings; cannot upload %s, scheduling retry", date)
        schedule_upload_retry(date, script_path)
        return

    aws = remote["AWS"]
    s3_location = remote["RECORDING"].get("s3-location")
    if not s3_location:
        logging.warning("No s3-location configured; cannot upload %s, scheduling retry", date)
        schedule_upload_retry(date, script_path)
        return

    retention_days = int(remote.get("SIDE_CAMERA", {}).get("retention", 3))
    api_key = remote.get("API", {}).get("api-key")

    ok = upload_videos_for_date(
        date=date,
        base_dir=base_dir,
        s3_location=s3_location,
        aws_access_key=aws.get("access-key"),
        aws_secret_key=aws.get("secret-key"),
        aws_region=aws.get("default-region"),
        api_key=api_key,
    )
    if ok:
        logging.info("Upload complete for %s", date)
    else:
        logging.warning("Upload incomplete for %s, scheduling retry", date)
        schedule_upload_retry(date, script_path)

    cleanup_old_uploads(base_dir, retention_days)


def main() -> None:
    setup_logging()
    args = parse_args()
    base_dir = args["base_dir"]
    script_path = Path(__file__).resolve()

    if args["upload_date"] is not None:
        remote = fetch_remote_settings()
        _attempt_upload_or_reschedule(args["upload_date"], base_dir, remote, script_path)
        sys.exit(0)

    pid_path = (
        Path(args["pid_file"]).expanduser().resolve()
        if args["pid_file"]
        else (Path(__file__).resolve().parent / ".pid")
    )
    flip = args["flip"]
    bitrate = args["bitrate"]
    business_start: tuple[int, int] | None = None
    business_end: tuple[int, int] | None = None

    write_pid_file(pid_path)

    threading.Thread(target=_bulk_sender_loop, daemon=True, name="bulk-sender").start()

    remote = fetch_remote_settings()
    if remote:
        if "SIDE_CAMERA" in remote:
            sc = remote["SIDE_CAMERA"]
            if "bitrate" in sc:
                bitrate = int(sc["bitrate"])
            if "flip" in sc:
                if (parsed := parse_bool(sc["flip"])) is not None:
                    flip = parsed
        if "BUSINESS_HOUR" in remote:
            bh = remote["BUSINESS_HOUR"]
            business_start = parse_time(bh.get("start-time", "07:00"))
            business_end = parse_time(bh.get("end-time", "22:00"))

    if args["ignore_hours"]:
        business_start = business_end = None

    if business_start is not None and business_end is not None:
        now = datetime.now()
        session_date = f"{now:%Y%m%d}"
        start_h, start_m = business_start
        end_h, end_m = business_end

        if not is_within_business_hours(now, start_h, start_m, end_h, end_m):
            logging.info("Outside business hours, scheduling for start-time")
            schedule_at(start_h, start_m, script_path)
            sys.exit(0)

        duration = seconds_until_end_time(now, end_h, end_m)
        logging.info("Recording to %s until end-time (%ds remaining)", base_dir, duration)

        run_recorder(
            base_dir=base_dir,
            flip=flip,
            bitrate=bitrate,
            fps=args["fps"],
            width=args["width"],
            height=args["height"],
            gop=args["gop"],
            duration_seconds=duration,
        )
        logging.info("Recording session ended")
        _attempt_upload_or_reschedule(session_date, base_dir, remote, script_path)
        sys.exit(0)

    if not args["ignore_hours"] and not is_recording_hours():
        sys.exit(0)

    logging.info("Recording to %s", base_dir)

    while True:
        run_recorder(
            base_dir=base_dir,
            flip=flip,
            bitrate=bitrate,
            fps=args["fps"],
            width=args["width"],
            height=args["height"],
            gop=args["gop"],
        )
        logging.warning("Capture stopped, restarting in 2 seconds...")
        time.sleep(2)


if __name__ == "__main__":
    main()
