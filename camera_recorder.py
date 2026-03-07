#!/usr/bin/env python3
"""
Pi camera recorder: captures video via rpicam-vid and segments into MP4 files.
Fetches settings from remote API (SIDE_CAMERA, BUSINESS_HOUR).
Uses at(1) to schedule recording within business hours.
"""

import argparse
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


def parse_args() -> dict:
    parser = argparse.ArgumentParser(description="Record Pi camera video in 5-minute segments")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to config file")
    parser.add_argument("--ignore-hours", action="store_true", help="Record at any hour")
    args = parser.parse_args()

    cfg = load_config(args.config)
    return {
        "base_dir": Path(cfg["base_dir"]).resolve(),
        "segment_seconds": cfg["segment_seconds"],
        "bitrate": cfg["bitrate"],
        "fps": cfg["fps"],
        "width": cfg["width"],
        "height": cfg["height"],
        "gop": cfg["gop"],
        "ignore_hours": args.ignore_hours or cfg["ignore_hours"],
    }


def is_recording_hours() -> bool:
    """Return True if current hour is 07:00–22:00 inclusive."""
    return 7 <= datetime.now().hour < 22


def main() -> None:
    args = parse_args()
    base_dir = args["base_dir"]
    bitrate = args["bitrate"]
    segment_seconds = args["segment_seconds"]
    business_start: tuple[int, int] | None = None
    business_end: tuple[int, int] | None = None

    remote = fetch_remote_settings()
    if remote:
        if "SIDE_CAMERA" in remote:
            sc = remote["SIDE_CAMERA"]
            if "bitrate" in sc:
                bitrate = int(sc["bitrate"])
            if "chunk-duration" in sc:
                segment_seconds = int(sc["chunk-duration"]) * 60
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
            print("[INFO] Outside business hours, scheduling for start-time")
            schedule_at(start_h, start_m, script_path)
            sys.exit(0)

        duration = seconds_until_end_time(now, end_h, end_m)
        print(f"[INFO] Recording to {base_dir} until end-time ({duration}s remaining)")

        run_recorder(
            base_dir=base_dir,
            segment_seconds=segment_seconds,
            bitrate=bitrate,
            fps=args["fps"],
            width=args["width"],
            height=args["height"],
            gop=args["gop"],
            duration_seconds=duration,
        )
        print("[INFO] Recording session ended, scheduling next run")
        schedule_at(start_h, start_m, script_path)
        sys.exit(0)

    if not args["ignore_hours"] and not is_recording_hours():
        sys.exit(0)

    print(f"[INFO] Recording to {base_dir}")

    while True:
        run_recorder(
            base_dir=base_dir,
            segment_seconds=segment_seconds,
            bitrate=bitrate,
            fps=args["fps"],
            width=args["width"],
            height=args["height"],
            gop=args["gop"],
        )
        print("[WARN] Capture stopped, restarting in 2 seconds...")
        time.sleep(2)


if __name__ == "__main__":
    main()
