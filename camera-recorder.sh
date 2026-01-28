#!/bin/bash
set -euo pipefail

# -------- CONFIG --------
BASE_DIR="$HOME/recordings"
SEGMENT_SECONDS=300         # 5 minutes
BITRATE=2097152              # 2 Mbps
FPS=25
WIDTH=1280
HEIGHT=720
GOP=125                      # 5s GOP (FPS * 5)
# ------------------------

mkdir -p "$BASE_DIR"

# Get current hour (00–23)
HOUR=$(date +%H)

# Exit if not between 07:00 and 22:00 (inclusive)
if (( HOUR < 7 || HOUR >= 22 )); then
  exit 0
fi

echo "[INFO] Recording to $BASE_DIR"

while true; do
  rpicam-vid \
    --nopreview \
    --codec h264 \
    --inline \
    --rotation 180 \
    -g "$GOP" \
    --bitrate "$BITRATE" \
    --framerate "$FPS" \
    --width "$WIDTH" \
    --height "$HEIGHT" \
    --mode 2304:1296 \
    -t 0 \
    -o - | \
  ffmpeg -hide_banner -loglevel error \
    -f h264 -i - \
    -c copy \
    -f segment \
    -segment_time "$SEGMENT_SECONDS" \
    -reset_timestamps 1 \
    -movflags +faststart \
    "$BASE_DIR/video_%06d.mp4"

  echo "[WARN] Capture stopped, restarting in 2 seconds..."
  sleep 2
done
