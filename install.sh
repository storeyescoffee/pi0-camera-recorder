#!/usr/bin/env bash
set -euo pipefail

# Installs system dependencies and a @reboot cron job in /etc/cron.d.
# Run on Raspberry Pi OS / Debian-based systems:
#   chmod +x install.sh && sudo ./install.sh

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "[ERROR] Please run as root (use sudo): sudo ./install.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

echo "[INFO] Repo dir: ${REPO_DIR}"

echo "[INFO] Installing apt dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y

# - python3-picamera2: Picamera2 library + libcamera bindings
# - python3-av: PyAV bindings used by picamera2.outputs.PyavOutput to write MP4
# - python3-boto3: AWS SDK, used by src/uploader.py to push recordings to S3
# - ffmpeg: faststart remux of recordings (src/recorder.py) for progressive streaming
# - at: used by schedule.py (at/atq/atrm)
# - cron: to run at boot
apt-get install -y --no-install-recommends \
  python3 \
  python3-picamera2 \
  python3-av \
  python3-boto3 \
  ffmpeg \
  at \
  cron

echo "[INFO] Enabling services..."
systemctl enable --now cron >/dev/null 2>&1 || true
systemctl enable --now atd >/dev/null 2>&1 || true


echo "[INFO] Installing @reboot cron job in /etc/cron.d..."

CRON_FILE="/etc/cron.d/pi0-camera-recorder"

# Always run the recorder (and the at jobs it queues) as this user, never root
TARGET_USER="m0hcine24"
if ! id -u "${TARGET_USER}" >/dev/null 2>&1; then
  echo "[ERROR] User '${TARGET_USER}' does not exist" >&2
  exit 1
fi

CRON_CMD="@reboot ${TARGET_USER} cd \"${REPO_DIR}\" && /usr/bin/python3 main.py"

# cron.d files need a user field, root ownership, mode 0644 and a trailing newline.
cat > "${CRON_FILE}" <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

${CRON_CMD}
CRON
chown root:root "${CRON_FILE}"
chmod 0644 "${CRON_FILE}"

echo "[INFO] Done. ${CRON_FILE}:"
echo "       ${CRON_CMD}"

chmod +x "$SCRIPT_DIR/start.sh" "$SCRIPT_DIR/stop.sh"
