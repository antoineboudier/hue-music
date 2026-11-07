# How huemusic works

This page explains what happens between "I have an MP3" and "my lights
flash on the drop": the main components, the data that flows between them,
and the non-obvious problems each one solves.

## The big picture

```
                 (once per song, offline)
  song.mp3 ──► analyze ──► analysis/<song>.json ──► you write presets/<song>.yaml
                                                          │
                 (every time you play)                    ▼
  ┌──────────┐  song position  ┌────────┐  colors per light  ┌────────────┐
  │  Clock   │ ──────────────► │ Engine │ ─────────────────► │ Hue stream │ ──► lights
  │ (music)  │   50 times/s    │ (Show) │                    │ (DTLS/UDP) │
  └──────────┘                 └────────┘                    └────────────┘
   Spotify, local MP3,           preset + light positions      or terminal preview
   or free-running
```

There are two separate phases:

1. **Authoring (offline).** The analyzer extracts the tempo, beat grid and
   song structure from an audio file. You turn that into a **preset**: a
   timeline of light effects written in musical time ("bar 36", "drop1 + 8
   bars").
2. **Playback (real time).** A **clock** says where we are in the song. 50
   times per second the **engine** computes a color for every light at that
   instant, and the **Hue stream** sends the colors to the bridge.

The key design choice is that the engine is a **pure function of time**:
`render(t) → {light: color}`. It keeps no state between frames, so you can
start anywhere (`--start drop2`), pause, seek, or render the whole song in a
test, and always get the same picture for the same `t`.

## Components

| file | role |
|---|---|
| [`analysis.py`](../huemusic/analysis.py) | tempo, beat grid, downbeat, per-bar energy |
| [`preset.py`](../huemusic/preset.py) | loads YAML presets, parses musical times, durations and colors |
| [`effects.py`](../huemusic/effects.py) | the effect library (`pulse`, `chase`, `sweep`, ...) |
| [`engine.py`](../huemusic/engine.py) | `Show`: chooses each cue's lights, orders them along paths, layers the cues |
| [`player.py`](../huemusic/player.py) | the real-time loop: clock → render → send, keyboard, status line |
| [`spotify.py`](../huemusic/spotify.py) | Spotify login, devices, track lookup, `SpotifyClock`, `FreeClock` |
| [`audio.py`](../huemusic/audio.py) | `LocalAudioClock`: plays an MP3 and uses the sound card as the clock |
| [`hue.py`](../huemusic/hue.py) | bridge pairing, entertainment areas, the DTLS streaming connection |
| [`studio.py`](../huemusic/studio.py) + [`static/studio.html`](../huemusic/static/studio.html) | browser preview: renders the whole show up front, serves it and the MP3 to a local page |
| [`config.py`](../huemusic/config.py) | `config.json` (credentials, light positions, latency per device) |
| [`cli.py`](../huemusic/cli.py) | the `python -m huemusic ...` commands |

## 1. Audio analysis

`python -m huemusic analyze song.mp3` gives three things: the tempo (BPM),
where bar 1 starts (`first_beat`), and a loudness/bass table per bar.

The obvious approach (librosa's beat tracker, then fitting a line through
the beats) turned out to be wrong in three ways on real dance tracks:

- **Rounded tempo estimates.** librosa reports values like 129.2 for a 128
  BPM track. Over 3 minutes that error drifts by several beats. So we
  *search* for the exact tempo instead.
- **The tracker jumps to off-beats.** In eurodance the offbeat bass is as
  loud as the kick, and the tracker flips between them. So the grid is
  fitted on the **kick drum** only:
  1. Split the audio into percussive (drums) and harmonic (bass, synths)
     parts.
  2. Band-pass the percussive part to 30–150 Hz and take a 10 ms
     time-domain envelope. A spectrogram would see kicks about 30 ms early.
  3. Keep the busiest half of the track (the drops, where the kick pattern
     is clean).
  4. Try every tempo (0.002 BPM steps) and every phase, and keep the grid
     whose beats land on the most kicks. Whole-number tempos are preferred
     when they fit nearly as well. Some recordings, like the
     Désenchantée music video, really are 140.025 BPM.
- **Bar 1 is ambiguous.** With a kick on every beat, every beat looks like
  beat 1. We use structure instead: sections (drops, breakdowns) start on
  bar lines. Of the 4 possible alignments we keep the one where energy
  changes most *between* bars.

The JSON in `analysis/` contains the grid, the raw detected beats and the
bar table. It is only a helper for writing presets. Nothing reads it at
playback.

## 2. Presets and musical time

A preset ([reference](preset-reference.md)) has four parts:

- `track`: title, tempo grid, audio file.
- `palette`: your color names.
- `sections`: named song parts.
- `cues`: what the lights do.

Everything is written in **musical time** and converted to seconds once, at
load time, by [`preset.py`](../huemusic/preset.py):

```
time(bar N, beat B) = first_beat + ((N-1)*4 + (B-1)) * 60/bpm
```

Expressions such as `drop1 + 8 bars`, `drop1.end - 2 beats` or `"1:05.75"`
all resolve to a number of seconds. Each section lasts until the next one
starts. A cue gets a `start`/`end` from `in:`, `at:`, `until:` or `dur:`.

The loader also checks everything up front: unknown effects, unknown
parameters (`colour:` instead of `color:`), bad times and cues that end
before they start are reported with the cue number. A typo fails at
`check` time, not in the middle of the show.

## 3. The engine: from cues to colors

For each frame, [`Show.render(t)`](../huemusic/engine.py) does this:

1. Start with every light black.
2. For each cue **in file order** that is active at `t`:
   - Compute its **envelope**: `intensity`, times the `fade_in` ramp,
     times the `fade_out` ramp.
   - Ask the effect for a color per light. The effect receives a context:
     time since the cue started, the cue duration, beats elapsed, the
     cue's lights *in path order*, and each light's position (0 to 1)
     along the path.
   - **Blend** the result onto the frame: `over` mixes toward the new color
     by the envelope, `add` adds light, `max` keeps the brighter value.

Later cues are drawn on top, like layers in an image editor. That's how you
compose: a base layer (`pulse`, `breathe`), a moving layer (`chase`) with
`blend: add`, and short accents last (a white hit on the first beat of a
drop).

### Selecting and ordering lights

Each cue chooses its lights with `lights:` and the order they're walked
in with `order:`. Both are resolved **once** when the show is built, not
every frame:

- `lights:` accepts `all`, `left`/`right`/`front`/`back` (halves of the
  room), `odd`/`even`, a group, a channel id, part of a light's name, or a
  list of these.
- `order:` turns light positions into a path. `x` and `y` are straight
  lines. `circle` sorts the lights by angle around their center, clockwise
  from the front. A list is an explicit loop. The positions come from the
  entertainment area you set up in the Hue app (x from -1 left to +1
  right, y from -1 back to +1 front). They're cached in `config.json` by
  `setup-hue`.

This is why a `chase` with `order: circle` goes around the room, and why a
`sweep` on a circle wraps around instead of leaving through one side.

### How effects keep the beat

Effects never count frames. They compute everything from **beats elapsed
since the cue started**: a `pulse` with `every: 1` is at full brightness
whenever that number is a whole number, then decays. A pulse therefore
stays exactly on the beat whatever the frame rate, and stays right after a
pause or a seek.

Accelerating effects integrate a changing rate. A `chase` from `every: 1`
to `every_end: 0.125` with `curve: doubling` splits the cue into 4 equal
stages at 1, 2, 4 and 8 steps per beat. The step count at any instant is
computed directly, so the head jumps forward at exactly the right moments.

## 4. Clocks: knowing where we are in the song

The engine needs one number, the song position, and three clocks provide
it through the same interface (`start`, `position`, `toggle_pause`,
`stop`):

- **`LocalAudioClock`** (`--mp3`) decodes the file into memory and plays it
  through PortAudio. Each time the sound card asks for audio, we note which
  sample will reach the speaker and when. Position = that sample's time +
  the time elapsed since, measured on the sound card's own clock.

  On Windows that reading jitters by about ±10 ms. So a steady
  `time.monotonic()` clock is pulled gently toward it (3% per frame), and
  only snaps on jumps over 100 ms. Result: about 2 ms of jitter, never going
  backwards.
- **`SpotifyClock`** can't hear the audio, only ask Spotify's servers. It
  starts playback, then a background thread polls `current_playback`:
  every 0.5 s for the first 8 s, then every 2 s. Each reply gives an
  estimated offset, timed at the midpoint of the request. The local clock
  is nudged toward the median of the last 5 estimates. Jumps over 0.5 s
  count as seeks, a paused state freezes the clock, and a different track
  ends the show.
- **`FreeClock`** (`--no-spotify`) is just a stopwatch, for dry runs.

### Latency

Clocks report what the computer *sends* to the speaker. Bluetooth headsets
and Connect speakers play it later, typically 150–250 ms for Bluetooth.
The player therefore renders at `position - latency`. Press `+`/`-` during
playback until the drops line up. The value is saved per output device in
`config.json`, so each setup keeps its own.

## 5. Talking to the lights: the Entertainment API

The regular Hue REST API accepts only about 10 commands per second for the
whole system, far too slow for strobes. huemusic uses the **Entertainment
API**, the same channel Hue Sync uses:

1. **Pairing** (`setup-hue`): you press the link button, and we request an
   app key *and* a `clientkey` (a 16-byte secret for encryption).
2. **Start**: we tell the bridge to start streaming to the area
   (`PUT .../entertainment_configuration/<id> {"action": "start"}`).
3. **DTLS handshake** on UDP port 2100. DTLS is TLS over UDP, authenticated
   with a pre-shared key: the identity is your **application id** (from
   `GET /auth/v1`, *not* the app key), and the key is the clientkey.
4. **Stream** about 50 packets per second. Each packet is a 52-byte header
   (`HueStream`, version, color space, area id) followed by 7 bytes per
   channel (channel id plus 16-bit R, G, B). The bridge forwards colors to
   the lights over Zigbee at about 25 Hz.
5. **Stop** when done. The bridge also gives up on its own after about 10 s
   without packets.

Two quirks took real debugging and are handled in
[`hue.py`](../huemusic/hue.py):

- **Identity.** With API v2 the PSK identity must be the
  `hue-application-id`. With the app key, the bridge ignores the handshake
  silently.
- **Retransmission.** The bridge ignores the first "hello with cookie"
  packet. A DTLS client is supposed to resend after a timeout (OpenSSL
  does), but python-mbedtls just blocks on `recv()` forever. huemusic keeps
  the last batch of packets it sent, and resends it after 0.5 s of silence.
  The whole handshake gives up after 10 s with a clear error.

A "channel" is not always a bulb: a gradient light strip has several
channels, and `setup-hue` names them `Strip #1`, `Strip #2`, and so on.

## 6. The real-time loop

[`player.run`](../huemusic/player.py) ties it together:

```
every 1/50 s:
    pos = clock.position()            # None = playback ended
    t   = pos - latency
    frame = show.render(t)            # ~0.02 ms for 10 lights
    for output in outputs: output.send(frame)
    handle keys (quit, pause, +/- latency, tap markers)
    every 3rd frame: redraw the status line (and color swatches with --preview)
```

The loop sleeps until the next scheduled frame. If the machine stalls, it
resets its schedule rather than rushing to catch up.

**Tap markers:** any letter key you press is recorded with the song time.
On exit they go to `taps/` along with the nearest bar and beat, which is
the fastest way to find exactly where a section starts.

## 7. The studio (preview in the browser)

`python -m huemusic studio` runs a small local web server (127.0.0.1
only, standard library, no dependencies), and the page is a single HTML
file with no external libraries.

- **Rendering up front.** Because `render(t)` is a pure function of time,
  the server renders the whole song at 30 frames per second in one go.
  That's about 6,000 frames × 10 lights × 3 bytes, roughly 170 KB, sent as
  base64 together with the lights, sections and cues. The page never asks
  Python again while playing, and what it shows is exactly what
  `play` would send to the bridge.
- **Sync with the audio.** The page plays `track.audio` in an `<audio>`
  element. The server supports HTTP range requests, which seeking needs.
  The animation reads `audio.currentTime` and extrapolates between its
  updates, so movement stays smooth.
- **Timeline.** Each pixel column covers a slice of time. It shows the
  *brightest* frame in that slice, so short flashes stay visible when
  zoomed out.
- **Auto-reload.** The page asks for the preset file's modification time
  every second, and re-renders when it changes. If the new version has an
  error, the message is shown and the last working version stays
  displayed.
- **Refresh lights.** This re-reads the entertainment area from the bridge
  (names and positions) into `config.json`. That's the same data
  `setup-hue` saves, without re-pairing.
- **Editing positions.**
  - Dragging a light saves `x`/`y` to `config.json` (marked
    `positions_modified`) and re-renders the show, because paths like
    `circle` and `left`/`right` depend on the positions.
  - **Push** writes them to the area's `locations.service_locations` with a
    `PUT entertainment_configuration/<id>`. Each channel maps to its
    entertainment service, the height (`z`) is kept, and services with
    several positions (gradient strips) are left as they are.
  - The bridge refuses the change while the area is streaming.
  - **Flash** follows channel → entertainment service → device → light,
    then sends `{"identify": {"action": "identify"}}` to that light.

## Files you'll see

| path | what | in git? |
|---|---|---|
| `config.json` | bridge credentials, light positions, Spotify client id, latency per device | no (secrets) |
| `.spotify_cache` | Spotify login token | no |
| `mp3/` | your audio files | no |
| `analysis/*.json` | analyzer output | yes |
| `presets/*.yaml` | the shows | yes |
| `taps/*.yaml` | tap-marker sessions | your choice |

## Extending

- **New effect:** subclass `Effect` in
  [`effects.py`](../huemusic/effects.py), declare `PARAMS` (name → kind
  and default; kinds are `color`, `colors`, `beats`, `float`,
  `choice:a|b`), implement `render(ctx)` to return `{channel_id: (r, g,
  b)}`, and decorate it with `@effect("name")`. The loader validates its
  parameters automatically. Use `ctx.beat` for timing and `ctx.pos` for
  space.
- **New music source:** write a class with the clock interface (`start`,
  `position`, `toggle_pause`, `stop`, `label`) and use it in `cmd_play`.
- **New output** (another light system, a web preview...): anything with a
  `send(frame, brightness)` method can be added to the outputs list.
