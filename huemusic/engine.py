"""Turns a preset + the list of lights into a color frame for any song time."""
from __future__ import annotations

import math

from .effects import BLACK, RGB, Channel, Ctx
from .preset import Cue, Preset

Frame = dict[int, RGB]


def _sorted(chs: list[Channel]) -> list[Channel]:
    return sorted(chs, key=lambda c: (c.x, c.y, c.id))


def select(spec, channels: list[Channel], groups: dict, _depth: int = 0) -> list[Channel]:
    """Resolve a `lights:` selector to channels (sorted left -> right).

    Accepts: "all", "left", "right", "front", "back", "odd", "even",
    a group name from the preset's `groups:`, a channel id, part of a light's
    name, or a list of any of these.
    """
    if _depth > 10:
        raise ValueError("groups reference each other in a loop")
    chs = _sorted(channels)
    if spec is None or spec == "all":
        return chs
    if isinstance(spec, list):
        picked: dict[int, Channel] = {}
        for item in spec:
            for c in select(item, channels, groups, _depth + 1):
                picked[c.id] = c
        return _sorted(list(picked.values()))
    if isinstance(spec, int):
        return [c for c in chs if c.id == spec]
    s = str(spec)
    if s in groups:
        return select(groups[s], channels, groups, _depth + 1)
    half = (len(chs) + 1) // 2
    if s == "left":
        return chs[:half]
    if s == "right":
        return chs[half:]
    if s in ("front", "back"):
        by_y = sorted(chs, key=lambda c: (-c.y, c.x))
        return _sorted(by_y[:half] if s == "front" else by_y[half:])
    if s == "odd":
        return chs[0::2]
    if s == "even":
        return chs[1::2]
    return [c for c in chs if s.lower() in c.name.lower()]


def _linear(chs: list[Channel], key) -> dict[int, float]:
    if len(chs) <= 1:
        return {c.id: 0.5 for c in chs}
    lo, hi = min(map(key, chs)), max(map(key, chs))
    if hi - lo < 1e-6:  # all at the same coordinate: fall back to order
        return {c.id: i / (len(chs) - 1) for i, c in enumerate(chs)}
    return {c.id: (key(c) - lo) / (hi - lo) for c in chs}


def arrange(chs: list[Channel], order, groups: dict) -> tuple[list[Channel], dict[int, float], bool]:
    """Order a cue's lights along a path.

    Returns (channels in path order, position 0..1 of each along the path,
    whether the path loops). Orders:
      x       left -> right (default)
      y       front -> back
      circle  clockwise around the room seen from above, starting in front
      [list]  explicit sequence of ids / names / groups (a loop); lights of
              the cue not in the list are appended at the end
    """
    if order in (None, "x"):
        ordered = _sorted(chs)
        return ordered, _linear(ordered, lambda c: c.x), False
    if order == "y":
        ordered = sorted(chs, key=lambda c: (-c.y, c.x, c.id))
        return ordered, _linear(ordered, lambda c: -c.y), False
    if order == "circle":
        if not chs:
            return [], {}, True
        cx = sum(c.x for c in chs) / len(chs)
        cy = sum(c.y for c in chs) / len(chs)
        # Hue: +x = right, +y = front. atan2(dx, dy): 0 = front, 90 deg = right.
        ang = {c.id: (math.atan2(c.x - cx, c.y - cy) / (2 * math.pi)) % 1.0 for c in chs}
        ordered = sorted(chs, key=lambda c: (ang[c.id], c.id))
        return ordered, ang, True
    if isinstance(order, list):
        wanted = {c.id for c in chs}
        ordered: list[Channel] = []
        for item in order:
            for c in select(item, chs, groups):
                if c.id in wanted and c not in ordered:
                    ordered.append(c)
        ordered += [c for c in _sorted(chs) if c not in ordered]
        n = len(ordered)
        return ordered, {c.id: i / n for i, c in enumerate(ordered)}, True
    raise ValueError(f"unknown order {order!r} (use x, y, circle or a list of lights)")


class Show:
    def __init__(self, preset: Preset, channels: list[Channel]):
        self.preset = preset
        self.channels = _sorted(channels)
        self.warnings: list[str] = []
        self._cues: list[tuple[Cue, list[Channel], dict[int, float], bool]] = []
        for cue in preset.cues:
            chs = select(cue.lights, self.channels, preset.groups)
            if not chs:
                self.warnings.append(f"cue #{cue.index + 1} ({cue.label}): lights {cue.lights!r} match no channel")
            ordered, pos, cyclic = arrange(chs, cue.order, preset.groups)
            self._cues.append((cue, ordered, pos, cyclic))

    @property
    def cue_targets(self) -> list[tuple[Cue, list[Channel]]]:
        """Each cue with the lights it drives (in path order)."""
        return [(cue, chs) for cue, chs, _, _ in self._cues]

    @property
    def duration(self) -> float | None:
        if self.preset.duration:
            return self.preset.duration
        return max((c.end for c in self.preset.cues), default=None)

    def render(self, t: float) -> tuple[Frame, list[str]]:
        frame: Frame = {c.id: BLACK for c in self.channels}
        active: list[str] = []
        beat_len = self.preset.grid.beat_len
        for cue, chs, pos, cyclic in self._cues:
            if not (cue.start <= t < cue.end) or not chs:
                continue
            k = cue.envelope(t)
            if k <= 0:
                continue
            active.append(cue.label)
            ctx = Ctx(t=t, local=t - cue.start, duration=cue.end - cue.start,
                      beat_len=beat_len, channels=chs, pos=pos, cyclic=cyclic)
            for cid, col in cue.effect.render(ctx).items():
                base = frame[cid]
                if cue.blend == "over":
                    frame[cid] = (base[0] + (col[0] - base[0]) * k,
                                  base[1] + (col[1] - base[1]) * k,
                                  base[2] + (col[2] - base[2]) * k)
                elif cue.blend == "add":
                    frame[cid] = (min(1.0, base[0] + col[0] * k),
                                  min(1.0, base[1] + col[1] * k),
                                  min(1.0, base[2] + col[2] * k))
                else:  # max
                    frame[cid] = (max(base[0], col[0] * k),
                                  max(base[1], col[1] * k),
                                  max(base[2], col[2] * k))
        return frame, active
