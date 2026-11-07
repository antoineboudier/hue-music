"""Spotify: login, devices, track lookup, and a clock that follows playback.

Uses the Authorization Code + PKCE flow, so only a Client ID is needed (no
secret in the config). The first login opens your browser; the token is then
cached in `.spotify_cache` and refreshed automatically.
"""
from __future__ import annotations

import collections
import statistics
import threading
import time

from .config import SPOTIFY_CACHE

SCOPES = "user-read-playback-state user-modify-playback-state"


class SpotifyError(RuntimeError):
    pass


def client(cfg: dict):
    import spotipy
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyPKCE

    sc = cfg["spotify"]
    if not sc.get("client_id"):
        raise SpotifyError("no Spotify client id configured: run `huemusic setup-spotify`")
    auth = SpotifyPKCE(
        client_id=sc["client_id"],
        redirect_uri=sc["redirect_uri"],
        scope=SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(SPOTIFY_CACHE)),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=5, retries=1)


def find_device(sp, wanted: str | None) -> dict:
    devices = sp.devices().get("devices", [])
    if not devices:
        raise SpotifyError("no Spotify device found: open Spotify on your PC/phone or wake up the speaker")
    if wanted:
        for d in devices:
            if wanted.lower() in d["name"].lower():
                return d
        names = ", ".join(d["name"] for d in devices)
        raise SpotifyError(f"no device matching {wanted!r} (available: {names})")
    for d in devices:
        if d.get("is_active"):
            return d
    if len(devices) == 1:
        return devices[0]
    names = ", ".join(d["name"] for d in devices)
    raise SpotifyError(f"several devices available, pick one with --device (available: {names})")


def resolve_track(sp, title: str, artist: str, uri: str | None) -> dict:
    if uri:
        return sp.track(uri)
    main_artist = artist.split("&")[0].split(",")[0].strip()
    res = sp.search(q=f"track:{title} artist:{main_artist}", type="track", limit=5)
    items = res.get("tracks", {}).get("items", [])
    if not items:
        raise SpotifyError(f"couldn't find '{title}' by {artist} on Spotify: set track.spotify_uri in the preset")
    return items[0]


class FreeClock:
    """Plain wall clock, used when playing without Spotify (preview/testing)."""

    label = "free-running clock"

    def __init__(self):
        self._offset = 0.0
        self._paused_at: float | None = None

    def start(self, position: float) -> None:
        self._offset = position - time.monotonic()

    def position(self) -> float | None:
        if self._paused_at is not None:
            return self._paused_at
        return time.monotonic() + self._offset

    def toggle_pause(self) -> None:
        if self._paused_at is None:
            self._paused_at = self.position()
        else:
            self.start(self._paused_at)
            self._paused_at = None

    def stop(self, pause_music: bool = False) -> None:
        pass


class SpotifyClock:
    """Estimates the song position locally, corrected by polling Spotify.

    Song position = monotonic() + offset. A background thread polls
    `current_playback` (fast at first, then every 2 s), converts each reading
    to an offset sample (using the midpoint of the request as the reading
    time), and nudges our offset toward the median of recent samples. Big
    jumps (> 0.5 s) are treated as seeks and applied immediately.
    """

    def __init__(self, sp, track_uri: str, device_id: str):
        self.sp, self.uri, self.device_id = sp, track_uri, device_id
        self.label = "spotify"
        self._lock = threading.Lock()
        self._offset = 0.0
        self._paused_at: float | None = None
        self._samples: collections.deque[float] = collections.deque(maxlen=5)
        self._ended = False
        self._stop = threading.Event()
        self._started = 0.0
        self.last_error: str | None = None

    def start(self, position: float) -> None:
        self.sp.start_playback(device_id=self.device_id, uris=[self.uri], position_ms=int(max(0, position) * 1000))
        self._started = time.monotonic()
        self._offset = position - self._started
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll()
                self.last_error = None
            except Exception as e:  # network hiccup: keep running on our own clock
                self.last_error = str(e)[:60]
            fast = time.monotonic() - self._started < 8
            self._stop.wait(0.5 if fast else 2.0)

    def _poll(self) -> None:
        t0 = time.monotonic()
        pb = self.sp.current_playback()
        t1 = time.monotonic()
        item = (pb or {}).get("item") or {}
        if item.get("uri") != self.uri:
            if t1 - self._started > 5:  # grace period while Spotify switches tracks
                self._ended = True
            return
        progress = pb["progress_ms"] / 1000
        with self._lock:
            if not pb.get("is_playing"):
                self._paused_at = progress
                self._samples.clear()
                return
            sample = progress - (t0 + t1) / 2
            if self._paused_at is not None or abs(sample - self._offset) > 0.5:
                self._paused_at = None
                self._offset = sample
                self._samples.clear()
            self._samples.append(sample)
            target = statistics.median(self._samples)
            self._offset += 0.3 * (target - self._offset)

    def position(self) -> float | None:
        if self._ended:
            return None
        with self._lock:
            if self._paused_at is not None:
                return self._paused_at
            return time.monotonic() + self._offset

    def toggle_pause(self) -> None:
        with self._lock:
            paused = self._paused_at is not None
            pos = self._paused_at if paused else time.monotonic() + self._offset
        try:
            if paused:
                self.sp.start_playback(device_id=self.device_id)
                with self._lock:
                    self._offset = pos - time.monotonic()
                    self._paused_at = None
            else:
                self.sp.pause_playback(device_id=self.device_id)
                with self._lock:
                    self._paused_at = pos
        except Exception as e:
            self.last_error = str(e)[:60]

    def stop(self, pause_music: bool = False) -> None:
        self._stop.set()
        if pause_music and not self._ended:
            try:
                self.sp.pause_playback(device_id=self.device_id)
            except Exception:
                pass
