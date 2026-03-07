"""rpicam-vid + ffmpeg recording pipeline."""

import subprocess
import threading
import time
from pathlib import Path


def _watch_segment_list(
    segment_list_path: Path, base_dir: Path, ffmpeg_proc: subprocess.Popen, rename_fn
) -> None:
    """Watch segment list and rename each completed segment immediately."""
    seen: set[str] = set()
    while ffmpeg_proc.poll() is None:
        seen = _process_segment_list(segment_list_path, base_dir, seen, rename_fn)
        time.sleep(0.5)
    _process_segment_list(segment_list_path, base_dir, seen, rename_fn)


def _process_segment_list(
    path: Path, base_dir: Path, seen: set[str], rename_fn
) -> set[str]:
    """Process segment list and rename any new completed segments. Returns updated seen set."""
    try:
        if path.exists():
            for line in path.read_text().strip().splitlines():
                name = line.strip()
                if name and name not in seen:
                    seen = seen | {name}
                    filepath = base_dir / name
                    if filepath.exists():
                        rename_fn(filepath)
    except Exception:
        pass
    return seen


def run_recorder(
    base_dir: Path,
    segment_seconds: int,
    bitrate: int,
    fps: int,
    width: int,
    height: int,
    gop: int,
    duration_seconds: int | None = None,
) -> None:
    """Run rpicam-vid piping to ffmpeg for segmented output. Renames each segment as soon as it's complete."""
    base_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = str(base_dir / "video_%06d.mp4")
    segment_list_path = base_dir / ".segment_list.txt"

    rpicam_args = [
        "rpicam-vid",
        "--nopreview",
        "--codec", "h264",
        "--inline",
        "--rotation", "180",
        "-g", str(gop),
        "--bitrate", str(bitrate),
        "--framerate", str(fps),
        "--width", str(width),
        "--height", str(height),
        "--mode", "2304:1296",
        "-t", str(duration_seconds) if duration_seconds else "0",
        "-o", "-",
    ]

    rpicam = subprocess.Popen(
        rpicam_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    ffmpeg = subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
            "-f", "h264", "-i", "-",
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(segment_seconds),
            "-reset_timestamps", "1",
            "-movflags", "+faststart",
            "-segment_list", str(segment_list_path),
            "-segment_list_type", "flat",
            output_pattern,
        ],
        stdin=rpicam.stdout,
        stderr=subprocess.PIPE,
    )

    rpicam.stdout.close()

    from rename import rename_segment
    watcher = threading.Thread(
        target=_watch_segment_list,
        args=(segment_list_path, base_dir, ffmpeg, rename_segment),
        daemon=True,
    )
    watcher.start()
    rpicam.wait()
    ffmpeg.stdin.close()
    ffmpeg.wait()
