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
  GET  /api/notes?preset=x    detected sounds (start time, strength, pitch class), cached
  POST /api/append-cue        append a cue (from tap recording) to a preset file
  POST /api/live/start|state|stop   drive the real lights from the page's playback
  POST /api/grid              save a nudged beat grid (track.first_beat)
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import threading
import time
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


def notes(name: str) -> dict:
    from .notes import PITCH_NAMES, notes_for

    path = audio_file(load(preset_file(name)))
    if path is None:
        raise NotFound("this preset has no audio file (track.audio)")
    return {"notes": notes_for(path), "names": PITCH_NAMES}


def append_cue(name: str, snippet: str) -> dict:
    """Append a cue (YAML list item, indented under `cues:`) to the end of a preset file.

    The file is re-loaded afterwards; if it doesn't have exactly one more cue
    (e.g. `cues:` isn't the last section), the original file is restored.
    """
    import yaml

    path = preset_file(name)
    original = path.read_text(encoding="utf-8")
    before = len(load(path).cues)
    own = yaml.safe_load(original) or {}
    text = re.sub(r"(?m)^cues:\s*\[\]\s*$", "cues:", original).rstrip("\n") + "\n"
    if "cues" not in own:
        text += "\ncues:\n"
    text += "\n" + snippet.rstrip("\n") + "\n"
    path.write_text(text, encoding="utf-8")
    try:
        after = len(load(path).cues)
        if after != before + 1:
            raise PresetError("couldn't add the cue at the end of the file (is `cues:` the last section?): paste it by hand")
    except PresetError:
        path.write_text(original, encoding="utf-8")
        raise
    return {"cues": after}


def set_first_beat(name: str, first_beat: float) -> dict:
    """Change the beat grid's start (track.first_beat), keeping the file's comments.

    For a variant, the value lives where the track header is defined: the
    first file (variant, then its bases) that sets `first_beat`. The preset
    is re-loaded afterwards and the file restored if it no longer loads.
    """
    import yaml

    preset = load(preset_file(name))
    target = next((f for f in preset.files
                   if "first_beat" in ((yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("track") or {})),
                  None)
    if target is None:
        raise NotFound("no track.first_beat found in this preset or its base")
    original = target.read_text(encoding="utf-8")
    value = round(float(first_beat), 4)
    new, n = re.subn(r"(?m)^(\s*first_beat:\s*)-?[\d.]+", rf"\g<1>{value}", original, count=1)
    if not n:
        raise NotFound(f"couldn't find a `first_beat:` line in {target.name}")
    target.write_text(new, encoding="utf-8")
    try:
        load(preset_file(name))
    except PresetError:
        target.write_text(original, encoding="utf-8")
        raise
    return {"first_beat": value, "file": target.name}


class Live:
    """Drives the real lights from the studio page.

    The page reports its playback position (on play/pause/seek, and as a
    heartbeat 4 times a second). A background thread extrapolates the
    position between reports, renders 50 frames per second with the same
    engine as `play`, and streams them to the bridge. If the page stops
    reporting for 3 seconds (closed tab, stopped server), streaming stops.
    """

    FPS = 50
    TIMEOUT = 3.0

    def __init__(self):
        self.lock = threading.Lock()
        self.stream = None
        self.thread: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.show: Show | None = None
        self.key = None  # (preset, preset mtime, config mtime) the show was built from
        self.t = 0.0
        self.wall = 0.0
        self.playing = False
        self.rate = 1.0
        self.offset = 0.0
        self.last_seen = 0.0
        self.brightness = 1.0
        self.error: str | None = None

    def _build(self, name: str) -> None:
        cfg_mtime = config.CONFIG_PATH.stat().st_mtime if config.CONFIG_PATH.exists() else 0
        key = (name, preset_mtime(name), cfg_mtime)
        if key != self.key:
            preset = load(preset_file(name))
            cfg = config.load()
            self.show = Show(preset, config.channels(cfg))
            self.brightness = float(cfg["playback"].get("brightness", 1.0))
            self.key = key

    def start(self, name: str) -> dict:
        from .hue import EntertainmentStream

        with self.lock:
            self._build(name)
            if self.stream is None:
                cfg = config.load()
                if not config.hue_ready(cfg):
                    raise NotFound("Hue isn't set up: run `python -m huemusic setup-hue` first")
                h = cfg["hue"]
                stream = EntertainmentStream(h["bridge_ip"], h["app_key"], h["client_key"], h["entertainment_id"])
                stream.open()
                self.stream = stream
                self.stop_evt = threading.Event()
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()
            self.error = None
            self.last_seen = time.monotonic()
            return {"on": True, "area": config.load()["hue"].get("entertainment_name")}

    def update(self, name: str, t: float, playing: bool, rate: float, offset_ms: float) -> dict:
        with self.lock:
            if self.stream is None:
                return {"on": False, "error": self.error}
            try:
                self._build(name)  # follows preset edits and position changes
            except PresetError:
                pass  # keep showing the last version that loaded
            self.t, self.wall = float(t), time.monotonic()
            self.playing, self.rate = bool(playing), float(rate)
            self.offset = float(offset_ms) / 1000
            self.last_seen = self.wall
            return {"on": True}

    def _loop(self) -> None:
        period = 1.0 / self.FPS
        nxt = time.monotonic()
        while not self.stop_evt.is_set():
            with self.lock:
                now = time.monotonic()
                if now - self.last_seen > self.TIMEOUT:
                    self.error = "stopped: the studio page stopped reporting"
                    break
                t = self.t + ((now - self.wall) * self.rate if self.playing else 0.0) - self.offset
                frame, _ = self.show.render(t)
                stream, brightness = self.stream, self.brightness
            try:
                stream.send(frame, brightness)
            except Exception as e:
                self.error = f"stream error: {e}"
                break
            nxt += period
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.monotonic()
        self._close()

    def _close(self) -> None:
        with self.lock:
            stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.send({c.id: (0.0, 0.0, 0.0) for c in self.show.channels})
            except Exception:
                pass
            stream.close()

    def stop(self) -> dict:
        self.stop_evt.set()
        th = self.thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2)
        self._close()
        return {"on": False}


LIVE = Live()


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
            elif url.path == "/api/notes":
                self._json(notes(name))
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
            "/api/append-cue": lambda: (lambda b: append_cue(b["preset"], b["yaml"]))(body()),
            "/api/grid": lambda: (lambda b: set_first_beat(b["preset"], b["first_beat"]))(body()),
            "/api/live/start": lambda: LIVE.start(body()["preset"]),
            "/api/live/state": lambda: (lambda b: LIVE.update(b["preset"], b["t"], b["playing"],
                                                              b.get("rate", 1), b.get("offset", 0)))(body()),
            "/api/live/stop": lambda: LIVE.stop(),
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
        LIVE.stop()  # turn the stream off cleanly
        server.server_close()
