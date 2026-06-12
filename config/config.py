"""Config loading from config.conf."""

import configparser
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.conf"


def load_config(config_path: Path | None = None) -> dict:
    """Load config from config.conf, with sensible defaults."""
    path = config_path or CONFIG_PATH
    defaults = {
        "base_dir": str(Path.home() / "recordings"),
        "flip": False,
        "segment_seconds": 300,
        "bitrate": 2_097_152,
        "fps": 25,
        "width": 1280,
        "height": 720,
        "gop": 125,
        "ignore_hours": False,
    }
    if not path.exists():
        return defaults

    cp = configparser.ConfigParser()
    cp.read(path)
    if "recorder" not in cp:
        return defaults

    r = cp["recorder"]
    return {
        "base_dir": Path(r.get("base_dir", defaults["base_dir"])).expanduser(),
        "flip": r.getboolean("flip", defaults["flip"]),
        "segment_seconds": r.getint("segment_seconds", defaults["segment_seconds"]),
        "bitrate": r.getint("bitrate", defaults["bitrate"]),
        "fps": r.getint("fps", defaults["fps"]),
        "width": r.getint("width", defaults["width"]),
        "height": r.getint("height", defaults["height"]),
        "gop": r.getint("gop", defaults["gop"]),
        "ignore_hours": r.getboolean("ignore_hours", defaults["ignore_hours"]),
    }
