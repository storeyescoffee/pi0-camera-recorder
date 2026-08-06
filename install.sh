#!/usr/bin/env bash
set -euo pipefail

# Installs system dependencies and configures a @reboot cron job.
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
# - at: used by schedule.py (at/atq/atrm)
# - cron: to run at boot
apt-get install -y --no-install-recommends \
  python3 \
  python3-picamera2 \
  python3-av \
  python3-boto3 \
  at \
  cron

echo "[INFO] Enabling services..."
systemctl enable --now cron >/dev/null 2>&1 || true
systemctl enable --now atd >/dev/null 2>&1 || true


echo "[INFO] Installing @reboot crontab entry (idempotent)..."

CRON_MARKER="# pi0-camera-recorder"
CRON_CMD="@reboot cd \"${REPO_DIR}\" && /usr/bin/python3 main.py ${CRON_MARKER}"

# Install for a target user (default: the sudo user if present, else current user)
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || true)}"
if [[ -z "${TARGET_USER}" ]]; then
  TARGET_USER="$(id -un)"
fi

TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT

# Keep existing lines except ones we own (marked).
crontab -u "${TARGET_USER}" -l 2>/dev/null \
  | awk -v marker="${CRON_MARKER}" 'index($0, marker) == 0 { print }' \
  > "${TMP}" || true

echo "${CRON_CMD}" >> "${TMP}"
crontab -u "${TARGET_USER}" "${TMP}"

echo "[INFO] Done. Crontab for ${TARGET_USER} now includes:"
echo "       ${CRON_CMD}"

chmod +x "$SCRIPT_DIR/start.sh" "$SCRIPT_DIR/stop.sh"
