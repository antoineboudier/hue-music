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

## New song in short

```powershell
python -m huemusic analyze "mp3/My Song.mp3"   # tempo, bar grid, energy per bar
# write presets/my-song.yaml (see docs/creating-a-preset.md)
python -m huemusic check my-song                # validate and print the timeline
python -m huemusic play my-song --mp3 --start drop1
```

## Other commands

| command | what |
|---|---|
| `devices` | list Spotify devices |
| `audio-devices` | list local audio outputs (for `--audio-device`) |
| `python -m pytest` | run the tests |
