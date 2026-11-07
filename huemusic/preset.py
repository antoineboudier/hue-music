"""Loading preset files (YAML) and parsing musical time expressions.

Time expressions (for `at`, `until`, `--start`):
    65.75            seconds
    "1:05.75"        minutes:seconds
    "bar 36"         bar 36, beat 1 (bars are 1-based, 4/4)
    "bar 36 beat 3"
    "beat 141"       absolute beat number (1-based)
    "drop1"          start of a section
    "drop1.end"      end of a section
    "drop1 + 8 bars" any of the above plus/minus a duration

Durations (for `dur`, `fade_in`, `fade_out`):
    1.5 / "1.5s" / "250ms" / "2 beats" / "4 bars"

Effect timing parameters (`every`, `period`, ...) are in beats when given as
plain numbers.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .effects import BUILTIN_COLORS, EFFECTS, RGB, Effect


class PresetError(ValueError):
    pass


@dataclass(frozen=True)
class Grid:
    bpm: float
    first_beat: float = 0.0  # time (s) of bar 1, beat 1

    @property
    def beat_len(self) -> float:
        return 60.0 / self.bpm

    def time_of(self, bar: float, beat: float = 1.0) -> float:
        return self.first_beat + ((bar - 1) * 4 + (beat - 1)) * self.beat_len

    def beat_index(self, t: float) -> float:
        """0-based beat index of time t (0 = bar 1 beat 1)."""
        return (t - self.first_beat) / self.beat_len

    def position(self, t: float) -> str:
        """'bar.beat' string, e.g. '36.1'."""
        n = math.floor(self.beat_index(t) + 1e-6)
        return f"{n // 4 + 1}.{n % 4 + 1}"


_NUM = r"-?\d+(?:\.\d+)?"
_OP_SPLIT = re.compile(r"\s+([+-])\s+")


def parse_duration(v, grid: Grid) -> float:
    """Duration in seconds. Bare numbers are seconds."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(rf"\s*({_NUM})\s*(bars?|beats?|s|ms)?\s*", str(v), re.I)
    if not m:
        raise PresetError(f"bad duration {v!r} (examples: 1.5, '1.5s', '2 beats', '4 bars')")
    n, unit = float(m[1]), (m[2] or "s").lower()
    if unit.startswith("bar"):
        return n * 4 * grid.beat_len
    if unit.startswith("beat"):
        return n * grid.beat_len
    if unit == "ms":
        return n / 1000
    return n


def parse_beats(v, grid: Grid) -> float:
    """Duration in beats. Bare numbers are beats."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(rf"\s*({_NUM})\s*(bars?|beats?|s|ms)?\s*", str(v), re.I)
    if not m:
        raise PresetError(f"bad beat length {v!r} (examples: 1, 0.5, '2 bars', '0.25 beats')")
    if not m[2]:
        return float(m[1])
    return parse_duration(v, grid) / grid.beat_len


def parse_time(v, grid: Grid, sections: dict[str, tuple[float, float]] | None = None) -> float:
    """Point in the song, in seconds."""
    if isinstance(v, (int, float)):
        return float(v)
    parts = _OP_SPLIT.split(str(v).strip())
    t = _parse_point(parts[0], grid, sections or {})
    for op, dur in zip(parts[1::2], parts[2::2]):
        dt = parse_duration(dur, grid)
        t = t + dt if op == "+" else t - dt
    return t


def _parse_point(s: str, grid: Grid, sections: dict[str, tuple[float, float]]) -> float:
    if s in sections:
        return sections[s][0]
    if s.endswith(".end") and s[:-4] in sections:
        return sections[s[:-4]][1]
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", s)
    if m:
        return int(m[1] or 0) * 60 + float(m[2])
    m = re.fullmatch(rf"bar\s+({_NUM})(?:\s+beat\s+({_NUM}))?", s, re.I)
    if m:
        return grid.time_of(float(m[1]), float(m[2] or 1))
    m = re.fullmatch(rf"beat\s+({_NUM})", s, re.I)
    if m:
        return grid.first_beat + (float(m[1]) - 1) * grid.beat_len
    known = ", ".join(sections) or "none defined"
    raise PresetError(
        f"bad time {s!r} (examples: 65.2, '1:05.2', 'bar 36', 'bar 36 beat 3', "
        f"'drop1 + 8 bars'; sections: {known})"
    )


def parse_color(v, palette: dict[str, RGB]) -> RGB:
    if isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v):
        return tuple(max(0.0, min(1.0, float(x) / 255)) for x in v)  # type: ignore[return-value]
    s = str(v).strip().lower()
    if s in palette:
        return palette[s]
    if s in BUILTIN_COLORS:
        return BUILTIN_COLORS[s]
    m = re.fullmatch(r"#?([0-9a-f]{6})", s)
    if m:
        h = m[1]
        return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)
    raise PresetError(f"unknown color {v!r} (use a palette name, a builtin like 'cyan', '#rrggbb' or [r, g, b])")


def _is_rgb(v) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v)


@dataclass
class Cue:
    index: int
    start: float
    end: float
    effect: Effect
    lights: object = "all"
    order: object = "x"          # x | y | circle | [explicit list]
    blend: str = "over"          # over | add | max
    fade_in: float = 0.0
    fade_out: float = 0.0
    intensity: float = 1.0
    label: str = ""

    def envelope(self, t: float) -> float:
        k = self.intensity
        if self.fade_in > 0:
            k *= min(1.0, (t - self.start) / self.fade_in)
        if self.fade_out > 0:
            k *= min(1.0, (self.end - t) / self.fade_out)
        return max(0.0, k)


@dataclass
class Preset:
    path: Path
    title: str
    artist: str
    spotify_uri: str | None
    audio: str | None
    duration: float | None
    grid: Grid
    palette: dict[str, RGB] = field(default_factory=dict)
    sections: dict[str, tuple[float, float]] = field(default_factory=dict)
    groups: dict[str, object] = field(default_factory=dict)
    cues: list[Cue] = field(default_factory=list)
    description: str = ""
    based_on: str | None = None
    files: list[Path] = field(default_factory=list)  # this file + the files it's based on

    @property
    def name(self) -> str:
        return self.path.stem

    def section_at(self, t: float) -> str:
        for name, (a, b) in self.sections.items():
            if a <= t < b:
                return name
        return ""


BLENDS = {"over", "add", "max"}


MERGED_MAPS = ("track", "palette", "groups", "sections")


def _read_raw(path: Path, chain: tuple[Path, ...] = ()) -> tuple[dict, list[Path]]:
    """Read a preset file, resolving `based_on:` (recursively).

    A variant inherits everything from its base. `track`, `palette`,
    `groups` and `sections` are merged key by key (the variant wins). `cues`
    are replaced by the variant's own, unless it sets `inherit_cues: true`,
    in which case its cues are drawn on top of the base's.
    """
    if path.resolve() in chain:
        names = " -> ".join(p.stem for p in chain + (path.resolve(),))
        raise PresetError(f"based_on loop: {names}")
    if not path.exists():
        raise PresetError(f"preset file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise PresetError(f"{path.name}: invalid YAML: {e}") from e
    base_name = raw.get("based_on")
    if not base_name:
        return raw, [path]
    base_path = path.parent / (base_name if str(base_name).endswith(".yaml") else f"{base_name}.yaml")
    try:
        base, files = _read_raw(base_path, chain + (path.resolve(),))
    except PresetError as e:
        raise PresetError(f"{path.name}: based_on {base_name!r}: {e}") from e
    merged = {k: v for k, v in base.items() if k not in ("description", "based_on", "inherit_cues")}
    for key, value in raw.items():
        if key in MERGED_MAPS and isinstance(value, dict):
            merged[key] = {**(base.get(key) or {}), **value}
        elif key == "cues":
            merged["cues"] = (list(base.get("cues") or []) if raw.get("inherit_cues") else []) + list(value or [])
        else:
            merged[key] = value
    return merged, [path] + files


def load(path: str | Path) -> Preset:
    path = Path(path)
    raw, files = _read_raw(path)

    track = raw.get("track") or {}
    if "bpm" not in track:
        raise PresetError(f"{path.name}: track.bpm is required")
    grid = Grid(float(track["bpm"]), float(track.get("first_beat", 0.0)))
    duration = float(track["duration"]) if track.get("duration") else None

    palette: dict[str, RGB] = {}
    for name, value in (raw.get("palette") or {}).items():
        palette[str(name).lower()] = parse_color(value, palette)

    # Sections: name -> start; each ends where the next one starts.
    starts = sorted(
        ((parse_time(v, grid), str(k)) for k, v in (raw.get("sections") or {}).items()),
        key=lambda x: x[0],
    )
    sections: dict[str, tuple[float, float]] = {}
    for i, (start, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else (duration or math.inf)
        sections[name] = (start, end)

    def parse_param(kind: str, v):
        if kind == "color":
            return parse_color(v, palette)
        if kind == "colors":
            items = v if isinstance(v, list) and not _is_rgb(v) else [v]
            return [parse_color(x, palette) for x in items]
        if kind == "beats":
            return parse_beats(v, grid)
        if kind == "float":
            return float(v)
        if kind == "str":
            return str(v)
        if kind == "bool":
            if isinstance(v, bool):
                return v
            if str(v).lower() in ("yes", "true", "on", "1"):
                return True
            if str(v).lower() in ("no", "false", "off", "0"):
                return False
            raise PresetError(f"{v!r} is not yes/no")
        if kind.startswith("choice:"):
            options = kind[7:].split("|")
            if str(v) not in options:
                raise PresetError(f"{v!r} is not one of {options}")
            return str(v)
        raise AssertionError(kind)

    cues: list[Cue] = []
    for i, spec in enumerate(raw.get("cues") or []):
        spec = dict(spec)
        desc = f"cue #{i + 1}" + (f" ({spec.get('effect')} {spec.get('in') or spec.get('at')})")
        try:
            name = spec.pop("effect", None)
            if name not in EFFECTS:
                raise PresetError(f"unknown effect {name!r} (available: {', '.join(sorted(EFFECTS))})")
            start = end = None
            section = spec.pop("in", None)
            if section is not None:
                if section not in sections:
                    raise PresetError(f"unknown section {section!r}")
                start, end = sections[section]
            if "at" in spec:
                start = parse_time(spec.pop("at"), grid, sections)
            if "until" in spec:
                end = parse_time(spec.pop("until"), grid, sections)
            if "dur" in spec:
                if start is None:
                    raise PresetError("`dur` needs `at`")
                end = start + parse_duration(spec.pop("dur"), grid)
            if start is None:
                raise PresetError("needs `at` or `in`")
            if end is None:
                raise PresetError("needs `until`, `dur` or `in`")
            if end <= start:
                raise PresetError(f"ends before it starts ({start:.2f}s -> {end:.2f}s)")
            blend = spec.pop("blend", "over")
            if blend not in BLENDS:
                raise PresetError(f"blend must be one of {sorted(BLENDS)}")
            order = spec.pop("order", "x")
            if not (order in ("x", "y", "circle") or isinstance(order, list)):
                raise PresetError(f"order must be x, y, circle or a list of lights, not {order!r}")
            cue = Cue(
                index=i,
                start=start,
                end=end,
                lights=spec.pop("lights", "all"),
                order=order,
                blend=blend,
                fade_in=parse_duration(spec.pop("fade_in", 0), grid),
                fade_out=parse_duration(spec.pop("fade_out", 0), grid),
                intensity=float(spec.pop("intensity", 1.0)),
                label=str(spec.pop("label", "") or name),
                effect=None,  # type: ignore[arg-type]
            )
            cue.effect = EFFECTS[name](spec, parse_param)
            cue.effect.seed = i
        except (PresetError, ValueError, TypeError) as e:
            raise PresetError(f"{path.name}: {desc}: {e}") from e
        cues.append(cue)

    return Preset(
        path=path,
        title=str(track.get("title", path.stem)),
        artist=str(track.get("artist", "")),
        spotify_uri=track.get("spotify_uri") or None,
        audio=track.get("audio") or None,
        duration=duration,
        grid=grid,
        palette=palette,
        sections=sections,
        groups=raw.get("groups") or {},
        cues=cues,
        description=str(raw.get("description") or ""),
        based_on=raw.get("based_on") or None,
        files=files,
    )


def write_spotify_uri(path: Path, uri: str) -> bool:
    """Fill an empty `spotify_uri:` line in the preset file, keeping comments."""
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^(\s*spotify_uri:)[ \t]*(#.*)?$", rf"\1 {uri}", text, count=1)
    if n:
        path.write_text(new, encoding="utf-8")
    return bool(n)
