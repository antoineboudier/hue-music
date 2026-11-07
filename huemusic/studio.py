"""huemusic studio: preview a preset in the browser, without the lights.

`python -m huemusic studio [preset]` starts a small web server on
127.0.0.1 and opens the page. The whole show is rendered up front with the
same engine as live playback (so what you see is exactly what the lights
would do) and sent to the page as one block of frames. The page then plays
the song's MP3 and animates the lights in sync with it.

The page polls the preset file and re-renders when you save it, so you can
keep it open next to your editor.

Endpoints (all local):
  GET  /                      the page (static/studio.html)
  GET  /api/presets           list of presets
  GET  /api/show?preset=x     rendered show: lights, sections, cues, frames
  GET  /api/mtime?preset=x    last modification time of the preset file
  GET  /api/audio?preset=x    the song's audio file (supports seeking)
  POST /api/refresh-lights    re-read lights + positions from the bridge
  POST /api/positions         save edited positions locally (config.json)
  POST /api/push-positions    write the local positions to the bridge's area
  POST /api/identify          blink the real light behind a channel
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from .engine import Show
from .preset import Preset, PresetError, load

STATIC = Path(__file__).parent / "static"
FPS = 30
_NAME = re.compile(r"^[\w\-]+$")


class NotFound(Exception):
    pass


def preset_file(name: str) -> Path:
    if not _NAME.match(name or ""):
        raise NotFound(f"invalid preset name {name!r}")
    path = config.PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        raise NotFound(f"preset not found: {name}")
    return path


def audio_file(preset: Preset) -> Path | None:
    if not preset.audio:
        return None
    p = Path(preset.audio)
    if not p.is_absolute():
        p = config.ROOT / p
    return p if p.exists() else None


def list_presets() -> list[dict]:
    out = []
    for path in sorted(config.PRESETS_DIR.glob("*.yaml"), key=lambda p: p.stem):
        entry = {"name": path.stem, "title": path.stem, "artist": "", "description": "", "based_on": None}
        try:
            p = load(path)
            entry.update(title=p.title, artist=p.artist, description=p.description, based_on=p.based_on)
        except PresetError as e:
            entry["error"] = str(e)
        out.append(entry)
    return out


def preset_mtime(name: str) -> float:
    """Latest modification time of a preset and the files it's based on."""
    path = preset_file(name)
    try:
        files = load(path).files
    except PresetError:
        files = [path]  # broken file: its own time is enough to trigger a reload (and show the error)
    return max(f.stat().st_mtime for f in files if f.exists())


def render_show(name: str, fps: int = FPS) -> dict:
    """Render a whole preset to frames: bytes [frame][channel][r, g, b]."""
    path = preset_file(name)
    preset = load(path)
    cfg = config.load()
    real = bool(cfg["hue"].get("channels"))
    show = Show(preset, config.channels(cfg))
    duration = show.duration or 0.0
    order = show.channels
    n = int(duration * fps) + 1
    buf = bytearray(n * len(order) * 3)
    i = 0
    for f in range(n):
        frame, _ = show.render(f / fps)
        for c in order:
            for v in frame[c.id]:
                buf[i] = int(max(0.0, min(1.0, v)) * 255 + 0.5)
                i += 1
    end = duration or float("inf")
    return {
        "name": name,
        "title": preset.title,
        "artist": preset.artist,
        "bpm": preset.grid.bpm,
        "first_beat": preset.grid.first_beat,
        "duration": duration,
        "fps": fps,
        "mtime": max(f.stat().st_mtime for f in preset.files),
        "description": preset.description,
        "based_on": preset.based_on,
        "audio": audio_file(preset) is not None,
        "area": cfg["hue"].get("entertainment_name") if real else None,
        "editable": real,
        "positions_modified": bool(cfg["hue"].get("positions_modified")),
        "channels": [{"id": c.id, "name": c.name.strip(), "x": c.x, "y": c.y, "z": c.z} for c in order],
        "sections": [{"name": k, "start": a, "end": min(b, end)} for k, (a, b) in preset.sections.items()],
        "cues": [
            {"label": cue.label, "effect": cue.effect.name, "start": cue.start, "end": cue.end,
             "blend": cue.blend, "lights": [c.id for c in chs]}
            for cue, chs in show.cue_targets
        ],
        "warnings": show.warnings,
        "frames": base64.b64encode(bytes(buf)).decode("ascii"),
    }


def _bridge_area():
    from .hue import Bridge

    cfg = config.load()
    if not config.hue_ready(cfg):
        raise NotFound("Hue isn't set up: run `python -m huemusic setup-hue` first")
    h = cfg["hue"]
    bridge = Bridge(h["bridge_ip"], h["app_key"])
    area = next((a for a in bridge.entertainment_areas() if a["id"] == h["entertainment_id"]), None)
    if area is None:
        raise NotFound("the entertainment area no longer exists: run setup-hue again")
    return cfg, bridge, area


def refresh_lights() -> dict:
    """Re-read light names and positions from the bridge (discards local edits)."""
    cfg, bridge, area = _bridge_area()
    cfg["hue"]["channels"] = bridge.channel_list(area)
    cfg["hue"]["positions_modified"] = False
    config.save(cfg)
    return {"channels": len(cfg["hue"]["channels"])}


def save_positions(updates: dict) -> dict:
    """Store edited (x, y) positions locally, in config.json. Nothing is sent to the bridge."""
    cfg = config.load()
    chs = {c["id"]: c for c in cfg["hue"].get("channels") or []}
    if not chs:
        raise NotFound("no lights configured: run `python -m huemusic setup-hue` first")
    clamp = lambda v: max(-1.0, min(1.0, round(float(v), 4)))
    for cid, p in updates.items():
        c = chs.get(int(cid))
        if c is not None:
            c["x"], c["y"] = clamp(p["x"]), clamp(p["y"])
    cfg["hue"]["positions_modified"] = True
    config.save(cfg)
    return {"ok": True}


def push_positions() -> dict:
    """Write the local positions to the entertainment area on the bridge."""
    cfg, bridge, area = _bridge_area()
    if area.get("status") == "active":
        raise NotFound("the area is streaming right now (huemusic play or Hue Sync): stop it first")
    service = {ch["channel_id"]: ch["members"][0]["service"]["rid"]
               for ch in area.get("channels", []) if len(ch.get("members") or []) == 1}
    positions = {service[c["id"]]: (c["x"], c["y"]) for c in cfg["hue"]["channels"] if c["id"] in service}
    updated, skipped = bridge.set_positions(area, positions)
    refresh_lights()  # read back what the bridge now has
    return {"updated": updated, "skipped": len(skipped)}


def identify(channel_id: int) -> dict:
    """Blink the real light behind a channel."""
    _, bridge, area = _bridge_area()
    ch = next((c for c in area.get("channels", []) if c["channel_id"] == channel_id), None)
    if ch is None or not ch.get("members"):
        raise NotFound(f"channel {channel_id} not found on the bridge")
    bridge.identify(bridge.light_for_service(ch["members"][0]["service"]["rid"]))
    return {"ok": True}


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """'bytes=START-END' -> (start, end) inclusive, or None for the whole file."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or (not m[1] and not m[2]):
        return None
    if m[1]:
        start = int(m[1])
        end = int(m[2]) if m[2] else size - 1
    else:  # suffix range: last N bytes
        start, end = max(0, size - int(m[2])), size - 1
    end = min(end, size - 1)
    if start > end:
        return None
    return start, end


class Handler(BaseHTTPRequestHandler):
    server_version = "huemusic-studio"

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def _guard(self, fn) -> None:
        try:
            fn()
        except PresetError as e:
            self._json({"error": str(e)}, 422)
        except NotFound as e:
            self._json({"error": str(e)}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # show the message in the page rather than hang
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        name = parse_qs(url.query).get("preset", [""])[0]

        def route():
            if url.path == "/":
                self._send(200, (STATIC / "studio.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/presets":
                self._json(list_presets())
            elif url.path == "/api/show":
                self._json(render_show(name))
            elif url.path == "/api/mtime":
                self._json({"mtime": preset_mtime(name)})
            elif url.path == "/api/audio":
                self._audio(name)
            else:
                raise NotFound(url.path)

        self._guard(route)

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        def body() -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        routes = {
            "/api/refresh-lights": lambda: refresh_lights(),
            "/api/positions": lambda: save_positions(body().get("positions", {})),
            "/api/push-positions": lambda: push_positions(),
            "/api/identify": lambda: identify(int(body()["channel"])),
        }
        if path in routes:
            self._guard(lambda: self._json(routes[path]()))
        else:
            self._json({"error": "not found"}, 404)

    def _audio(self, name: str) -> None:
        path = audio_file(load(preset_file(name)))
        if path is None:
            raise NotFound("this preset has no audio file (track.audio)")
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = parse_range(self.headers.get("Range"), size)
        start, end = rng if rng else (0, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(64 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def serve(preset: str | None = None, port: int = 8765, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/" + (f"?preset={preset}" if preset else "")
    print(f"huemusic studio running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
