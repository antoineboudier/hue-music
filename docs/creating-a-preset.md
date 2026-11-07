# Creating a preset for a new song

A start-to-finish method, with the advice that came out of making the
Spotless, Désenchantée and Dramma presets. Expect 20–40 minutes for a first
version, then some tuning while listening.

For the exact syntax of each key and effect, see the
[preset reference](preset-reference.md).

## 0. Get the right audio

Put the MP3 in `mp3/`. It must be **the same version you'll play**: radio
edit, extended mix and music-video audio have different intros and lengths.
A 4-second difference in the intro shifts every cue by 4 seconds.

If you'll play it from Spotify later, compare the lengths. `play` warns
when the preset's `duration` and Spotify's version differ by more than
2 s.

## 1. Analyze

```powershell
python -m huemusic analyze "mp3/R3HAB & ARTBAT - Dramma Visualiser.mp3"
```

This takes about 20 s, writes `analysis/<song>.json`, and prints:

- the **header values** (last line): `bpm`, `first_beat`, `duration`;
- a **table with one row per bar**: overall energy (`#`) and bass (`=`).

## 2. Read the table

Here is an excerpt from Dramma:

```
  bar   time       energy                          bass
   22  0:39.549  #############                   ==
   23  0:41.424  ##############                  =
   24  0:43.299  ##############
   25  0:45.174  ############                    ===
   26  0:47.049  ############################    ==================   <- drop
   27  0:48.924  #############################   ===================
  ...
   37  1:07.674  ##############################  ===================
   38  1:09.549  ###################             ====                 <- lull
  ...
   41  1:15.174  ############                    ==
   42  1:17.049  ############################    ==================   <- drop again
```

Typical shapes in dance music:

| what you see | what it is |
|---|---|
| long run of full `#` **and** full `=` | **drop** (or the main beat) |
| `#` medium or rising, `=` small or none | **build-up** (risers, snare rolls, no kick) |
| `#` low, `=` none | **breakdown** or **intro** (pads, vocals) |
| one or two nearly empty bars right before a drop | **gap**: the silence before the drop |
| one weaker bar in the middle of a drop | **break bar**: a fill or vocal shot, great for a special effect |
| everything falling to zero | **outro** |

Sections almost always last **4, 8 or 16 bars**. If you find a 7-bar
section, look again: one of its edges is probably a bar off.

### Check that the bars are aligned

The analyzer picks the bar-1 alignment automatically, and it's right on
the three songs so far. But it's the most fragile result, so check it: the
first bar of every drop should be full, not half full.

If you see a pattern like this, the bar lines are off:

```
   43  ...  #######################   =========     <- half-full bar before each drop
   44  ...  #############################   ===================
```

To find out by how many beats, play from just before the drop and press a
key exactly when the drop hits. The tap file gives you the nearest bar and
beat, e.g. `bar 43 beat 3`. If the drop lands on beat **B**, the bar lines
must move by **B − 1** beats:

```
new first_beat = first_beat + (B - 1) * 60/bpm     # or subtract (5 - B) beats instead,
                                                   # if that keeps it closer to 0
```

Example from Désenchantée (140.025 BPM, beat = 0.4285 s): drops landed on
beat 3, so `0.8918 + 2 × 0.4285 = 1.7488`, or equally
`0.8918 − 2 × 0.4285 = 0.0348`. That second value is the one in the
preset. Moving a whole bar (4 beats) only renumbers the bars, so both
values are the same alignment.

## 3. Create the file

Copy an existing preset (Dramma is a good, compact template) to
`presets/<name>.yaml`. The file name is the name you'll use in `play`, so
keep it short.

Fill the header from the analyzer output:

```yaml
track:
  title: Dramma                # used to search Spotify, so use the real title
  artist: R3HAB & ARTBAT
  spotify_uri:                 # leave empty, filled on first Spotify play
  audio: "mp3/R3HAB & ARTBAT - Dramma Visualiser.mp3"
  duration: 209.514
  bpm: 128
  first_beat: 0.1741
```

## 4. Write the sections

One line per section, at the bar where it starts. Each section lasts until
the next one starts:

```yaml
sections:
  intro:   bar 2
  verse:   bar 10
  build1:  bar 22
  drop1:   bar 26
  lull1:   bar 38
  drop1b:  bar 42
  ...
  end:     bar 110
```

Tips:

- **Name sections by role, numbered** (`drop1`, `drop2`, `build2`). Cues
  then read like the song: `at: drop2 + 8 bars`.
- **Split the special moments into their own section**, e.g. a `gap` or
  `riser` just before a drop. It's easier than writing `drop2 - 2 bars`
  everywhere.
- **Run `check` now.** It prints every section with its time, so you can
  compare against what you hear.

```powershell
python -m huemusic check dramma
```

## 5. Choose a palette

Pick **3 to 5 colors that fit the mood**, plus a near-black and a white:

```yaml
palette:
  ember:   "#3a0400"   # near-black base, keeps the room "on" in quiet parts
  crimson: "#d1001f"
  amber:   "#ff7a00"
  gold:    "#ffc040"
  bone:    "#fff1dc"   # warm white for hits
```

- A **dark tint** (`ember`, `night`, `deep`) is better than black for quiet
  sections: the room stays in the song's mood.
- **Use white sparingly**, for hits and fast runners. If white is
  everywhere, the drop has nothing left to add.
- Hue bulbs are weak on **deep blue and pure violet**, and those look dim
  next to other colors. Strips show saturated colors best.

Past palettes: Spotless (cold: ice, cyan, blue, magenta), Désenchantée
(night club: night, purple, magenta, rose, ice), Dramma (theatre: ember,
crimson, amber, gold).

## 6. Write the cues, section by section

Think of each section as **2 to 4 layers**, written in this order:

1. **Base.** What the whole room does: `breathe`, `pulse`, `alternate`,
   `fade`. Use the default blend (`over`).
2. **Movement.** Something travelling: `chase` or `sweep`, usually with
   `order: circle` and `blend: add`.
3. **Texture.** `sparkle` with `blend: add`, low `intensity`.
4. **Accents.** Short cues on exact moments: a white `solid` with
   `fade_out` on the first beat of a drop, a `blackout` in a gap. Put them
   last so they're drawn on top.

### Recipes that work

**Quiet intro / breakdown**: slow and dim, so the drop will contrast:

```yaml
  - in: breakdown
    effect: breathe
    color: crimson
    period: 2 bars
    min: 0.1
  - in: breakdown
    order: circle
    effect: sweep
    color: amber
    every: 4 bars
    width: 0.25
    intensity: 0.4
    blend: add
```

**Build-up**: a light circling the room and doubling its speed, over a
rising base:

```yaml
  - in: build2
    effect: fade
    from: ember
    to: crimson
    ease: in
  - in: build2
    order: circle
    effect: chase
    colors: [gold, bone]
    every: 1
    every_end: 0.125    # 1 -> 1/2 -> 1/4 -> 1/8 beat per light
    curve: doubling
    tail: 3
    blend: add
```

Use a build length divisible by the number of stages: 8 bars with 4
stages gives 2 bars per stage. With a short build (4 bars), start faster:
`every: 0.5`.

**Silence before the drop**: go fully dark, then hit:

```yaml
  - at: drop2 - 1 beat
    until: drop2
    effect: blackout
  - at: drop2
    dur: 2 beats
    effect: solid
    color: bone
    fade_out: 2 beats
```

**Drop with a kick/bass split**: very effective on house and eurodance:

```yaml
  - in: drop1
    lights: odd
    effect: pulse
    color: magenta
    every: 1
    decay: 0.5
    min: 0.15
  - in: drop1
    lights: even
    effect: pulse
    color: ice
    every: 1
    offset: 0.5          # on the "and" between beats
    decay: 0.5
    min: 0.1
```

**Break bar inside a drop**: a fast spin, used as a recurring signature:

```yaml
  - at: drop1 + 7 bars
    dur: 1 bar
    order: circle
    effect: chase
    color: white
    every: 0.125         # 3+ laps of the room in one bar
    tail: 3
    background: night
```

### Design advice

- **Contrast is everything.** The drop only feels big if what comes before
  is smaller. Keep intros and breakdowns dim and slow, and save full
  brightness, white and fast movement for the drops.
- **Give each drop its own look** (pump, then rainbow wheel, then
  alternating halves) and **reuse one motif** across the song (the same
  spin on every break bar). Variety plus repetition makes a show feel
  designed, not random.
- **Change something every 8 bars** within long sections: `at: drop1 + 8
  bars` is the most useful line in a preset. Music changes every 8 bars,
  and the lights should too.
- **Start cues on bars, not seconds.** Everything stays on the beat, and
  if you fix `first_beat` later, the whole show moves together.
- **Fewer, stronger layers.** Five effects at once turn into a mess on 10
  lights. If you can't tell what a layer adds, remove it.
- **Strobes:** short bursts only (the last bar of a build, the first beats
  of a drop). Long fast strobing gets tiring. It can also trigger
  photosensitive epilepsy, so be careful when guests are present.

## 7. Watch, listen, adjust

```powershell
python -m huemusic play dramma --mp3 --start "drop1 - 4 bars"
```

- **Work section by section** with `--start <section>` or `--start "drop2 -
  4 bars"`. There's no need to replay the whole song each time.
- **Everything early or late by the same amount** means latency, not the
  preset: press `+`/`-`. The value is saved per output device.
- **One section wrong** means a section boundary is off: press a letter
  key when it really starts, then copy the bar from the `taps/` file into
  `sections:`.
- **Drops land on the third beat** means a bar-alignment problem: see
  [step 2](#check-that-the-bars-are-aligned).
- **Without the bridge**, `--no-hue` draws the lights as colored blocks in
  the terminal. It's handy on a laptop.

## Checklist

- [ ] Same audio version as the one you'll play (compare lengths)
- [ ] `bpm`, `first_beat`, `duration` copied from `analyze`
- [ ] Drops start on full bars in the table (bars aligned)
- [ ] Sections at 4/8/16-bar boundaries, `check` passes
- [ ] Quiet parts dim, drops bright, white reserved for hits
- [ ] Each drop looks different, with one recurring motif
- [ ] Blackout in the gaps, white hit on each drop
- [ ] Watched each section once with `--start`
