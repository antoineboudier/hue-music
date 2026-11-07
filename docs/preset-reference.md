# Preset reference

The full syntax of `presets/*.yaml`. For the method and advice, see
[Creating a preset](creating-a-preset.md).

## Structure

```yaml
track:
  title: Spotless
  artist: Martin Garrix & Jay Hardway
  spotify_uri:          # optional, auto-filled from a search on first Spotify play
  audio: "mp3/Martin Garrix & Jay Hardway - Spotless.mp3"   # used by --mp3
  duration: 195.08      # seconds; the show ends here, and it's compared with Spotify's version
  bpm: 128
  first_beat: 0.128     # seconds of bar 1 beat 1

palette:                # your own color names (case-insensitive)
  ice: "#a8e4ff"

groups:                 # optional named light groups
  strips: ["strip"]     # name fragment(s) or channel ids

sections:               # each lasts until the next one starts (the last one: until `duration`)
  intro: bar 2
  drop1: bar 36

cues:                   # layered in list order: later cues are drawn on top
  - in: drop1           # whole section ...
    at: drop1 + 8 bars  # ... and/or explicit start (overrides the section start)
    until: drop1.end    # ... or dur: 4 bars
    effect: pulse
    colors: [cyan, magenta]
    every: 1            # effect timing parameters are in beats (or "2 bars", "0.5s")
    lights: left
    order: circle
    blend: add
    intensity: 0.8
    fade_in: 1 beat
    fade_out: 2 beats
    label: kick         # optional name shown in the status line
```

## Values

**Times** (`at`, `until`, section starts, `--start`):

| form | meaning |
|---|---|
| `65.2` | seconds |
| `"1:05.2"` | minutes:seconds |
| `bar 36` | bar 36, beat 1 (bars are 1-based, 4 beats per bar) |
| `bar 36 beat 3` | a specific beat |
| `beat 141` | absolute beat number (1-based) |
| `drop1` | start of a section |
| `drop1.end` | end of a section |
| `drop1 + 8 bars`, `drop1.end - 2 beats` | any of the above, plus or minus a duration |

**Durations** (`dur`, `fade_in`, `fade_out`): `1.5` or `"1.5s"`,
`"250ms"`, `"2 beats"`, `"4 bars"`.

**Beat lengths** (effect parameters like `every`, `period`, `speed`): a
plain number means beats (`0.5` is an eighth note), or `"2 bars"`,
`"1s"`.

**Colors:** a palette name, a builtin (`white black warm red orange yellow
green cyan blue purple magenta pink`), `"#rrggbb"`, or `[r, g, b]` with
values 0–255. Parameters marked `color(s)` accept one color or a list,
cycled by the effect.

## Cue options

| key | default | meaning |
|---|---|---|
| `in` | | section name: sets start and end |
| `at` / `until` / `dur` | | explicit start / end / length |
| `effect` | (required) | see [Effects](#effects) |
| `lights` | `all` | which lights (see below) |
| `order` | `x` | the path that spatial effects follow (see below) |
| `blend` | `over` | `over`: mix toward the color (by intensity and fades). `add`: add light on top. `max`: keep the brighter value |
| `intensity` | 1 | 0–1 multiplier |
| `fade_in` / `fade_out` | 0 | ramps at the start/end of the cue |
| `label` | effect name | shown in the status line |

### `lights:`

| value | lights |
|---|---|
| `all` | every channel |
| `left` / `right` | left/right half of the room (by x position) |
| `front` / `back` | front/back half (by y position) |
| `odd` / `even` | every other light, from left to right |
| a group name | from `groups:` |
| a number | that channel id |
| any other text | lights whose name contains it (case-insensitive) |
| a list | the union, e.g. `[left, "desk", 3]` |

Channel ids, names and positions: `python -m huemusic setup-hue` prints
them, and `identify` lights them one by one.

### `order:` (paths)

Spatial effects (`chase`, `sweep`, `rainbow`, `alternate`) walk the cue's
lights along a path:

| value | path |
|---|---|
| `x` | left to right (default) |
| `y` | front to back |
| `circle` | clockwise around the room seen from above, starting in front; loops |
| `[3, 7, "lamp", 1]` | exactly this sequence (ids, names or groups); loops. Lights not listed are appended |

Check a path on the real lights with
`python -m huemusic identify --order circle`. If the circle comes out
scrambled, either fix the positions in the Hue app's entertainment area
(then run `setup-hue` again), or use an explicit list.

## Effects

| effect | parameters (defaults) | what it does |
|---|---|---|
| `solid` | `color` (white) | constant color |
| `blackout` | | lights off |
| `fade` | `from` (black), `to` (white), `ease` linear/in/out/inout | color transition over the cue |
| `pulse` | `color(s)`, `every` 1, `decay` 0.6, `offset` 0, `min` 0 | flash every `every` beats, then decay over `decay` × interval; `offset: 0.5` puts it on the off-beat; `min` = brightness between flashes |
| `strobe` | `color(s)`, `rate` 2, `rate_end`, `duty` 0.35, `background` | on/off flashing, `rate` flashes per beat; `rate_end` accelerates; `duty` = fraction of time on |
| `chase` | `color(s)`, `every` 1, `every_end`, `curve` linear/doubling, `tail` 0, `width` 1, `direction` forward/backward/bounce/random, `decay` 0, `background` | one light at a time along the path; `every_end` changes speed over the cue (`doubling`: equal stages, each twice as fast); `tail` = fading lights behind |
| `sweep` | `color(s)`, `every` (whole cue), `width` 0.35, `direction` forward/backward/bounce, `background` | soft band moving along the path; wraps around on loops; `width` as a fraction of the path |
| `rainbow` | `speed` 16, `spread` 0.5, `saturation` 1 | hues rotating once per `speed` beats; `spread: 1` = the full color wheel across the path |
| `breathe` | `color`, `period` 4, `min` 0.1 | slow sine brightness |
| `sparkle` | `color(s)`, `every` 0.5, `density` 0.3, `decay` 1, `background` | random lights flash each step; `density` = fraction of lights |
| `alternate` | `colors` [white, black], `every` 1, `split` left_right/odd_even | the two halves of the path swap colors every step |

`direction` also accepts `right`/`left` as aliases of `forward`/`backward`.

### Writing a new effect

Add a class to [`huemusic/effects.py`](../huemusic/effects.py):

```python
@effect("heartbeat")
class Heartbeat(Effect):
    """Double pulse once per bar."""
    PARAMS = {"color": ("color", "red")}

    def render(self, ctx):
        b = ctx.beat % 4                       # position in the bar
        level = max(0.0, 1 - b * 4) + max(0.0, 1 - abs(b - 0.5) * 4) * 0.6
        return {c.id: scale(self.p["color"], level) for c in ctx.channels}
```

It's immediately usable as `effect: heartbeat`, and its parameters are
validated automatically. Useful context fields:

- `ctx.beat`: beats since the cue started.
- `ctx.progress`: 0 to 1 through the cue.
- `ctx.channels`: the cue's lights, in path order.
- `ctx.pos[id]`: each light's 0–1 position along the path.
- `ctx.cyclic`: whether the path loops.
