"""Picamera2-based recording: one continuous MP4 per session.

Focus: produce constant-frame-rate (CFR) H.264 MP4 by hard-locking capture pacing
via FrameDurationLimits (microseconds per frame).
"""

import logging
import secrets
import time
from datetime import datetime
from pathlib import Path

from .api import append_side_video_to_csv

_OVERLAY_W = 380
_OVERLAY_H = 42
_OVERLAY_PAD = 20

_NAME_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def new_video_path(base_dir: Path, timestamp: datetime | None = None) -> Path:
    """Build a unique YYYYMMDD_<8 random a-z0-9>.mp4 path under base_dir."""
    ts = timestamp or datetime.now()
    while True:
        suffix = "".join(secrets.choice(_NAME_ALPHABET) for _ in range(8))
        path = base_dir / f"{ts:%Y%m%d}_{suffix}.mp4"
        if not path.exists():
            return path


def _make_timestamp_callback():
    try:
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
    except ImportError:
        logging.warning("Pillow not installed; datetime overlay disabled. Run: pip install Pillow")
        return None

    font = None
    for fp in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        try:
            font = ImageFont.truetype(fp, size=24)
            break
        except (IOError, OSError):
            pass

    _cache: dict = {"text": "", "bright": None, "shadow": None}

    def _render(text: str) -> None:
        bright_img = Image.new("L", (_OVERLAY_W, _OVERLAY_H), 0)
        shadow_img = Image.new("L", (_OVERLAY_W, _OVERLAY_H), 0)
        ImageDraw.Draw(bright_img).text((0, 0), text, fill=255, font=font)
        ImageDraw.Draw(shadow_img).text((2, 2), text, fill=255, font=font)
        _cache["bright"] = np.array(bright_img) > 128
        _cache["shadow"] = np.array(shadow_img) > 128
        _cache["text"] = text

    def callback(request) -> None:
        from picamera2 import MappedArray

        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        if now != _cache["text"]:
            _render(now)
        bright, shadow = _cache["bright"], _cache["shadow"]
        if bright is None:
            return
        with MappedArray(request, "main") as m:
            frame_w = m.array.shape[1]
            x = frame_w - _OVERLAY_W - _OVERLAY_PAD
            y = _OVERLAY_PAD
            roi = m.array[y : y + _OVERLAY_H, x : x + _OVERLAY_W]
            roi[shadow] = 16
            roi[bright] = 235

    return callback


def run_recorder(
    base_dir: Path,
    flip: bool,
    bitrate: int,
    fps: int,
    width: int,
    height: int,
    gop: int,
    duration_seconds: int | None = None,
) -> None:
    """
    Record one continuous H.264 MP4 with picamera2, until duration_seconds elapses
    (or forever if None). The file is named up front as YYYYMMDD_<8 chars>.mp4.
    """
    try:
        from libcamera import Transform
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import PyavOutput
    except ImportError as e:
        raise ImportError(
            "picamera2 is required. Install with: sudo apt install python3-picamera2"
        ) from e

    # Enforce a fixed bitrate in a compatibility-friendly range.
    # (Keeps quality reasonable while avoiding huge peaks on small hardware.)
    target_min = 2_000_000
    target_max = 2_500_000
    if bitrate < target_min:
        logging.warning("Bitrate %d too low; raising to %d bps", bitrate, target_min)
        bitrate = target_min
    elif bitrate > target_max:
        logging.warning("Bitrate %d too high; lowering to %d bps", bitrate, target_max)
        bitrate = target_max

    base_dir.mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()
    video_config = {"size": (width, height), "format": "YUV420"}
    # Single config flag: flip=True rotates the image 180° (both axes)
    transform = Transform(hflip=int(flip), vflip=int(flip))
    # Hard lock frame pacing for CFR: 25 FPS => 40,000 µs per frame.
    # Do not rely only on FrameRate; FrameDurationLimits enforces timing stability.
    frame_us = int(round(1_000_000 / max(1, int(fps))))
    config = picam2.create_video_configuration(
        main=video_config,
        controls={
            "FrameDurationLimits": (frame_us, frame_us),
            # Keep FrameRate for completeness/compat, but pacing is enforced above.
            "FrameRate": fps,
        },
        transform=transform,
    )
    picam2.configure(config)

    timestamp_cb = _make_timestamp_callback()
    if timestamp_cb:
        picam2.pre_callback = timestamp_cb

    encoder = H264Encoder(bitrate=bitrate)
    # Keyframe interval in frames; gop = 2 * fps gives a 2-second GOP at 25 fps.
    encoder.iperiod = gop

    started = datetime.now()
    video_path = new_video_path(base_dir, started)

    logging.info(
        "Recording %s: %dx%d @ %d fps (FrameDurationLimits=%dus), bitrate=%d bps, gop=%d, flip=%s",
        video_path.name,
        width,
        height,
        fps,
        frame_us,
        bitrate,
        gop,
        flip,
    )

    picam2.start_recording(encoder, PyavOutput(str(video_path)))
    append_side_video_to_csv(
        date=f"{started:%Y-%m-%d}",
        hour=started.hour,
        name=video_path.stem,
    )

    try:
        if duration_seconds:
            time.sleep(duration_seconds)
        else:
            while True:
                time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        picam2.stop_recording()
        logging.info("Stopped recording %s", video_path.name)
