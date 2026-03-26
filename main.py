#!/usr/bin/env python3
"""
Pi camera recorder: captures video via rpicam-vid and segments into MP4 files.
Fetches settings from remote API (SIDE_CAMERA, BUSINESS_HOUR).
Uses at(1) to schedule recording within business hours.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from api import fetch_remote_settings
from config import CONFIG_PATH, load_config
from recorder import run_recorder
from schedule import (
    is_within_business_hours,
    parse_time,
    schedule_at,
    seconds_until_end_time,
)

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


def parse_args() -> dict:
    parser = argparse.ArgumentParser(description="Record Pi camera video in 5-minute segments")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to config file")
    parser.add_argument("--ignore-hours", action="store_true", help="Record at any hour")
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=None,
        help="Path to write pid file (default: <project_dir>/.pid)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    return {
        "base_dir": Path(cfg["base_dir"]).resolve(),
        "flip": cfg["flip"],
        "segment_seconds": cfg["segment_seconds"],
        "bitrate": cfg["bitrate"],
        "fps": cfg["fps"],
        "width": cfg["width"],
        "height": cfg["height"],
        "gop": cfg["gop"],
        "ignore_hours": args.ignore_hours or cfg["ignore_hours"],
        "pid_file": args.pid_file,
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


def main() -> None:
    setup_logging()
    args = parse_args()
    base_dir = args["base_dir"]
    pid_path = (
        Path(args["pid_file"]).expanduser().resolve()
        if args["pid_file"]
        else (Path(__file__).resolve().parent / ".pid")
    )
    flip = args["flip"]
    bitrate = args["bitrate"]
    segment_seconds = args["segment_seconds"]
    business_start: tuple[int, int] | None = None
    business_end: tuple[int, int] | None = None

    write_pid_file(pid_path)

    remote = fetch_remote_settings()
    if remote:
        if "SIDE_CAMERA" in remote:
            sc = remote["SIDE_CAMERA"]
            if "bitrate" in sc:
                bitrate = int(sc["bitrate"])
            if "chunk-duration" in sc:
                segment_seconds = int(sc["chunk-duration"]) * 60
            if "flip" in sc:
                if (parsed := parse_bool(sc["flip"])) is not None:
                    flip = parsed
        if "BUSINESS_HOUR" in remote:
            bh = remote["BUSINESS_HOUR"]
            business_start = parse_time(bh.get("start-time", "07:00"))
            business_end = parse_time(bh.get("end-time", "22:00"))

    if args["ignore_hours"]:
        business_start = business_end = None

    script_path = Path(__file__).resolve()

    if business_start is not None and business_end is not None:
        now = datetime.now()
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
            segment_seconds=segment_seconds,
            bitrate=bitrate,
            fps=args["fps"],
            width=args["width"],
            height=args["height"],
            gop=args["gop"],
            duration_seconds=duration,
        )
        logging.info("Recording session ended, scheduling next run")
        schedule_at(start_h, start_m, script_path)
        sys.exit(0)

    if not args["ignore_hours"] and not is_recording_hours():
        sys.exit(0)

    logging.info("Recording to %s", base_dir)

    while True:
        run_recorder(
            base_dir=base_dir,
            flip=flip,
            segment_seconds=segment_seconds,
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
