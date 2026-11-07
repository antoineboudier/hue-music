"""Light effects.

An effect is a small object that, given the current time context, returns a
color for each targeted channel. Timing parameters are expressed in *beats*
(numbers) or as strings like "2 bars" / "0.5 beats" / "1.5s".

Add a new effect by subclassing `Effect`, declaring `PARAMS` and decorating
with `@effect("name")`. It then becomes usable from preset files.
"""
from __future__ import annotations

import colorsys
import math
import random
import re
from dataclasses import dataclass
from typing import Callable

RGB = tuple[float, float, float]
BLACK: RGB = (0.0, 0.0, 0.0)

BUILTIN_COLORS: dict[str, RGB] = {
    "black": BLACK,
    "white": (1.0, 1.0, 1.0),
    "warm": (1.0, 0.75, 0.45),
    "red": (1.0, 0.0, 0.0),
    "orange": (1.0, 0.4, 0.0),
    "yellow": (1.0, 0.85, 0.0),
    "green": (0.0, 1.0, 0.0),
    "cyan": (0.0, 1.0, 1.0),
    "blue": (0.0, 0.0, 1.0),
    "purple": (0.5, 0.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "pink": (1.0, 0.3, 0.6),
}


@dataclass(frozen=True)
class Channel:
    id: int
    name: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Ctx:
    t: float                  # song time (seconds)
    local: float              # seconds since the cue started
    duration: float           # cue length in seconds
    beat_len: float           # seconds per beat
    channels: list[Channel]   # targeted channels, in path order (see cue `order:`)
    pos: dict[int, float]     # channel id -> 0..1 position along the path
    cyclic: bool = False      # the path loops (circle / explicit list)

    @property
    def beat(self) -> float:
        """Beats elapsed since the cue started."""
        return self.local / self.beat_len

    @property
    def beats_total(self) -> float:
        return self.duration / self.beat_len

    @property
    def progress(self) -> float:
        if self.duration <= 0:
            return 1.0
        return min(1.0, max(0.0, self.local / self.duration))


def scale(c: RGB, k: float) -> RGB:
    return (c[0] * k, c[1] * k, c[2] * k)


def mix(a: RGB, b: RGB, k: float) -> RGB:
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def steps_elapsed(beat: float, total: float, every: float, every_end: float | None, curve: str) -> float:
    """How many steps have happened after `beat` beats, for a step length that
    goes from `every` to `every_end` beats over `total` beats.

    curve "linear":   the step *rate* ramps smoothly.
    curve "doubling": the rate doubles in equal-length stages (1 -> 1/2 ->
                      1/4 -> 1/8 beat...), like a snare roll in a build-up.
    """
    if not every_end or every_end == every or total <= 0:
        return beat / every
    r0, r1 = 1.0 / every, 1.0 / every_end
    if curve == "doubling":
        levels = max(1, round(abs(math.log2(r1 / r0))))
        factor = 2.0 if r1 > r0 else 0.5
        seg = total / (levels + 1)
        n, rate, b = 0.0, r0, beat
        for _ in range(levels):
            if b <= seg:
                return n + b * rate
            n += seg * rate
            b -= seg
            rate *= factor
        return n + b * rate
    return r0 * beat + (r1 - r0) * beat * beat / (2 * total)


EFFECTS: dict[str, type["Effect"]] = {}


def effect(name: str) -> Callable[[type["Effect"]], type["Effect"]]:
    def deco(cls: type[Effect]) -> type[Effect]:
        cls.name = name
        EFFECTS[name] = cls
        return cls
    return deco


class Effect:
    """Base class. PARAMS maps parameter name -> (kind, default).

    kinds: color, colors (one color or a list), beats, float, str, bool,
           choice:a|b|c
    """
    name = "?"
    PARAMS: dict[str, tuple[str, object]] = {}
    seed = 0

    def __init__(self, params: dict, parse: Callable[[str, object], object]):
        params = dict(params)
        if "colors" in params and "color" in self.PARAMS:
            params["color"] = params.pop("colors")
        unknown = sorted(set(params) - set(self.PARAMS))
        if unknown:
            raise ValueError(
                f"unknown parameter(s) {unknown} for effect '{self.name}' "
                f"(allowed: {sorted(self.PARAMS)})"
            )
        self.p: dict[str, object] = {}
        for key, (kind, default) in self.PARAMS.items():
            value = params.get(key, default)
            self.p[key] = None if value is None else parse(kind, value)

    def render(self, ctx: Ctx) -> dict[int, RGB]:
        raise NotImplementedError

    # helpers
    def _all(self, ctx: Ctx, color: RGB) -> dict[int, RGB]:
        return {c.id: color for c in ctx.channels}

    def _bg(self, ctx: Ctx) -> dict[int, RGB]:
        bg = self.p.get("background") or BLACK
        return self._all(ctx, bg)  # type: ignore[arg-type]


@effect("solid")
class Solid(Effect):
    """A constant color."""
    PARAMS = {"color": ("color", "white")}

    def render(self, ctx):
        return self._all(ctx, self.p["color"])


@effect("blackout")
class Blackout(Effect):
    """All targeted lights off."""
    PARAMS = {}

    def render(self, ctx):
        return self._all(ctx, BLACK)


@effect("fade")
class Fade(Effect):
    """Crossfade from one color to another over the cue's duration."""
    PARAMS = {
        "from": ("color", "black"),
        "to": ("color", "white"),
        "ease": ("choice:linear|in|out|inout", "linear"),
    }

    def render(self, ctx):
        k = ctx.progress
        ease = self.p["ease"]
        if ease == "in":
            k = k * k
        elif ease == "out":
            k = 1 - (1 - k) ** 2
        elif ease == "inout":
            k = 0.5 - 0.5 * math.cos(math.pi * k)
        return self._all(ctx, mix(self.p["from"], self.p["to"], k))


@effect("pulse")
class Pulse(Effect):
    """Flash on every `every` beats, then decay. Cycles through `colors`."""
    PARAMS = {
        "color": ("colors", "white"),
        "every": ("beats", 1),
        "decay": ("float", 0.6),   # fraction of the interval it takes to fade out
        "offset": ("beats", 0),
        "min": ("float", 0.0),     # brightness floor between hits
    }

    def render(self, ctx):
        k = (ctx.beat - self.p["offset"]) / self.p["every"]
        n = math.floor(k)
        phase = k - n
        decay = self.p["decay"]
        level = max(0.0, 1.0 - phase / decay) ** 2 if decay > 0 else 0.0
        lo = self.p["min"]
        level = lo + (1 - lo) * level
        colors = self.p["color"]
        return self._all(ctx, scale(colors[n % len(colors)], level))


@effect("strobe")
class Strobe(Effect):
    """Hard on/off flashing. `rate` = flashes per beat; set `rate_end` to accelerate."""
    PARAMS = {
        "color": ("colors", "white"),
        "rate": ("float", 2.0),
        "rate_end": ("float", None),
        "duty": ("float", 0.35),   # fraction of each flash that is "on"
        "background": ("color", None),
    }

    def render(self, ctx):
        r0 = self.p["rate"]
        r1 = self.p["rate_end"] if self.p["rate_end"] is not None else r0
        b, total = ctx.beat, ctx.beats_total
        n = r0 * b + ((r1 - r0) * b * b / (2 * total) if total > 0 else 0.0)
        if (n % 1.0) < self.p["duty"]:
            colors = self.p["color"]
            return self._all(ctx, colors[int(n) % len(colors)])
        return self._bg(ctx)


@effect("chase")
class Chase(Effect):
    """One (or `width`) lights at a time, stepping along the cue's `order`
    (left -> right by default, or `order: circle` to go around the room).

    Set `every_end` to accelerate (or slow down) over the cue, and `tail` for
    a fading comet trail behind the lit light.
    """
    PARAMS = {
        "color": ("colors", "white"),
        "every": ("beats", 1),          # beats per step at the start
        "every_end": ("beats", None),   # beats per step at the end of the cue
        "curve": ("choice:linear|doubling", "linear"),
        "width": ("float", 1),
        "tail": ("float", 0),           # number of fading lights behind the head
        "direction": ("choice:forward|backward|bounce|random|right|left", "forward"),
        "decay": ("float", 0.0),        # 0 = hard steps, 1 = fade to dark by next step
        "background": ("color", None),
    }

    def _index(self, step: int, n: int) -> int:
        d = self.p["direction"]
        if d in ("forward", "right"):
            return step % n
        if d in ("backward", "left"):
            return (-step) % n
        if d == "bounce":
            period = max(1, 2 * n - 2)
            s = step % period
            return s if s < n else period - s
        return random.Random(step * 7919 + self.seed).randrange(n)

    def render(self, ctx):
        chs = ctx.channels
        n = len(chs)
        if n == 0:
            return {}
        k = steps_elapsed(ctx.beat, ctx.beats_total, self.p["every"], self.p["every_end"], self.p["curve"])
        step = math.floor(k)
        fade = 1.0 - self.p["decay"] * (k - step)
        colors = self.p["color"]
        tail = max(0, int(self.p["tail"]))
        out = self._bg(ctx)
        # draw the tail first (oldest, dimmest), then the head on top
        for j in range(tail, -1, -1):
            s = step - j
            if s < 0:
                continue
            level = fade * (1.0 - j / (tail + 1))
            color = scale(colors[s % len(colors)], level)
            head = self._index(s, n)
            for w in range(max(1, int(self.p["width"]))):
                cid = chs[(head + w) % n].id
                prev = out[cid]
                out[cid] = (max(prev[0], color[0]), max(prev[1], color[1]), max(prev[2], color[2]))
        return out


@effect("rainbow")
class Rainbow(Effect):
    """Rotating hues spread across the room."""
    PARAMS = {
        "speed": ("beats", 16),     # beats per full trip around the color wheel
        "spread": ("float", 0.5),   # how much of the wheel is visible at once
        "saturation": ("float", 1.0),
    }

    def render(self, ctx):
        base = ctx.beat / self.p["speed"]
        out = {}
        for c in ctx.channels:
            h = (base + self.p["spread"] * ctx.pos[c.id]) % 1.0
            out[c.id] = colorsys.hsv_to_rgb(h, self.p["saturation"], 1.0)
        return out


@effect("sweep")
class Sweep(Effect):
    """A soft band of light moving along the cue's `order` (wraps on a circle)."""
    PARAMS = {
        "color": ("colors", "white"),
        "every": ("beats", None),   # beats per sweep; default: one sweep over the whole cue
        "width": ("float", 0.35),   # band half-width as a fraction of the room
        "direction": ("choice:forward|backward|bounce|right|left", "forward"),
        "background": ("color", None),
    }

    def render(self, ctx):
        if self.p["every"]:
            k = ctx.beat / self.p["every"]
            n, ph = math.floor(k), k % 1.0
        else:
            n, ph = 0, ctx.progress
        d = self.p["direction"]
        if d in ("left", "backward") or (d == "bounce" and n % 2):
            ph = 1.0 - ph
        w = self.p["width"]
        colors = self.p["color"]
        color = colors[n % len(colors)]
        bg = self.p["background"] or BLACK
        out = {}
        if ctx.cyclic:  # around a loop: the band wraps, no entry/exit margin
            for c in ctx.channels:
                dist = abs(ctx.pos[c.id] - ph)
                dist = min(dist, 1.0 - dist)
                out[c.id] = mix(bg, color, max(0.0, 1.0 - dist / w) if w > 0 else 0.0)
            return out
        pos = -w + ph * (1 + 2 * w)
        for c in ctx.channels:
            level = max(0.0, 1.0 - abs(ctx.pos[c.id] - pos) / w) if w > 0 else 0.0
            out[c.id] = mix(bg, color, level)
        return out


@effect("breathe")
class Breathe(Effect):
    """Slow sinusoidal brightness."""
    PARAMS = {
        "color": ("color", "white"),
        "period": ("beats", 4),
        "min": ("float", 0.1),
    }

    def render(self, ctx):
        lo = self.p["min"]
        level = lo + (1 - lo) * (0.5 - 0.5 * math.cos(2 * math.pi * ctx.beat / self.p["period"]))
        return self._all(ctx, scale(self.p["color"], level))


@effect("sparkle")
class Sparkle(Effect):
    """Random lights flashing on each step."""
    PARAMS = {
        "color": ("colors", "white"),
        "every": ("beats", 0.5),
        "density": ("float", 0.3),  # fraction of lights per step
        "decay": ("float", 1.0),
        "background": ("color", None),
    }

    def render(self, ctx):
        chs = ctx.channels
        if not chs:
            return {}
        k = ctx.beat / self.p["every"]
        step = math.floor(k)
        rng = random.Random(step * 1_000_003 + self.seed)
        count = max(1, round(self.p["density"] * len(chs)))
        level = max(0.0, 1.0 - (k - step) / self.p["decay"]) if self.p["decay"] > 0 else 1.0
        colors = self.p["color"]
        out = self._bg(ctx)
        for c in rng.sample(chs, min(count, len(chs))):
            out[c.id] = scale(colors[rng.randrange(len(colors))], level)
        return out


@effect("sequence")
class Sequence(Effect):
    """A hand-written rhythm, like a drum-machine pattern: one symbol per step.

        pattern: "1 . 2 3 4 . 5 . 6 . . . A . . ."
        step: 0.25            # beats per symbol (0.25 = 16th notes)

    Symbols (combine several in one step with +, e.g. L+5):
        .      nothing          -      hold the previous hit one more step
        A      all lights       L R    left / right half of the room
        F B    front / back half
        1..N   the Nth light along the cue's `order` path
        >  <   next / previous light along the path (keeps counting across
               repeats, so a phrase walks around the room)
    Each hit flashes and fades over its length (`decay`). The pattern repeats
    until the cue ends (`repeat: false` plays it once). `color` can be a list,
    cycled hit by hit.
    """
    PARAMS = {
        "pattern": ("str", None),
        "step": ("beats", 0.5),
        "color": ("colors", "white"),
        "decay": ("float", 0.8),     # fraction of the hit's length to fade out; 0 = hard on/off
        "repeat": ("bool", True),
        "background": ("color", None),
    }
    _PART = re.compile(r"^(?:[ALRFB<>]|\d+)$")

    def __init__(self, params, parse):
        super().__init__(params, parse)
        pat = (self.p["pattern"] or "").strip()
        if not pat:
            raise ValueError('sequence needs a pattern, e.g. pattern: "1 . 2 . A . . ."')
        tokens = pat.split() if any(ch.isspace() for ch in pat) else list(pat)
        self.tokens: list[list[str] | None] = []  # parts per step; None = rest, [] = hold
        for tok in (t.upper() for t in tokens):
            if tok == ".":
                self.tokens.append(None)
            elif tok == "-":
                self.tokens.append([])
            else:
                parts = tok.split("+")
                bad = [p for p in parts if not self._PART.match(p) or p == "0"]
                if bad:
                    raise ValueError(f"unknown symbol {tok!r} in pattern (use . - A L R F B 1-9 > < and +)")
                self.tokens.append(parts)
        # which hit owns each step (holds extend the previous hit), and hit lengths
        n = len(self.tokens)
        self.owner: list[int | None] = [None] * n
        self.length: dict[int, int] = {}
        last = None
        for i, t in enumerate(self.tokens):
            if t is None:
                last = None
            elif not t:  # hold
                if last is not None:
                    self.owner[i] = last
                    self.length[last] += 1
            else:
                self.owner[i] = last = i
                self.length[i] = 1
        self.hit_no = {i: k for k, i in enumerate(sorted(self.length))}
        # cursor for > / <: value after each part, within one cycle
        self.cursor: dict[tuple[int, int], int] = {}
        c = 0
        for i, t in enumerate(self.tokens):
            for j, part in enumerate(t or []):
                if part in "<>":
                    c += 1 if part == ">" else -1
                    self.cursor[(i, j)] = c
        self.cursor_per_cycle = c

    def _lights(self, i: int, cycle: int, ctx: Ctx) -> list[Channel]:
        chs = ctx.channels
        n = len(chs)
        out: dict[int, Channel] = {}
        for j, part in enumerate(self.tokens[i] or []):
            if part == "A":
                sel = chs
            elif part in "LRFB":
                key = (lambda c: (c.x, c.id)) if part in "LR" else (lambda c: (-c.y, c.id))
                ordered = sorted(chs, key=key)
                half = (n + 1) // 2
                sel = ordered[:half] if part in "LF" else ordered[half:]
            elif part in "<>":
                pos = cycle * self.cursor_per_cycle + self.cursor[(i, j)]
                sel = [chs[(pos - 1) % n]]
            else:
                sel = [chs[(int(part) - 1) % n]]
            for c in sel:
                out[c.id] = c
        return list(out.values())

    def render(self, ctx):
        n = len(self.tokens)
        if not ctx.channels:
            return {}
        k = ctx.beat / self.p["step"]
        i = math.floor(k)
        cycle, idx = divmod(i, n)
        own = self.owner[idx] if i >= 0 else None
        if own is None or (cycle > 0 and not self.p["repeat"]):
            return self._bg(ctx)
        start = i - (idx - own)
        phase = (k - start) / self.length[own]
        decay = self.p["decay"]
        level = 1.0 if decay <= 0 else max(0.0, 1.0 - phase / decay) ** 2
        colors = self.p["color"]
        color = scale(colors[(cycle * len(self.length) + self.hit_no[own]) % len(colors)], level)
        out = self._bg(ctx)
        for c in self._lights(own, cycle, ctx):
            out[c.id] = color
        return out


@effect("alternate")
class Alternate(Effect):
    """Two halves of the room swap colors every `every` beats."""
    PARAMS = {
        "color": ("colors", ["white", "black"]),
        "every": ("beats", 1),
        "split": ("choice:left_right|odd_even", "left_right"),
    }

    def render(self, ctx):
        step = math.floor(ctx.beat / self.p["every"])
        colors = self.p["color"]
        out = {}
        for i, c in enumerate(ctx.channels):
            grp = (0 if ctx.pos[c.id] < 0.5 else 1) if self.p["split"] == "left_right" else i % 2
            out[c.id] = colors[(step + grp) % len(colors)]
        return out
