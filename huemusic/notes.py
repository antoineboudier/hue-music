"""Note/sound detection, for the studio's "sounds" track.

Finds where sounds start in the melodic part of the song (drums removed by
harmonic/percussive separation), with a strength and a pitch class for
each. It's a visual aid for placing hand-written patterns: timing is good to
~±50 ms, and the pitch is the dominant note at that moment (in a full mix
that can be the chord rather than the melody).

Results are cached next to the other analysis files, keyed by the audio
file's size and modification time.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import config

SR = 22050
HOP = 256
PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _cache_path(audio: Path) -> Path:
    return config.ROOT / "analysis" / f"{audio.stem}.notes.json"


def detect(audio: Path) -> list[dict]:
    import librosa

    y, sr = librosa.load(str(audio), sr=SR, mono=True)
    yh = librosa.effects.harmonic(y, margin=3.0)
    env = librosa.onset.onset_strength(y=yh, sr=sr, hop_length=HOP)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, delta=0.08, wait=2)
    chroma = librosa.feature.chroma_cqt(y=yh, sr=sr, hop_length=HOP)
    ref = float(np.percentile(env[frames], 95)) if len(frames) else 1.0
    notes = []
    for f in frames:
        c = chroma[:, f:f + 6].mean(axis=1)
        notes.append({
            "t": round(float(librosa.frames_to_time(f, sr=sr, hop_length=HOP)), 3),
            "s": round(min(1.0, float(env[f]) / (ref or 1.0)), 2),   # strength 0..1
            "pc": int(np.argmax(c)),                                  # pitch class 0=C .. 11=B
        })
    return notes


def notes_for(audio: Path) -> list[dict]:
    """Cached detection (computed on first use, ~20-30 s)."""
    st = audio.stat()
    key = {"file": audio.name, "size": st.st_size, "mtime": st.st_mtime}
    cache = _cache_path(audio)
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("key") == key:
                return data["notes"]
        except (ValueError, KeyError):
            pass
    notes = detect(audio)
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"key": key, "notes": notes}), encoding="utf-8")
    return notes
