"""The real-time loop: read the clock, render a frame, send it, handle keys."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from .analysis import fmt_time
from .engine import Frame, Show
from .keys import KeyReader

HELP = "keys: q quit | space pause | + / - latency +-10ms | any other key = tap marker"


@dataclass
class Result:
    latency_ms: int
    taps: list[tuple[float, str]] = field(default_factory=list)
    reason: str = ""


def swatches(frame: Frame, order) -> str:
    """Colored blocks for the terminal (24-bit ANSI)."""
    out = []
    for c in order:
        r, g, b = (int(max(0.0, min(1.0, v)) * 255) for v in frame.get(c.id, (0, 0, 0)))
        out.append(f"\x1b[48;2;{r};{g};{b}m  ")
    return "".join(out) + "\x1b[0m"


def _line(text: str) -> None:
    sys.stdout.write("\r\x1b[2K" + text)
    sys.stdout.flush()


def run(show: Show, clock, outputs: list, *, fps: int, brightness: float,
        latency_ms: int, start: float, preview: bool) -> Result:
    grid = show.preset.grid
    res = Result(latency_ms=latency_ms)
    print(HELP)
    clock.start(start)
    period = 1.0 / fps
    next_t = time.monotonic()
    n = 0
    end = (show.duration or float("inf")) + 1.0
    try:
        with KeyReader() as keys:
            while True:
                pos = clock.position()
                if pos is None:
                    res.reason = ("Spotify stopped or changed track" if getattr(clock, "label", "") == "spotify"
                                  else "end of audio")
                    break
                t = pos - res.latency_ms / 1000
                if t > end:
                    res.reason = "end of song"
                    break
                frame, active = show.render(t)
                for o in outputs:
                    o.send(frame, brightness)

                for k in keys.poll():
                    if k in ("q", "Q", "\x1b", "\x03"):
                        res.reason = "quit"
                        raise StopIteration
                    elif k == " ":
                        clock.toggle_pause()
                    elif k in ("+", "="):
                        res.latency_ms += 10
                    elif k in ("-", "_"):
                        res.latency_ms -= 10
                    elif k.isprintable():
                        res.taps.append((t, k))
                        _line(f"  tap '{k}' at {fmt_time(t)}  bar {grid.position(t)}  "
                              f"(nearest beat {_nearest_beat(grid, t)})\n")

                if n % 3 == 0:
                    sw = swatches(frame, show.channels) + " " if preview else ""
                    sec = show.preset.section_at(t)
                    err = getattr(clock, "last_error", None)
                    status = (f"{sw}{fmt_time(max(t, 0))}  bar {grid.position(t):>6}  "
                              f"[{sec}] {', '.join(active)[:38]:<38} lat {res.latency_ms}ms")
                    if err:
                        status += f"  (spotify: {err})"
                    _line(status)
                n += 1
                next_t += period
                delay = next_t - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.2:  # fell far behind (e.g. machine was busy): resync
                    next_t = time.monotonic()
    except (StopIteration, KeyboardInterrupt):
        res.reason = res.reason or "quit"
    finally:
        clock.stop(pause_music=res.reason == "quit")
        sys.stdout.write("\n")
    return res


def _nearest_beat(grid, t: float) -> str:
    b = round(grid.beat_index(t))
    bt = grid.first_beat + b * grid.beat_len
    return f"bar {b // 4 + 1} beat {b % 4 + 1}, {t - bt:+.3f}s"
