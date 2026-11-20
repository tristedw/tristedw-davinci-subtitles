from subtitles import render as R

DATA = {
    "fps": 30, "duration": 4,
    "speakers": {"a": {"name": "Maya", "color": "#7CC4FF", "show_name": True, "style": {}}},
    "lines": [{"id": 1, "start": 0.5, "end": 2.5, "spk": "a", "text": "Okay so this is the *new* grinder.",
               "words": [{"w": w, "start": 0.5 + i * 0.3, "end": 0.75 + i * 0.3} for i, w in enumerate("Okay so this is the new grinder.".split())]}],
    "style": {},
}


def test_migrate_fills_defaults():
    s = R.migrate({"size": 60})
    assert s["size"] == 60 and s["font"] == R.DEFAULTS["font"]


def test_hex2rgb():
    assert R.hex2rgb("#fff") == (255, 255, 255)
    assert R.hex2rgb("#7CC4FF") == (124, 196, 255)


def test_frame_draws_only_while_a_line_is_up():
    r = R.Renderer(DATA)
    assert r.frame(0.1) is None
    im = r.frame(1.5)
    assert im is not None and im.size == (R.W, R.H)
    assert im.getchannel("A").getbbox() is not None  # something got drawn
