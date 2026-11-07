import struct
from pathlib import Path

import pytest

from huemusic import config, engine, hue, preset
from huemusic.effects import Channel

ROOT = Path(__file__).resolve().parent.parent
G = preset.Grid(bpm=120, first_beat=1.0)  # 0.5 s per beat, 2 s per bar


@pytest.mark.parametrize("expr,expected", [
    (12.5, 12.5),
    ("12.5", 12.5),
    ("1:05.5", 65.5),
    ("bar 1", 1.0),
    ("bar 3", 5.0),
    ("bar 3 beat 2", 5.5),
    ("beat 5", 3.0),
    ("bar 3 + 2 beats", 6.0),
    ("bar 3 - 1 bar", 3.0),
    ("drop", 21.0),
    ("drop.end", 41.0),
    ("drop + 250ms", 21.25),
])
def test_parse_time(expr, expected):
    sections = {"drop": (21.0, 41.0)}
    assert preset.parse_time(expr, G, sections) == pytest.approx(expected)


def test_parse_time_errors():
    with pytest.raises(preset.PresetError):
        preset.parse_time("nope", G, {})


def test_durations_and_beats():
    assert preset.parse_duration("2 bars", G) == 4.0
    assert preset.parse_duration("3 beats", G) == 1.5
    assert preset.parse_beats(0.5, G) == 0.5
    assert preset.parse_beats("1 bar", G) == 4.0
    assert preset.parse_beats("1s", G) == 2.0


def test_colors():
    assert preset.parse_color("#ff0000", {}) == (1.0, 0.0, 0.0)
    assert preset.parse_color([0, 255, 0], {}) == (0.0, 1.0, 0.0)
    assert preset.parse_color("Mine", {"mine": (0.1, 0.2, 0.3)}) == (0.1, 0.2, 0.3)


def _write(tmp_path, body: str) -> Path:
    p = tmp_path / "t.yaml"
    p.write_text("track: {title: T, artist: A, bpm: 120, first_beat: 1.0, duration: 60}\n" + body)
    return p


def test_sections_and_layering(tmp_path):
    p = preset.load(_write(tmp_path, """
sections: {a: bar 1, b: bar 3}
cues:
  - {in: a, effect: solid, color: red}
  - {in: a, effect: solid, color: blue, blend: add, lights: right}
  - {in: b, effect: solid, color: white, intensity: 0.5}
"""))
    assert p.sections["a"] == (1.0, 5.0)
    assert p.sections["b"] == (5.0, 60.0)
    show = engine.Show(p, config.demo_channels(4))
    frame, active = show.render(2.0)
    assert frame[0] == (1.0, 0.0, 0.0)          # left: red only
    assert frame[3] == (1.0, 0.0, 1.0)          # right: red + blue
    frame, _ = show.render(6.0)
    assert frame[0] == pytest.approx((0.5, 0.5, 0.5))  # white at 50% over black
    frame, active = show.render(0.5)            # before any cue
    assert frame[0] == (0, 0, 0) and active == []


def test_pulse_hits_on_beats(tmp_path):
    p = preset.load(_write(tmp_path, """
cues:
  - {at: bar 1, until: bar 2, effect: pulse, color: white, every: 1, decay: 0.5}
"""))
    show = engine.Show(p, config.demo_channels(2))
    on, _ = show.render(1.5)       # exactly on beat 2
    off, _ = show.render(1.5 + 0.3)  # 60% through the beat, past the decay
    assert on[0] == (1.0, 1.0, 1.0)
    assert off[0] == (0.0, 0.0, 0.0)


def test_bad_cue_reports_position(tmp_path):
    with pytest.raises(preset.PresetError, match="cue #1"):
        preset.load(_write(tmp_path, "cues:\n  - {at: 0, dur: 1, effect: solid, colour: red}\n"))
    with pytest.raises(preset.PresetError, match="unknown effect"):
        preset.load(_write(tmp_path, "cues:\n  - {at: 0, dur: 1, effect: lazer}\n"))


def test_selectors():
    chs = [Channel(1, "Desk strip", -1), Channel(2, "Lamp", 0), Channel(3, "TV strip", 1)]
    ids = lambda spec, groups={}: [c.id for c in engine.select(spec, chs, groups)]
    assert ids("all") == [1, 2, 3]
    assert ids("left") == [1, 2]
    assert ids("right") == [3]
    assert ids("strip") == [1, 3]
    assert ids([3, "lamp"]) == [2, 3]
    assert ids("mine", {"mine": [1, 3]}) == [1, 3]


def test_stream_message():
    area = "12345678-1234-1234-1234-123456789abc"
    msg = hue.build_message(area, {0: (1.0, 0.0, 0.5), 7: (0.0, 1.0, 0.0)})
    assert msg[:9] == b"HueStream"
    assert msg[9:11] == b"\x02\x00"
    assert msg[16:52] == area.encode()
    assert len(msg) == 52 + 2 * 7
    assert struct.unpack(">BHHH", msg[52:59]) == (0, 65535, 0, 32767)


def test_spotless_preset_loads():
    p = preset.load(ROOT / "presets" / "spotless.yaml")
    show = engine.Show(p, config.demo_channels())
    assert not show.warnings
    t = 0.0
    while t < p.duration:
        frame, _ = show.render(t)
        assert all(0.0 <= v <= 1.0 for rgb in frame.values() for v in rgb)
        t += 0.05


def test_write_spotify_uri(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("track:\n  spotify_uri:              # filled automatically\n  bpm: 1\n")
    assert preset.write_spotify_uri(p, "spotify:track:abc")
    assert "  spotify_uri: spotify:track:abc\n  bpm: 1" in p.read_text()
