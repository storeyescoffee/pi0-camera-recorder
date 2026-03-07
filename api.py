"""Remote settings API."""

import json
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://panel.storeyes.io/api/device-gw"
SETTINGS_URL = f"{BASE_URL}/settings?include=side_camera,business_hour"
SIDE_VIDEOS_URL = f"{BASE_URL}/side-videos"

CACHE_DIR = Path(__file__).resolve().parent / "caches"
SETTINGS_CACHE_PATH = CACHE_DIR / "settings.json"


def _get_pi_serial() -> str | None:
    """Read Raspberry Pi serial ID from /proc/cpuinfo."""
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        for line in cpuinfo.splitlines():
            if line.strip().lower().startswith("serial"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def fetch_remote_settings() -> dict | None:
    """
    Fetch settings from API. On success, save to caches/settings.json.
    Always try API first; on failure, use cache as fallback.
    """
    try:
        headers = {}
        if serial := _get_pi_serial():
            headers["X-DEVICE-ID"] = serial
        req = urllib.request.Request(SETTINGS_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_CACHE_PATH.write_text(json.dumps(data, indent=2))
            return data
    except Exception as e:
        print(f"[WARN] Could not fetch remote settings: {e}", file=sys.stderr)
        try:
            if SETTINGS_CACHE_PATH.exists():
                return json.loads(SETTINGS_CACHE_PATH.read_text())
        except Exception as cache_err:
            print(f"[WARN] Could not load settings cache: {cache_err}", file=sys.stderr)
        return None


def _request_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if serial := _get_pi_serial():
        headers["X-DEVICE-ID"] = serial
    return headers


def post_side_video(date: str, hour: int, name: str) -> dict | None:
    """
    POST a side video to the API after renaming.
    date: ISO date (e.g. 2025-03-02)
    hour: 0-23
    name: max 255 chars
    Returns response body on 201, None on failure.
    """
    if not _get_pi_serial():
        print("[WARN] Cannot POST side-video: no device ID", file=sys.stderr)
        return None

    body = json.dumps({"date": date, "hour": hour, "name": name[:255]})
    try:
        req = urllib.request.Request(
            SIDE_VIDEOS_URL,
            data=body.encode(),
            headers=_request_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 201:
                return json.load(resp)
    except Exception as e:
        print(f"[WARN] Could not POST side-video: {e}", file=sys.stderr)
    return None
