"""Business hours and at(1) scheduling."""

import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def parse_time(s: str) -> tuple[int, int]:
    """Parse 'HH:MM' to (hour, minute)."""
    parts = s.strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def is_within_business_hours(
    now: datetime, start_h: int, start_m: int, end_h: int, end_m: int
) -> bool:
    """
    Check if now is within business hours.
    Handles: 07:00-23:00 (same day) and 17:00-00:00 / 17:00-02:00 (overnight).
    """
    now_mins = now.hour * 60 + now.minute
    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m

    if start_mins < end_mins:
        return start_mins <= now_mins < end_mins
    return now_mins >= start_mins or now_mins < end_mins


def seconds_until_end_time(now: datetime, end_h: int, end_m: int) -> int:
    """
    Seconds from now until end-time. If end crosses midnight (e.g. 02:00),
    end is interpreted as next occurrence.
    """
    today_end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if end_h * 60 + end_m > now.hour * 60 + now.minute:
        delta = today_end - now
    else:
        delta = today_end + timedelta(days=1) - now
    return max(0, int(delta.total_seconds()))


def next_start_datetime(now: datetime, start_h: int, start_m: int) -> datetime:
    """Next occurrence of start-time (today or tomorrow)."""
    today_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    if today_start > now:
        return today_start
    return today_start + timedelta(days=1)


def schedule_at(start_h: int, start_m: int, script_path: Path | None = None) -> None:
    """Schedule script to run at start-time using at(1)."""
    if shutil.which("at") is None:
        print("[ERROR] 'at' command not found. Install with: apt install at", file=sys.stderr)
        sys.exit(1)

    script = script_path or Path(__file__).resolve().parent / "camera_recorder.py"
    cmd = f"python3 {script}"

    now = datetime.now()
    start_dt = next_start_datetime(now, start_h, start_m)

    if start_dt.date() == now.date():
        at_spec = f"{start_h:02d}:{start_m:02d}"
    else:
        at_spec = f"{start_h:02d}:{start_m:02d} tomorrow"

    proc = subprocess.run(
        ["at", at_spec],
        input=cmd.encode(),
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"[ERROR] Failed to schedule at {at_spec}: {proc.stderr.decode()}", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] Scheduled next run at {at_spec}")
