# huemusic roadmap: proposed enhancements

## Context

The app now covers the whole chain:
- **Analysis:** beat grid, sections, sound detection.
- **Presets:** YAML timelines, variants via `based_on`, a 12-effect engine including `sequence`.
- **Playback:** `play` via Spotify or MP3, streaming to Hue.
- **Studio:** room and timeline preview, tap recording, position editing, real lights.

Making four shows exposed recurring friction:
- **Grid problems.** Downbeats were off in Désenchantée, and the 3000 version may be 50 ms off.
- **Hand-written scripts.** I had to write one-off scripts to follow a synth (pitch → lights, sound fingerprint from taps).
- **Duplicated cues.** One cue per color in `sequence`.
- **Repeated work.** Writing near-identical presets by hand for each song.

The list below is grouped by area, with my estimate of value and effort. ★ = what I'd do first.

---

## 1. Timing and sync (most impactful for "it feels right")

| # | Enhancement | Why | Effort |
|---|---|---|---|
| ★1 | **Metronome / grid check in the studio.** Optional click on every beat (accent on bar 1), mixed with the song. A "grid ±10 ms" nudge updates `first_beat` in the preset. | Makes a wrong grid audible in 5 seconds (the Désenchantée 3000 half-16th issue) and fixable without code. | S |
| 2 | **Grid validation in `analyze`.** Measure the kick and transient offset against the fitted grid in several sections, warn when it's above 20 ms, and report a confidence for the bar-1 choice. | Catches the problems we found by hand, before writing a preset. | S–M |
| 3 | **Latency calibration.** Lights flash on beats and you tap with them; the median offset is saved as the device latency. Works in `play` and in the studio's real-lights mode. | Replaces trial and error with `+`/`-`. | S |
| 4 | **Spotify in the studio.** A source selector (MP3 / Spotify device), reusing `SpotifyClock`. | You can tune on the real speaker, at its real latency. | M |

## 2. Following sounds (the synth work, made built-in)

| # | Enhancement | Why | Effort |
|---|---|---|---|
| ★5 | **"Learn sound from taps".** After recording taps, the studio learns the tapped sound's fingerprint (the spectral-flux approach used on Désenchantée 3000), finds **every** occurrence in a chosen range (including missed notes), and snaps the taps to the real attacks, including off-grid timing. | Exactly what you asked for twice. Today it needs me to write scripts. | M–L |
| 6 | **Per-symbol colors in `sequence`:** `R:pink L:cyan .` | Avoids 4 duplicate cues per motif. | S |
| 7 | **`melody` effect / generator.** Pitch-track a range and map notes to lights and colors (`map: {C#: "1+2", D#: "3+4"}`), generated from the studio over a selected range. | Built-in version of the piano-roll idea from the 3000 synth section. | M |
| 8 | **Better "sounds" track:** separate tracks for drums, bass, melody/synth and voice (separation already used in scripts), each one hideable. | You can see which instrument a tick belongs to. | M |

## 3. Studio authoring (less YAML for common edits)

| # | Enhancement | Why | Effort |
|---|---|---|---|
| ★9 | **Cue lanes on the timeline.** Each cue drawn as a bar under the sections. Click a cue to highlight its lights, and **open the YAML at that line** in VS Code (`vscode://file/...:line`). | Links "what I see" to "what to edit". It's the biggest current gap. | S–M |
| 10 | **Quick cue editing in the page:** change color, intensity, effect parameters or start/end from a side panel, with a safe write-back like `append_cue`. Drag section boundaries. | Common tweaks without the editor. | L |
| 11 | **Loop a range (A–B)** plus zoom/scroll shortcuts. | Iterating on a 4-bar passage. | S |
| 12 | **Automatic backups:** every studio write (append-cue, positions, grid nudge) keeps a copy in `presets/.history/`, with a one-level undo button. | Safety now that the page edits files. | S |
| 13 | **Waveform under the sounds track.** | Easier visual orientation. | S |

## 4. Creating presets faster

| # | Enhancement | Why | Effort |
|---|---|---|---|
| ★14 | **`huemusic draft <mp3>`:** analysis → automatic section labels (intro/build/drop/breakdown/gap from energy + bass), plus a first preset built from proven recipes (circle-build chase, blackout + hit, kick/offbeat split, break-bar spin) and a palette suggestion. | I wrote this structure by hand four times. You'd get a playable draft in 30 s. | M |
| 15 | **Shared recipes / snippets:** reusable cue blocks (`use: circle-build` with parameters) in `presets/_recipes.yaml`. | Less copy-paste between songs. | M |
| 16 | **General parameter ramps:** `intensity_end`, `color_end`, any parameter `from→to` over a cue (today only `every_end`/`rate_end`). | Smooth builds without extra fade cues. | S–M |
| 17 | **More spatial paths:** `order: center` (outward ripple), `order: z` (floor to ceiling, since heights are stored), random-but-stable shuffle. | New moves with no new effects. | S |

## 5. Playing shows

| # | Enhancement | Why | Effort |
|---|---|---|---|
| 18 | **Auto mode:** watch Spotify, and when the current track has a preset (matched by URI), start its show in sync automatically; idle ambience otherwise. | Lights just work while you listen. | M |
| 19 | **Setlist:** play several presets in a row, with crossfades. | Parties and demos. | S–M |
| 20 | **Safety limits:** global maximum flash rate and brightness (photosensitivity), a per-preset `brightness:`, and a "calm mode" that softens strobes. | Guests, late evenings. | S |

## 6. Quality and maintenance

| # | Enhancement | Why | Effort |
|---|---|---|---|
| ★21 | **First git commit**, plus a commit after each milestone. The repo has never been committed, only staged. Decide whether `analysis/*.notes.json` and `taps/` are tracked. | Protects all this work. | XS |
| 22 | **Tests for the page's logic:** move tap → pattern, the grid maths and the circle order into a small JS module tested with node. | Tap recording is currently untested end to end. | S |
| 23 | **Investigate the 50 ms grid offset in `analyze`** (Désenchantée 3000), and settle on one onset reference (kick attack) for all songs. | Ties in with #1 and #2. | S–M |
| 24 | **Resolve the leftover state:** `positions_modified` is still set in `config.json`. Show a clearer warning in `play` when local positions differ from the bridge. | Avoids surprises. | XS |

---

## Suggested order

1. **#21** git commit (5 min).
2. **#1** metronome + grid nudge, with #23.
3. **#9** cue lanes + open in VS Code.
4. **#5** learn sound from taps, with #6 per-symbol colors.
5. **#14** `huemusic draft`.

Everything else as needed.

## Verification (per item, when implemented)

- `python -m pytest` stays green, with new tests for each engine/preset feature (ramps, per-symbol colors, new orders, draft generator output loads and validates with `check`).
- Studio features: headless Edge screenshots (as used so far), plus a manual pass by you for audio, keyboard and real-light behavior.
- Timing features: re-run the kick/attack offset measurement on all 4 songs; offsets under 15 ms after the grid fix.

---

_Written 2026-11-07, after the first four shows (Spotless, Désenchantée, Dramma, Désenchantée 3000)._
