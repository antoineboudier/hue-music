# huemusic

Hand-written Philips Hue light shows, played in sync with a song from
Spotify or a local MP3.

- **Hue Entertainment API** streaming (50 updates/s), the low-latency path
  Hue Sync uses. Fast enough for strobes and beat-accurate pulses.
- **Presets** are YAML timelines written in musical time ("bar 36",
  "drop1 + 8 bars"), with layered, spatial effects (chases around the room,
  sweeps, kick/bass splits...).
- **Audio analysis** finds the tempo, beat grid and song structure, to help
  you write presets.

Presets included: `spotless`, `desenchantee`, `dramma`.

## Documentation

- [How it works](docs/how-it-works.md): architecture, beat analysis,
  sync, the Hue streaming protocol, how to extend.
- [Creating a preset](docs/creating-a-preset.md): step-by-step method and
  design advice.
- [Preset reference](docs/preset-reference.md): every key, value format
  and effect.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[analysis,dev]"
```

### Hue

You need an entertainment area in the Hue app (Settings > Entertainment
areas). Light positions in that area are used by the effects, so place the
lights roughly where they really are.

```powershell
python -m huemusic setup-hue                 # finds the bridge, press its button, pick the area
python -m huemusic identify --order circle   # lights each channel in turn to check positions
```

### Spotify (optional, Premium required)

1. Create an app at <https://developer.spotify.com/dashboard>.
2. Set the Redirect URI to exactly `http://127.0.0.1:8888/callback`, and
   tick **Web API**.
3. Run the following and paste the Client ID when asked:

```powershell
python -m huemusic setup-spotify             # asks for the Client ID, browser login once
```

Credentials are stored in `config.json` and `.spotify_cache`, which are
git-ignored.

## Playing

```powershell
python -m huemusic play spotless --mp3                  # local audio file (track.audio in the preset)
python -m huemusic play spotless                        # through Spotify, on the active device
python -m huemusic play spotless --device "Kitchen"     # a specific Spotify device
python -m huemusic play spotless --mp3 --start drop1    # start at a section, bar, or time
python -m huemusic play spotless --mp3 --no-hue         # preview in the terminal, no bridge
```

Keys while playing:

| key | action |
|---|---|
| `q` / Esc | stop |
| space | pause / resume |
| `+` / `-` | latency +/- 10 ms, if the lights are early/late (saved per device) |
| any letter | tap marker, saved to `taps/` with the nearest bar/beat |

## Studio: preview without the lights

```powershell
python -m huemusic studio spotless
```

This opens a page in your browser showing:
- your lights seen from above, at their entertainment-area positions, animated in sync with the song's MP3;
- a timeline with one row per light, showing its color through the whole song, with the sections above it.

You can play, pause, seek, slow down (down to 0.25×) and zoom in to a few bars. Click a light to see which cues drive it. Keep the page open while you edit a preset: it refreshes when you save.

**Real lights:**
- **Real lights: on** (top right) sends the show to your Hue lights, in sync with the page's playback, including when you pause, seek or slow down.
- If the lights run ahead of the sound you hear, raise **delay** (Bluetooth: about 150–250 ms).
- Edits are applied live: saving the preset, switching version, moving a light.
- Closing the page, or stopping the studio with Ctrl+C, turns the stream off.

**Following a melody:**
- The timeline's **sounds** track shows where each sound starts, colored by pitch.
- **● Record taps** lets you tap keys in time with the music (`L`/`R` halves, `A` all, `1`–`9` a light, `N` next light). The taps become a `sequence` cue, snapped to the grid, which you can add to the preset in one click.
- See [Follow a melody](docs/creating-a-preset.md#follow-a-melody).

**Editing light positions:** click **Edit positions**, then:
- drag the lights; the numbered arrows show the order a circle will follow;
- click a light without dragging to make the real one blink;
- changes are saved in `config.json` and used right away by the studio and `play`;
- **Push to bridge** writes them to the entertainment area, so the Hue app and Hue Sync use them too; heights are kept, and gradient strips are left unchanged;
- **Reset from bridge** discards your local changes.

## New song in short

```powershell
python -m huemusic analyze "mp3/My Song.mp3"   # tempo, bar grid, energy per bar
# write presets/my-song.yaml (see docs/creating-a-preset.md)
python -m huemusic check my-song                # validate and print the timeline
python -m huemusic studio my-song               # watch it in the browser while you edit
python -m huemusic play my-song --mp3 --start drop1
```

## Several versions of a song

```powershell
python -m huemusic new spotless-v2 --from spotless -d "more strobes"
python -m huemusic list                         # presets grouped by song
```

`spotless-v2.yaml` contains `based_on: spotless`: it reuses the song's header, sections and palette, and starts with a copy of the cues for you to change. See [Preset reference](docs/preset-reference.md#several-versions-of-a-song).

## Other commands

| command | what |
|---|---|
| `devices` | list Spotify devices |
| `audio-devices` | list local audio outputs (for `--audio-device`) |
| `python -m pytest` | run the tests |
