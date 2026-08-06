#!/bin/bash
# Clear related at jobs, then start main.py
# Use this for cron @reboot or manual startup

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Clear queue-'a' (recording-schedule) at jobs only; leave queue-'u' (upload-retry) jobs intact
if command -v atq >/dev/null 2>&1; then
  atq 2>/dev/null | awk '$(NF-1)=="a"{print $1}' | grep -E '^[0-9]+$' | xargs -r atrm 2>/dev/null || true
fi

exec python3 main.py "$@"
