"""Offline audio analysis: tempo, beat grid, downbeats and a per-bar energy map.

The output is meant to *help you write a preset by hand*: it gives you the
`bpm` / `first_beat` values for the preset header, and prints an energy table
per bar so you can spot the intro, builds, drops and breakdowns.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

SR = 22050
HOP = 512


def _fit_grid(beats: np.ndarray, period: float) -> tuple[float, float]:
    """Fit a constant-tempo grid (period, phase) to detected beat times.

    Beat trackers miss beats in breakdowns, so we can't just regress on the
    list index. Instead each detected beat is assigned the integer grid index
    nearest to it, then we regress time ~ index. Repeated a few times while
    dropping outliers.
    """
    t0 = beats[0]
    keep = np.ones(len(beats), dtype=bool)
    for _ in range(4):
        idx = np.round((beats - t0) / period)
        period, t0 = np.polyfit(idx[keep], beats[keep], 1)
        resid = beats - (t0 + idx * period)
        keep = np.abs(resid) < max(0.03, 2.5 * np.median(np.abs(resid[keep])))
    return float(period), float(t0)


def _search_period(beats: np.ndarray, bpm_guess: float) -> float:
    """librosa's tempo estimate is quantized (e.g. 129.2 for a 128 BPM track).

    Scan nearby tempos and keep the one whose constant grid fits the detected
    beats best; a wrong tempo drifts and gives large residuals over 3 minutes.
    """
    best, best_err = bpm_guess, float("inf")
    for bpm in np.arange(bpm_guess - 3, bpm_guess + 3, 0.02):
        p = 60.0 / bpm
        idx = np.round((beats - beats[0]) / p)
        r = beats - idx * p
        r = r - np.median(r)
        err = float(np.median(np.abs(r)))
        if err < best_err:
            best, best_err = bpm, err
    return 60.0 / best


def _kick_envelope(y: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """Onset strength of the low end (30-150 Hz) at ~6 ms resolution.

    Computed in the time domain (zero-phase band-pass + 10 ms RMS) rather
    than from a spectrogram, whose long windows would see kicks ~30 ms early.
    """
    from scipy.signal import butter, sosfiltfilt

    hop = 128
    sos = butter(4, [30, 150], btype="bandpass", fs=sr, output="sos")
    low = sosfiltfilt(sos, y) ** 2
    win = int(0.010 * sr)
    rms = np.sqrt(np.convolve(low, np.ones(win) / win, mode="same"))[::hop]
    env = np.log1p(rms * 100)
    env = np.maximum(0.0, np.diff(env, prepend=env[0]))
    # tolerate +-1 frame of jitter
    env = np.maximum(env, np.maximum(np.roll(env, 1), np.roll(env, -1)))
    # Keep only the punchiest half of the track (drops: clean four-on-the-floor);
    # intros/breakdowns with sparse or syncopated kicks just add noise.
    dt = hop / sr
    w = int(4.0 / dt)
    activity = np.convolve(env, np.ones(w) / w, mode="same")
    env = np.where(activity >= np.median(activity), env, 0.0)
    return env, dt


def _grid_score(env: np.ndarray, dt: float, bpm: float) -> tuple[float, float]:
    """Best (score, phase) of a constant grid at `bpm` against the kick envelope."""
    p = 60.0 / bpm
    n = int(len(env) * dt / p) - 1
    phases = np.arange(0.0, p, dt)
    idx = ((phases[:, None] + np.arange(n)[None, :] * p) / dt).astype(int)
    scores = env[idx].mean(axis=1)
    i = int(np.argmax(scores))
    return float(scores[i]), float(phases[i])


def _fit_kick_grid(env: np.ndarray, dt: float, bpm_guess: float) -> tuple[float, float]:
    """Find the constant tempo + phase whose beats land on the most kicks."""
    def search(lo: float, hi: float, step: float) -> float:
        cands = np.arange(lo, hi, step)
        return float(cands[int(np.argmax([_grid_score(env, dt, b)[0] for b in cands]))])

    bpm = search(bpm_guess - 4, bpm_guess + 4, 0.05)
    bpm = search(bpm - 0.06, bpm + 0.06, 0.002)
    # Dance music is almost always an integer BPM: snap if it scores as well.
    r = float(round(bpm))
    if abs(bpm - r) < 0.15 and _grid_score(env, dt, r)[0] >= 0.9 * _grid_score(env, dt, bpm)[0]:
        bpm = r
    _, phase = _grid_score(env, dt, bpm)
    return 60.0 / bpm, phase


def analyze(path: str | Path) -> dict:
    import librosa  # heavy import, keep it local

    y, sr = librosa.load(str(path), sr=SR, mono=True)
    duration = len(y) / sr

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    _, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=HOP, units="time")
    beats = np.asarray(beats)
    # The beat tracker only gives a first tempo guess: on tracks with a strong
    # offbeat bass (e.g. eurodance) it flips between on- and off-beats. The
    # grid itself is fitted on the kick drum.
    bpm_guess = 60.0 / _search_period(beats, 60.0 / float(np.median(np.diff(beats))))
    # Kick detection on the percussive part only, so an offbeat bassline
    # (same frequency range) doesn't pull the grid half a beat off.
    kick, dt = _kick_envelope(librosa.effects.percussive(y, margin=3.0), sr)
    period, t0 = _fit_kick_grid(kick, dt, bpm_guess)
    bpm = 60.0 / period

    # Downbeat. With a kick on every beat, "the strongest beat" says nothing.
    # Instead: song sections change on bar lines, so pick the alignment (of 4)
    # where energy differs most between consecutive bars.
    S = np.abs(librosa.stft(y, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr)
    low = S[freqs < 150].sum(axis=0)
    rms = librosa.feature.rms(S=S, hop_length=HOP)[0]
    frame_t = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=HOP)
    n_beats = int((duration - t0) / period)
    beat_frame = np.searchsorted(frame_t, t0 + np.arange(n_beats + 1) * period)
    per_beat = np.array([
        [low[a:b].mean() / (low.max() + 1e-9), rms[a:b].mean() / (rms.max() + 1e-9)]
        for a, b in zip(beat_frame[:-1], beat_frame[1:])
    ]).sum(axis=1)

    def contrast(phase: int) -> float:
        n = (len(per_beat) - phase) // 4
        bars = per_beat[phase:phase + 4 * n].reshape(n, 4).mean(axis=1)
        return float(np.abs(np.diff(bars)).sum())

    phase = int(np.argmax([contrast(p) for p in range(4)]))
    first_beat = t0 + phase * period
    while first_beat - 4 * period > -0.05:  # earliest downbeat at or after ~0s
        first_beat -= 4 * period
    first_beat = max(first_beat, 0.0) if first_beat > -0.05 else first_beat

    # Per-bar energy (overall loudness + bass) to reveal song structure.
    bar_len = 4 * period
    bars = []
    b = 0
    while first_beat + b * bar_len < duration:
        start = first_beat + b * bar_len
        m = (frame_t >= start) & (frame_t < start + bar_len)
        if m.any():
            bars.append({
                "bar": b + 1,
                "time": round(start, 3),
                "energy": float(rms[m].mean()),
                "bass": float(low[m].mean()),
            })
        b += 1
    emax = max(x["energy"] for x in bars)
    bmax = max(x["bass"] for x in bars)
    for x in bars:
        x["energy"] = round(x["energy"] / emax, 3)
        x["bass"] = round(x["bass"] / bmax, 3)

    return {
        "file": Path(path).name,
        "duration": round(duration, 3),
        "bpm": round(bpm, 3),
        "first_beat": round(first_beat, 4),
        "beats_detected": [round(float(t), 3) for t in beats],
        "bars": bars,
    }


def fmt_time(t: float) -> str:
    sign = "-" if t < 0 else ""
    t = abs(t)
    return f"{sign}{int(t // 60)}:{t % 60:06.3f}"


def print_report(a: dict, every: int = 1) -> None:
    print(f"{a['file']}: {fmt_time(a['duration'])}  bpm={a['bpm']}  first_beat={a['first_beat']}s")
    print("\n  bar   time       energy                          bass")
    for x in a["bars"]:
        if (x["bar"] - 1) % every:
            continue
        e = "#" * int(x["energy"] * 30)
        bs = "=" * int(x["bass"] * 20)
        marker = "  <-- phrase" if (x["bar"] - 1) % 8 == 0 else ""
        print(f"  {x['bar']:>3}  {fmt_time(x['time'])}  {e:<30}  {bs:<20}{marker}")


def save(a: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(a, indent=1), encoding="utf-8")
