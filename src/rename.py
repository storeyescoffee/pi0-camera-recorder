"""Rename a single video segment to DDMMYYYY_HHMMSS.mp4."""

import re
import subprocess
from pathlib import Path
from datetime import datetime


def _get_birth_time_linux(path: Path) -> float | None:
    """Get file birth time via stat (GNU); Python's stat() often doesn't have it on Linux."""
    try:
        out = subprocess.run(
            ["stat", "-c", "%W", path],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            t = float(out.stdout.strip())
            if t > 0:
                return t
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _parse_renamed_stem(stem: str) -> tuple[str, int] | None:
    """
    Parse DDMMYYYY_HHMMSS or DDMMYYYY_HHMMSS_N to (date_iso, hour).
    Returns None if format does not match.
    """
    m = re.match(r"^(\d{2})(\d{2})(\d{4})_(\d{2})(\d{2})(\d{2})(?:_\d+)?$", stem)
    if not m:
        return None
    d, mo, y, h, _mi, _s = m.groups()
    return f"{y}-{mo}-{d}", int(h)


def _get_timestamp(path: Path) -> datetime:
    """Get file birth time; fallback to mtime if birth not available."""
    st = path.stat()
    birth = getattr(st, "st_birthtime", None)
    if birth and birth > 0:
        return datetime.fromtimestamp(birth)
    birth = _get_birth_time_linux(path)
    if birth is not None:
        return datetime.fromtimestamp(birth)
    return datetime.fromtimestamp(st.st_mtime)


def rename_segment(path: Path, timestamp: datetime | None = None) -> bool:
    """
    Rename a single video_*.mp4 file to DDMMYYYY_HHMMSS.mp4.
    Uses the given timestamp (time of the segment's first frame) if provided,
    otherwise falls back to file birth time.
    Returns True if renamed, False if skipped (file does not exist or wrong pattern).
    """
    path = Path(path)
    if not path.exists() or not path.name.startswith("video_"):
        return False

    ts = timestamp or _get_timestamp(path)
    base_dir = path.parent
    new_name = base_dir / f"{ts:%d%m%Y_%H%M%S}.mp4"

    if new_name.exists() and new_name != path:
        i = 1
        stem = ts.strftime("%d%m%Y_%H%M%S")
        while (base_dir / f"{stem}_{i}.mp4").exists():
            i += 1
        new_name = base_dir / f"{stem}_{i}.mp4"

    print(f"Renaming: {path.name} → {new_name.name}")
    path.rename(new_name)

    stem = new_name.stem
    if parsed := _parse_renamed_stem(stem):
        date_iso, hour = parsed
        from api import append_side_video_to_csv
        append_side_video_to_csv(date=date_iso, hour=hour, name=stem)

    return True
