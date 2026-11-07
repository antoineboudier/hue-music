# huemusic

Hand-written Philips Hue light shows, played in sync with a Spotify track.

- **Hue Entertainment API** streaming (DTLS, 50 updates/s), the low-latency
  path Hue Sync uses. Fast enough for strobes and beat-accurate pulses.
- **Spotify Web API** starts the track on any device (PC, phone, Connect
  speaker) and is polled to keep the light clock locked to playback.
- **Presets** are YAML timelines written in musical time ("bar 36",
  "drop1 + 8 bars"), with layered effects.
- **Offline analysis** of an MP3 gives you the tempo, beat grid and a per-bar
  energy map to place sections.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[analysis,dev]"
```

### 1. Hue

Prerequisite: an entertainment area exists in the Hue app
(Settings > Entertainment areas).

```powershell
python -m huemusic setup-hue      # finds the bridge, asks you to press its button, picks the area
python -m huemusic identify       # lights each channel left -> right so you can check positions
```

Credentials end up in `config.json` (git-ignored).

Light positions come from the entertainment area. Effects like `chase`,
`sweep` and the `left`/`right` groups use the x position, so placing the
lights properly in the Hue app makes them look right.

### 2. Spotify (Premium required for playback control)

1. Go to <https://developer.spotify.com/dashboard> and create an app.
2. Set the Redirect URI to exactly `http://127.0.0.1:8888/callback` and
   tick **Web API**.
3. Run the following and paste the Client ID when asked. Your browser opens
   once to log in.

```powershell
python -m huemusic setup-spotify
python -m huemusic devices        # lists the devices Spotify can play on
```

## Playing

```powershell
python -m huemusic play spotless                      # lights + music on the active Spotify device
python -m huemusic play spotless --device "Kitchen"   # a specific device (substring of its name)
python -m huemusic play spotless --start drop1        # jump to a section (also "bar 36", "1:05", "drop1 - 4 bars")
python -m huemusic play spotless --preview            # also draw the lights in the terminal
python -m huemusic play spotless --no-hue             # terminal preview only, no bridge needed
python -m huemusic play spotless --no-hue --no-spotify   # pure dry run, no music
```

Keys while playing:

| key | action |
|---|---|
| `q` / Esc | stop (also pauses Spotify) |
| space | pause / resume |
| `+` / `-` | latency +/- 10 ms |
| any other key | tap marker |

### Latency calibration

Spotify reports where it *thinks* playback is. The sound reaches you later:
Bluetooth adds about 150-250 ms, and Connect speakers vary. If the lights are
ahead of the music, press `+` until the drop hits together.

The value is saved **per device** in `config.json`, so the PC and the
speaker each keep their own setting.

### Tap markers

Press letters while the song plays (e.g. `d` on each drop, `b` on builds).
On exit they're saved to `taps/<preset>-<date>.yaml` with the exact time and
the nearest bar/beat, ready to paste into `at:` fields.

## Making a preset for a new song

```powershell
python -m huemusic analyze "mp3/Some Song.mp3"
```

This prints `bpm`, `first_beat` and `duration` for the preset header, plus an
energy/bass table per bar. Drops are the long blocks of high energy with
bass. Builds have rising energy and no bass. Breakdowns are low blocks.

Copy `presets/spotless.yaml`, change the header, place the `sections:` from
the table, and run:

```powershell
python -m huemusic check my-song            # validates and prints the resolved timeline
python -m huemusic play my-song --no-hue     # watch it in the terminal with the music
```

Use the MP3 of the **same version** as on Spotify (radio edit vs. extended
mix). `play` warns if the durations differ.

## Preset reference

```yaml
track:
  title: Spotless
  artist: Martin Garrix & Jay Hardway
  spotify_uri:          # optional, auto-filled from a search on first play
  duration: 195.08
  bpm: 128
  first_beat: 0.128     # seconds of bar 1 beat 1

palette:                # your own color names
  ice: "#a8e4ff"

groups:                 # optional light groups
  strips: ["strip"]     # name fragment(s) or channel ids

sections:               # each lasts until the next one starts
  intro: bar 2
  drop1: bar 36

cues:                   # layered in list order (later = on top)
  - in: drop1           # whole section ...
    at: drop1 + 8 bars  # ... or/and explicit start
    until: drop1.end    # ... or dur: 4 bars
    effect: pulse
    colors: [cyan, magenta]
    every: 1            # effect timings: beats (or "2 bars", "0.5s")
    lights: left        # all | left | right | front | back | odd | even | group | id | name | [list]
    blend: add          # over (default) | add | max
    intensity: 0.8
    fade_in: 1 beat
    fade_out: 2 beats
```

**Times:** `65.2`, `"1:05.2"`, `bar 36`, `bar 36 beat 3`, `beat 141`,
`drop1`, `drop1.end`, `drop1 + 8 bars`, `drop1 - 2 beats`.

**Colors:** a palette name, a builtin (`white black warm red orange yellow
green cyan blue purple magenta pink`), `"#rrggbb"`, or `[r, g, b]`.

### Effects

| effect | parameters (defaults) | what it does |
|---|---|---|
| `solid` | `color` | constant color |
| `blackout` | | lights off |
| `fade` | `from` (black), `to` (white), `ease` linear/in/out/inout | color transition over the cue |
| `pulse` | `color(s)`, `every` 1, `decay` 0.6, `offset` 0, `min` 0 | flash on the beat then decay; cycles colors |
| `strobe` | `color(s)`, `rate` 2/beat, `rate_end`, `duty` 0.35, `background` | on/off flashing; `rate_end` accelerates |
| `chase` | `color(s)`, `every` 1, `width` 1, `direction` right/left/bounce/random, `decay` 0, `background` | one light at a time across the room |
| `sweep` | `color(s)`, `every` (whole cue), `width` 0.35, `direction` right/left/bounce, `background` | soft band moving across the room |
| `rainbow` | `speed` 16 beats, `spread` 0.5, `saturation` 1 | rotating hues across the room |
| `breathe` | `color`, `period` 4, `min` 0.1 | slow sine brightness |
| `sparkle` | `color(s)`, `every` 0.5, `density` 0.3, `decay` 1, `background` | random lights flash each step |
| `alternate` | `colors` [white, black], `every` 1, `split` left_right/odd_even | room halves swap colors |

To add an effect, write a class in `huemusic/effects.py` decorated with
`@effect("name")`. It is then usable in presets.

## How sync works

1. The Hue stream is opened first (DTLS handshake about 1 s), sending black.
2. `start_playback` is called with the track URI and start position, and a
   local clock starts from that moment.
3. A background thread polls Spotify (every 0.5 s at first, then every 2 s).
   Each reading is turned into an offset, and the local clock is nudged
   toward the median of recent readings. Jumps over 0.5 s count as seeks.
   Pausing or seeking from the Spotify app is followed. Changing the track
   ends the show.
4. Lights render at `song position - latency`.

## Layout

```
huemusic/
  cli.py        commands
  player.py     real-time loop (clock -> render -> send, keys)
  engine.py     Show: cues + light selection -> frame
  effects.py    effect library
  preset.py     YAML loading, time/duration/color parsing
  hue.py        bridge REST (pairing, areas) + Entertainment DTLS stream
  spotify.py    auth, devices, SpotifyClock
  analysis.py   librosa beat grid + per-bar energy
presets/        one YAML per song
analysis/       analysis output (JSON)
taps/           tap-marker sessions
```
