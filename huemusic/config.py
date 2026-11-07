"""Local configuration (bridge credentials, Spotify client id, latency).

Stored in `config.json` at the project root. It contains secrets: it is
git-ignored, don't share it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .effects import Channel

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
SPOTIFY_CACHE = ROOT / ".spotify_cache"
PRESETS_DIR = ROOT / "presets"
TAPS_DIR = ROOT / "taps"

DEFAULTS: dict = {
    "hue": {
        "bridge_ip": None,
        "app_key": None,          # a.k.a. "username" in the v1 API
        "client_key": None,       # PSK for the entertainment (DTLS) stream
        "entertainment_id": None,
        "entertainment_name": None,
        "channels": [],           # cached from the entertainment area
    },
    "spotify": {
        "client_id": None,
        "redirect_uri": "http://127.0.0.1:8888/callback",
        "device": None,           # default device name (substring match)
    },
    "playback": {
        "fps": 50,
        "brightness": 1.0,
        "latency_ms": {},         # per device name: how late the audio is vs. Spotify's clock
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    if CONFIG_PATH.exists():
        return _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return copy.deepcopy(DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def hue_ready(cfg: dict) -> bool:
    h = cfg["hue"]
    return all(h.get(k) for k in ("bridge_ip", "app_key", "client_key", "entertainment_id"))


def channels(cfg: dict) -> list[Channel]:
    chs = cfg["hue"].get("channels") or []
    if chs:
        return [Channel(c["id"], c.get("name", ""), c.get("x", 0.0), c.get("y", 0.0), c.get("z", 0.0)) for c in chs]
    return demo_channels()


def demo_channels(n: int = 10) -> list[Channel]:
    """Fake layout used for preview when no bridge is configured."""
    return [Channel(i, f"demo {i}", -1 + 2 * i / (n - 1), 0.0, 0.0) for i in range(n)]
