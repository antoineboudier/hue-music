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


def test_steps_elapsed_doubling():
    from huemusic.effects import steps_elapsed

    # 32 beats, 1 -> 1/8 beat per step: 4 stages of 8 beats at 1, 2, 4, 8 steps/beat
    f = lambda b: steps_elapsed(b, 32, 1, 0.125, "doubling")
    assert f(8) == pytest.approx(8)
    assert f(16) == pytest.approx(8 + 16)
    assert f(24) == pytest.approx(8 + 16 + 32)
    assert f(32) == pytest.approx(8 + 16 + 32 + 64)
    assert steps_elapsed(4, 32, 2, None, "linear") == 2  # constant speed
    lin = lambda b: steps_elapsed(b, 32, 1, 0.25, "linear")
    assert lin(32) == pytest.approx((1 + 4) / 2 * 32)  # average rate x duration


def test_circle_order():
    # front, right, back, left of the room -> clockwise from the front
    chs = [Channel(1, "back", 0, -1), Channel(2, "left", -1, 0), Channel(3, "front", 0, 1), Channel(4, "right", 1, 0)]
    ordered, pos, cyclic = engine.arrange(chs, "circle", {})
    assert [c.name for c in ordered] == ["front", "right", "back", "left"]
    assert cyclic and pos[3] == pytest.approx(0.0) and pos[4] == pytest.approx(0.25)
    ordered, _, _ = engine.arrange(chs, [2, "front"], {})
    assert [c.id for c in ordered] == [2, 3, 1, 4]  # listed first, rest appended


def test_chase_circle_with_tail(tmp_path):
    p = preset.load(_write(tmp_path, """
cues:
  - {at: bar 1, until: bar 3, order: circle, effect: chase, every: 1, tail: 1}
"""))
    chs = [Channel(1, "back", 0, -1), Channel(2, "left", -1, 0), Channel(3, "front", 0, 1), Channel(4, "right", 1, 0)]
    show = engine.Show(p, chs)
    frame, _ = show.render(1.0 + 0.5 * 1 + 0.01)   # step 1: head on "right", tail on "front"
    assert frame[4] == pytest.approx((1, 1, 1))
    assert frame[3] == pytest.approx((0.5, 0.5, 0.5))
    assert frame[1] == frame[2] == (0, 0, 0)


def test_sweep_wraps_on_circle(tmp_path):
    p = preset.load(_write(tmp_path, """
cues:
  - {at: bar 1, until: bar 2, order: circle, effect: sweep, every: 4, width: 0.3}
"""))
    chs = [Channel(1, "back", 0, -1), Channel(2, "left", -1, 0), Channel(3, "front", 0, 1), Channel(4, "right", 1, 0)]
    frame, _ = engine.Show(p, chs).render(1.0 + 2.0 * 0.95)  # band at 0.95: near "front" (0.0)
    assert frame[3][0] > 0.7 and frame[2][0] > 0  # front lit through the wrap, left partly


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


def test_studio_parse_range():
    from huemusic.studio import parse_range

    assert parse_range(None, 100) is None
    assert parse_range("bytes=0-", 100) == (0, 99)
    assert parse_range("bytes=10-19", 100) == (10, 19)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=50-500", 100) == (50, 99)
    assert parse_range("bytes=200-", 100) is None


def test_studio_render_show():
    import base64

    from huemusic import studio

    d = studio.render_show("spotless", fps=10)
    n_ch = len(d["channels"])
    frames = base64.b64decode(d["frames"])
    assert len(frames) == (int(d["duration"] * 10) + 1) * n_ch * 3
    assert {s["name"] for s in d["sections"]} >= {"intro", "drop1", "drop2"}
    assert all(set(c) >= {"label", "start", "end", "lights"} for c in d["cues"])
    with pytest.raises(studio.NotFound):
        studio.preset_file("../config")


def test_studio_save_positions_is_local(tmp_path, monkeypatch):
    import json

    from huemusic import studio

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"hue": {"channels": [{"id": 3, "name": "a", "x": 0, "y": 0, "z": 0.5}]}}))
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    studio.save_positions({"3": {"x": 0.5, "y": -2}})
    saved = json.loads(cfg_path.read_text())["hue"]
    assert saved["channels"][0] == {"id": 3, "name": "a", "x": 0.5, "y": -1.0, "z": 0.5}  # clamped, z kept
    assert saved["positions_modified"] is True


def test_bridge_set_positions_keeps_height_and_gradients():
    sent = {}

    class FakeBridge(hue.Bridge):
        def __init__(self):
            pass

        def put(self, path, body):
            sent["path"], sent["body"] = path, body

    area = {"id": "A", "locations": {"service_locations": [
        {"service": {"rid": "s1", "rtype": "entertainment"}, "positions": [{"x": 0, "y": 0, "z": 0.7}], "equalization_factor": 1},
        {"service": {"rid": "g1", "rtype": "entertainment"}, "positions": [{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}]},
    ]}}
    updated, skipped = FakeBridge().set_positions(area, {"s1": (0.25, -0.5), "g1": (1, 1)})
    assert (updated, skipped) == (1, ["g1"])
    locs = sent["body"]["locations"]["service_locations"]
    assert sent["path"] == "entertainment_configuration/A"
    assert locs[0]["positions"] == [{"x": 0.25, "y": -0.5, "z": 0.7}]
    assert len(locs[1]["positions"]) == 2  # gradient strip untouched


def test_based_on_variant(tmp_path):
    (tmp_path / "base.yaml").write_text(
        "track: {title: T, artist: A, bpm: 120, first_beat: 1.0, duration: 60}\n"
        "palette: {mine: '#ff0000', other: '#00ff00'}\n"
        "sections: {intro: bar 1, drop: bar 3}\n"
        "cues:\n  - {in: intro, effect: solid, color: mine}\n")
    (tmp_path / "v2.yaml").write_text(
        "based_on: base\ndescription: blue version\n"
        "palette: {mine: '#0000ff'}\n"
        "sections: {outro: bar 5}\n"
        "cues:\n  - {in: drop, effect: solid, color: other}\n")
    (tmp_path / "v3.yaml").write_text(
        "based_on: v2\ninherit_cues: true\n"
        "cues:\n  - {in: intro, effect: blackout}\n")
    v2 = preset.load(tmp_path / "v2.yaml")
    assert v2.description == "blue version" and v2.based_on == "base"
    assert v2.palette["mine"] == (0, 0, 1) and v2.palette["other"] == (0, 1, 0)  # merged, variant wins
    assert list(v2.sections) == ["intro", "drop", "outro"] and v2.grid.bpm == 120
    assert [c.label for c in v2.cues] == ["solid"] and v2.cues[0].start == 5.0  # own cues replace the base's
    assert [f.stem for f in v2.files] == ["v2", "base"]
    v3 = preset.load(tmp_path / "v3.yaml")
    assert [c.effect.name for c in v3.cues] == ["solid", "blackout"]  # v2's cues + its own
    assert v3.description == ""  # not inherited


def test_based_on_errors(tmp_path):
    (tmp_path / "a.yaml").write_text("based_on: b\n")
    (tmp_path / "b.yaml").write_text("based_on: a\n")
    with pytest.raises(preset.PresetError, match="loop"):
        preset.load(tmp_path / "a.yaml")
    (tmp_path / "c.yaml").write_text("based_on: missing\n")
    with pytest.raises(preset.PresetError, match="not found"):
        preset.load(tmp_path / "c.yaml")


def _lit(show, t, thr=0.05):
    return sorted(k for k, v in show.render(t)[0].items() if max(v) > thr)


def test_sequence_effect(tmp_path):
    # 120 BPM, step 0.5 beat = 0.25 s per symbol
    p = preset.load(_write(tmp_path, """
cues:
  - {at: 1, dur: 3, order: circle, effect: sequence, step: 0.5, pattern: "> . > - . A . .", decay: 0}
  - {at: 4, dur: 1, effect: sequence, step: 0.5, pattern: "L R", repeat: no}
"""))
    chs = [Channel(0, "front", 0, 1), Channel(1, "right", 1, 0), Channel(2, "back", 0, -1), Channel(3, "left", -1, 0)]
    s = engine.Show(p, chs)
    t = lambda step: 1 + step * 0.25 + 0.01
    assert _lit(s, t(0)) == [0]            # first > : first light around the circle
    assert _lit(s, t(1)) == []             # rest
    assert _lit(s, t(2)) == [1] == _lit(s, t(3))  # next light, held one more step
    assert _lit(s, t(5)) == [0, 1, 2, 3]   # A
    assert _lit(s, t(8)) == [2]            # 2nd pass: > keeps counting around the room
    # L then R: halves by x (left=-1, then front/back at x=0 by id, then right)
    assert _lit(s, 4.01) == [0, 3] and _lit(s, 4.26) == [1, 2]
    assert _lit(s, 4.51) == []             # repeat: no


def test_sequence_rejects_unknown_symbols(tmp_path):
    with pytest.raises(preset.PresetError, match="unknown symbol"):
        preset.load(_write(tmp_path, 'cues:\n  - {at: 0, dur: 1, effect: sequence, pattern: "1 X 2"}\n'))


def test_studio_append_cue(tmp_path, monkeypatch):
    from huemusic import studio

    monkeypatch.setattr(config, "PRESETS_DIR", tmp_path)
    f = tmp_path / "song.yaml"
    f.write_text("track: {bpm: 120}\ncues:\n  - {at: 0, dur: 1, effect: solid}\n")
    snippet = '  - at: bar 2\n    dur: 2 beats\n    effect: sequence\n    pattern: "L R"\n'
    assert studio.append_cue("song", snippet) == {"cues": 2}
    # a file where cues isn't the last section: refused, and left untouched
    g = tmp_path / "other.yaml"
    original = "cues:\n  - {at: 0, dur: 1, effect: solid}\ntrack: {bpm: 120}\n"
    g.write_text(original)
    with pytest.raises(preset.PresetError):
        studio.append_cue("other", snippet)
    assert g.read_text() == original


def test_studio_live_streams_and_times_out(monkeypatch):
    import time as _time

    from huemusic import studio

    sent, closed = [], []

    class FakeStream:
        def __init__(self, *a):
            pass

        def open(self):
            pass

        def send(self, frame, brightness=1.0):
            sent.append(frame)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(hue, "EntertainmentStream", FakeStream)
    monkeypatch.setattr(config, "hue_ready", lambda cfg: True)
    live = studio.Live()
    live.TIMEOUT = 0.4
    assert live.start("spotless")["on"]
    live.update("spotless", 66.0, True, 0.5, 100)   # drop 1, half speed, lights delayed 100 ms
    _time.sleep(0.2)
    assert len(sent) >= 5
    assert live.t == 66.0 and live.rate == 0.5 and abs(live.offset - 0.1) < 1e-9
    _time.sleep(0.6)                                  # no heartbeat -> stops by itself
    assert closed and live.stream is None
    assert live.update("spotless", 0, False, 1, 0) == {"on": False, "error": live.error}


def test_write_spotify_uri(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("track:\n  spotify_uri:              # filled automatically\n  bpm: 1\n")
    assert preset.write_spotify_uri(p, "spotify:track:abc")
    assert "  spotify_uri: spotify:track:abc\n  bpm: 1" in p.read_text()
