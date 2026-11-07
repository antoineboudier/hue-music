"""Command line interface: `python -m huemusic <command>` (or `huemusic <command>`)."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

from . import config
from .analysis import fmt_time


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _choose(items: list, label) -> object:
    for i, it in enumerate(items, 1):
        print(f"  {i}. {label(it)}")
    while True:
        s = _ask(f"Choose 1-{len(items)}: ")
        if s.isdigit() and 1 <= int(s) <= len(items):
            return items[int(s) - 1]


def preset_path(name: str) -> Path:
    p = Path(name)
    if p.exists():
        return p
    p = config.PRESETS_DIR / f"{name}.yaml"
    if p.exists():
        return p
    raise SystemExit(f"preset not found: {name} (looked for {p})")


# ---------------------------------------------------------------- hue setup
def cmd_setup_hue(args) -> None:
    from . import hue

    cfg = config.load()
    h = cfg["hue"]
    ip = args.ip or h.get("bridge_ip")
    if not ip:
        print("Looking for your Hue bridge...")
        found = hue.discover()
        if len(found) == 1:
            ip = found[0]
        elif found:
            ip = _choose(found, str)
        else:
            ip = _ask("Couldn't discover it. Bridge IP (see Hue app > Settings > Bridges): ")
    print(f"Bridge: {ip}")

    if args.repair or not (h.get("app_key") and h.get("client_key")) or h.get("bridge_ip") != ip:
        print("\nPress the round link button on top of the bridge (you have 30 s)...")
        for _ in range(30):
            creds = hue.pair(ip)
            if creds:
                break
            time.sleep(1)
        else:
            raise SystemExit("Link button not pressed in time, run the command again.")
        h["app_key"], h["client_key"] = creds
        h["bridge_ip"] = ip
        config.save(cfg)
        print("Paired, credentials saved to config.json")

    bridge = hue.Bridge(ip, h["app_key"])
    areas = bridge.entertainment_areas()
    if not areas:
        raise SystemExit("No entertainment area found: create one in the Hue app (Settings > Entertainment areas).")
    print("\nEntertainment areas:")
    area = areas[0] if len(areas) == 1 else _choose(areas, hue.area_name)
    h["entertainment_id"] = area["id"]
    h["entertainment_name"] = hue.area_name(area)
    h["channels"] = bridge.channel_list(area)
    config.save(cfg)
    print(f"\nUsing area '{h['entertainment_name']}' with {len(h['channels'])} channels:")
    _print_channels(h["channels"])
    print("\nNext: `python -m huemusic identify` to see which light is which.")


def _print_channels(chs: list[dict]) -> None:
    print("   id  x      y      name")
    for c in sorted(chs, key=lambda c: c["x"]):
        print(f"  {c['id']:>3}  {c['x']:+.2f}  {c['y']:+.2f}  {c['name']}")


def _open_hue(cfg: dict):
    from .hue import EntertainmentStream

    if not config.hue_ready(cfg):
        raise SystemExit("Hue isn't set up: run `python -m huemusic setup-hue` (or use --no-hue).")
    h = cfg["hue"]
    s = EntertainmentStream(h["bridge_ip"], h["app_key"], h["client_key"], h["entertainment_id"])
    print(f"Connecting to entertainment area '{h['entertainment_name']}'...")
    s.open()
    return s


def cmd_identify(args) -> None:
    from .effects import BLACK

    from .engine import arrange

    cfg = config.load()
    chs, _, _ = arrange(config.channels(cfg), args.order, {})
    stream = _open_hue(cfg)
    try:
        print(f"Lighting each channel in turn, order: {args.order} (Ctrl+C to stop).")
        for c in chs:
            print(f"  channel {c.id:>2}  x={c.x:+.2f} y={c.y:+.2f}  {c.name}")
            end = time.monotonic() + args.seconds
            while time.monotonic() < end:
                frame = {o.id: (0.0, 0.0, 0.15) for o in chs}
                frame[c.id] = (1.0, 1.0, 1.0)
                stream.send(frame)
                time.sleep(0.04)
        stream.send({c.id: BLACK for c in chs})
    except KeyboardInterrupt:
        pass
    finally:
        stream.close()


# ------------------------------------------------------------ spotify setup
def cmd_setup_spotify(args) -> None:
    from . import spotify

    cfg = config.load()
    sc = cfg["spotify"]
    if args.client_id:
        sc["client_id"] = args.client_id
    if not sc.get("client_id"):
        print(f"""
1. Go to https://developer.spotify.com/dashboard and log in.
2. "Create app": any name/description, Redirect URI:  {sc['redirect_uri']}
   and tick "Web API".
3. Open the app's settings and copy its Client ID.
""")
        sc["client_id"] = _ask("Client ID: ")
    config.save(cfg)
    print("Logging in (your browser will open the first time)...")
    sp = spotify.client(cfg)
    me = sp.current_user()
    print(f"Logged in as {me.get('display_name') or me['id']} ({me.get('product', '?')})")
    _list_devices(sp)


def _list_devices(sp) -> None:
    devices = sp.devices().get("devices", [])
    if not devices:
        print("No active Spotify device. Open Spotify on your PC/phone (or wake your speaker) and retry.")
    for d in devices:
        print(f"  - {d['name']}  ({d['type']}){'  [active]' if d.get('is_active') else ''}")


def cmd_devices(args) -> None:
    from . import spotify

    _list_devices(spotify.client(config.load()))


# ----------------------------------------------------------------- analysis
def cmd_analyze(args) -> None:
    from .analysis import analyze, print_report, save

    print(f"Analyzing {args.audio} (takes ~10-30 s)...")
    a = analyze(args.audio)
    out = Path(args.out) if args.out else config.ROOT / "analysis" / (Path(args.audio).stem + ".json")
    out.parent.mkdir(exist_ok=True)
    save(a, out)
    print_report(a, every=args.every)
    print(f"\nSaved to {out}")
    print(f"Preset header:  bpm: {a['bpm']}   first_beat: {a['first_beat']}   duration: {a['duration']}")


# -------------------------------------------------------------------- check
def cmd_check(args) -> None:
    from .engine import Show
    from .preset import load

    p = load(preset_path(args.preset))
    g = p.grid
    print(f"{p.title} - {p.artist}   {g.bpm} BPM, bar 1 at {g.first_beat}s, {len(p.cues)} cues\n")
    print("Sections:")
    for name, (a, b) in p.sections.items():
        print(f"  {name:<12} {fmt_time(a)} (bar {g.position(a):>6})  ->  {fmt_time(b) if b != float('inf') else 'end'}")
    print("\nCues:")
    for c in p.cues:
        print(f"  #{c.index + 1:<3} {fmt_time(c.start)} -> {fmt_time(c.end)}  bar {g.position(c.start):>6}  "
              f"{c.effect.name:<9} {c.blend:<4} lights={c.lights}"
              + (f" order={c.order}" if c.order != "x" else ""))
    show = Show(p, config.channels(config.load()))
    for w in show.warnings:
        print("warning:", w)
    print("\nOK")


# --------------------------------------------------------------------- play
def cmd_play(args) -> None:
    from . import player
    from .engine import Show
    from .preset import load, parse_time, write_spotify_uri

    cfg = config.load()
    path = preset_path(args.preset)
    preset = load(path)
    show = Show(preset, config.channels(cfg))
    for w in show.warnings:
        print("warning:", w)

    use_hue = not args.no_hue
    preview = args.preview or not use_hue
    start = parse_time(args.start, preset.grid, preset.sections) if args.start else 0.0

    outputs = []
    stream = None
    try:
        if use_hue:
            stream = _open_hue(cfg)
            outputs.append(stream)

        if args.mp3 is not None:
            from .audio import LocalAudioClock

            audio = _audio_path(args.mp3, preset)
            clock = LocalAudioClock(str(audio), _device_arg(args.audio_device))
            print(f"Audio: {audio.name} -> {clock.device_name}")
            if preset.duration and abs(clock.duration - preset.duration) > 2:
                print(f"warning: this file is {fmt_time(clock.duration)} long but the preset was made for "
                      f"{fmt_time(preset.duration)}: probably a different edit, timings will be off.")
            device_key = f"local: {clock.device_name}"
        elif args.no_spotify:
            from .spotify import FreeClock

            clock = FreeClock()
            device_key = "(no spotify)"
        else:
            from . import spotify

            sp = spotify.client(cfg)
            dev = spotify.find_device(sp, args.device or cfg["spotify"].get("device"))
            track = spotify.resolve_track(sp, preset.title, preset.artist, preset.spotify_uri)
            names = ", ".join(a["name"] for a in track["artists"])
            print(f"Track: {track['name']} - {names}  ({track['uri']})")
            if not preset.spotify_uri and write_spotify_uri(path, track["uri"]):
                print(f"  saved spotify_uri into {path.name}")
            if preset.duration and abs(track["duration_ms"] / 1000 - preset.duration) > 2:
                print(f"warning: Spotify's version is {fmt_time(track['duration_ms'] / 1000)} long but the preset "
                      f"was made for {fmt_time(preset.duration)}: it's probably a different edit, timings will be off.")
            print(f"Device: {dev['name']} ({dev['type']})")
            clock = spotify.SpotifyClock(sp, track["uri"], dev["id"])
            device_key = dev["name"]

        lat_cfg = cfg["playback"]["latency_ms"]
        latency = args.latency if args.latency is not None else int(lat_cfg.get(device_key, 0))
        result = player.run(
            show, clock, outputs,
            fps=args.fps or cfg["playback"]["fps"],
            brightness=args.brightness if args.brightness is not None else cfg["playback"]["brightness"],
            latency_ms=latency, start=start, preview=preview,
        )
    finally:
        if stream:
            stream.close()

    print(f"Stopped: {result.reason}")
    if result.latency_ms != latency:
        lat_cfg[device_key] = result.latency_ms
        config.save(cfg)
        print(f"Latency for '{device_key}' saved: {result.latency_ms} ms")
    if result.taps:
        _save_taps(preset, result.taps, result.latency_ms)


def _audio_path(arg: str, preset) -> Path:
    if arg:
        p = Path(arg)
    elif preset.audio:
        p = Path(preset.audio)
        if not p.is_absolute():
            p = config.ROOT / p
    else:
        raise SystemExit("no audio file: pass one (--mp3 path/to/song.mp3) or set track.audio in the preset")
    if not p.exists():
        raise SystemExit(f"audio file not found: {p}")
    return p


def _device_arg(s: str | None):
    return int(s) if s and s.isdigit() else (s or None)


def cmd_audio_devices(args) -> None:
    import sounddevice as sd

    default_out = sd.default.device[1]
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            mark = "  [default]" if i == default_out else ""
            print(f"  {i:>3}  {d['name']}  ({sd.query_hostapis(d['hostapi'])['name']}){mark}")


def _save_taps(preset, taps, latency_ms) -> None:
    g = preset.grid
    config.TAPS_DIR.mkdir(exist_ok=True)
    path = config.TAPS_DIR / f"{preset.path.stem}-{dt.datetime.now():%Y%m%d-%H%M%S}.yaml"
    lines = [f"# taps for {preset.title}, latency {latency_ms} ms. 'beat' = nearest beat on the grid."]
    for t, k in taps:
        b = round(g.beat_index(t))
        lines.append(f'- {{key: "{k}", at: "{fmt_time(t)}", beat: "bar {b // 4 + 1} beat {b % 4 + 1}", '
                     f'off: {t - (g.first_beat + b * g.beat_len):+.3f}}}')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(taps)} taps saved to {path}")


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    if os.name == "nt":
        os.system("")  # enables ANSI escape codes in the Windows console
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    ap = argparse.ArgumentParser(prog="huemusic", description="Hue light shows synced to Spotify.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup-hue", help="pair with the bridge and choose an entertainment area")
    s.add_argument("--ip", help="bridge IP (skips discovery)")
    s.add_argument("--repair", action="store_true", help="pair again even if already paired")
    s.set_defaults(fn=cmd_setup_hue)

    s = sub.add_parser("identify", help="light each channel one by one to see which is which")
    s.add_argument("--seconds", type=float, default=1.5)
    s.add_argument("--order", choices=["x", "y", "circle"], default="x",
                   help="x = left to right, y = front to back, circle = around the room")
    s.set_defaults(fn=cmd_identify)

    s = sub.add_parser("setup-spotify", help="configure the Spotify app and log in")
    s.add_argument("--client-id")
    s.set_defaults(fn=cmd_setup_spotify)

    s = sub.add_parser("devices", help="list Spotify devices")
    s.set_defaults(fn=cmd_devices)

    s = sub.add_parser("audio-devices", help="list local audio outputs (for play --mp3)")
    s.set_defaults(fn=cmd_audio_devices)

    s = sub.add_parser("analyze",help="detect tempo/beat grid/structure of an audio file")
    s.add_argument("audio")
    s.add_argument("--out", help="output JSON (default: analysis/<name>.json)")
    s.add_argument("--every", type=int, default=1, help="print every N bars")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("check", help="validate a preset and print its timeline")
    s.add_argument("preset")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("play", help="play a song with its light show")
    s.add_argument("preset", help="preset name (presets/<name>.yaml) or path")
    s.add_argument("--start", help="start position, e.g. '1:00', 'bar 36', 'drop1', 'drop1 - 4 bars'")
    s.add_argument("--device", help="Spotify device name (substring)")
    s.add_argument("--latency", type=int, help="audio latency in ms (default: saved value for the device)")
    s.add_argument("--preview", action="store_true", help="show the lights in the terminal")
    s.add_argument("--no-hue", action="store_true", help="don't drive the lights (implies --preview)")
    s.add_argument("--mp3", nargs="?", const="", metavar="FILE",
                   help="play a local audio file instead of Spotify (default: track.audio from the preset)")
    s.add_argument("--audio-device", help="output device for --mp3 (name substring or index, see audio-devices)")
    s.add_argument("--no-spotify", action="store_true", help="don't play music, just run the timeline")
    s.add_argument("--fps", type=int)
    s.add_argument("--brightness", type=float, help="master brightness 0..1")
    s.set_defaults(fn=cmd_play)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print()
    except Exception as e:  # show a clean message for expected errors
        from .hue import HueError
        from .preset import PresetError
        from .spotify import SpotifyError

        if isinstance(e, (HueError, PresetError, SpotifyError)):
            raise SystemExit(f"error: {e}")
        raise
