"""Local audio playback (MP3/WAV/FLAC) used as the show clock.

The file is decoded into memory and played through the default output via
PortAudio. The clock comes from the audio stream itself: in the callback we
remember which sample is about to reach the DAC and when, so `position()`
reflects what is actually coming out of the sound card. Bluetooth headphones
add their own delay on top, which PortAudio can't see: use the latency keys.
"""
from __future__ import annotations

import threading
import time


class LocalAudioClock:
    def __init__(self, path: str, device: str | int | None = None):
        import sounddevice as sd
        import soundfile as sf

        self._sd = sd
        self.data, self.sr = sf.read(path, dtype="float32", always_2d=True)
        self.device = device
        self.device_name = sd.query_devices(device, kind="output")["name"]
        self.label = f"local audio ({self.device_name})"
        self.duration = len(self.data) / self.sr
        self._lock = threading.Lock()
        self._frame = 0              # next frame to hand to the callback
        self._dac_frame = 0          # frame playing at time _dac_time
        self._dac_time: float | None = None
        self._paused = False
        self._ended = False
        self._smooth: float | None = None  # smoothed offset: position = monotonic() + _smooth
        self._stream = None
        self.last_error: str | None = None

    def _callback(self, out, frames, time_info, status):
        with self._lock:
            if status.output_underflow:
                self.last_error = "audio underflow"
            if self._paused:
                out.fill(0)
                return
            start = self._frame
            chunk = self.data[start:start + frames]
            n = len(chunk)
            out[:n] = chunk
            if n < frames:
                out[n:] = 0
                self._ended = n == 0
            self._frame = start + n
            self._dac_frame = start
            self._dac_time = time_info.outputBufferDacTime

    def start(self, position: float) -> None:
        self._frame = int(max(0.0, position) * self.sr)
        self._dac_frame = self._frame
        self._stream = self._sd.OutputStream(
            samplerate=self.sr, channels=self.data.shape[1], dtype="float32",
            device=self.device, callback=self._callback, latency="low",
        )
        self._stream.start()

    def position(self) -> float | None:
        with self._lock:
            if self._ended:
                return None
            if self._dac_time is None or self._stream is None:
                return self._frame / self.sr
            if self._paused:
                return self._dac_frame / self.sr
            # Raw estimate: extrapolate from the last callback using the
            # stream's own clock. On Windows (MME) this jitters by ~+-10 ms,
            # so we follow it with a smooth monotonic clock pulled gently
            # toward it, snapping only on big jumps (start, resume, glitch).
            raw = self._dac_frame / self.sr + max(0.0, self._stream.time - self._dac_time)
            mono = time.monotonic()
            est = mono + self._smooth if self._smooth is not None else raw
            if self._smooth is None or abs(raw - est) > 0.1:
                self._smooth = raw - mono
            else:
                self._smooth += 0.03 * (raw - est)
            return mono + self._smooth

    def toggle_pause(self) -> None:
        with self._lock:
            if not self._paused:
                # resume later exactly where the listener is now
                now = self._stream.time if self._stream else 0.0
                heard = self._dac_frame + int(max(0.0, now - (self._dac_time or now)) * self.sr)
                self._frame = self._dac_frame = min(heard, len(self.data))
                self._dac_time = None
            self._smooth = None
            self._paused = not self._paused

    def stop(self, pause_music: bool = False) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def audio_duration(path: str) -> float:
    import soundfile as sf

    return sf.info(path).duration
