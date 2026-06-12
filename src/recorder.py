"""Picamera2-based recording with Python-controlled duration and segmented output.

Focus: produce constant-frame-rate (CFR) H.264 MP4 by hard-locking capture pacing
via FrameDurationLimits (microseconds per frame).
"""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from .rename import rename_segment

_OVERLAY_W = 380
_OVERLAY_H = 42
_OVERLAY_PAD = 20


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


def _segment_loop(
    splitter,
    base_dir: Path,
    segment_seconds: int,
    duration_seconds: int | None,
    stop_event: threading.Event,
    done_event: threading.Event,
) -> None:
    """Run segment splitting in a loop until duration or stop."""
    segment_index = 0
    start_time = time.monotonic()

    while not stop_event.is_set():
        remaining = duration_seconds - int(time.monotonic() - start_time) if duration_seconds else None
        if remaining is not None and remaining <= 0:
            break
        sleep_for = segment_seconds
        if remaining is not None and remaining < segment_seconds:
            sleep_for = remaining
        if stop_event.wait(timeout=sleep_for):
            break
        if duration_seconds and (time.monotonic() - start_time) >= duration_seconds:
            break
        prev_path = base_dir / f"video_{segment_index:06d}.mp4"
        segment_index += 1
        new_path = base_dir / f"video_{segment_index:06d}.mp4"
        try:
            from picamera2.outputs import PyavOutput

            splitter.split_output(PyavOutput(str(new_path)))
            if prev_path.exists():
                rename_segment(prev_path)
        except Exception:
            break
    done_event.set()


def run_recorder(
    base_dir: Path,
    flip: bool,
    segment_seconds: int,
    bitrate: int,
    fps: int,
    width: int,
    height: int,
    gop: int,
    duration_seconds: int | None = None,
) -> None:
    """
    Record using picamera2 with H264Encoder.
    Uses SplittableOutput for segment switching and Python timing for reliable duration.
    """
    try:
        from libcamera import Transform
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import PyavOutput, SplittableOutput
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
    first_segment = base_dir / "video_000000.mp4"

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
    # Request keyframes every gop frames for clean segment boundaries
    encoder.iperiod = gop

    logging.info(
        "Recorder settings: %dx%d @ %d fps (FrameDurationLimits=%dus), bitrate=%d bps, gop=%d, flip=%s",
        width,
        height,
        fps,
        frame_us,
        bitrate,
        gop,
        flip,
    )

    output = SplittableOutput(output=PyavOutput(str(first_segment)))
    picam2.start_recording(encoder, output)

    stop_event = threading.Event()
    done_event = threading.Event()
    segment_thread = threading.Thread(
        target=_segment_loop,
        args=(
            output,
            base_dir,
            segment_seconds,
            duration_seconds,
            stop_event,
            done_event,
        ),
        daemon=True,
    )
    segment_thread.start()

    try:
        if duration_seconds:
            time.sleep(duration_seconds)
        else:
            while True:
                time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        done_event.wait(timeout=segment_seconds + 10)
        picam2.stop_recording()
        # Rename any remaining video_*.mp4 (the final segment we were writing to)
        for path in sorted(base_dir.glob("video_*.mp4")):
            rename_segment(path)
