from subtitles import exporters as E

DATA = {
    "speakers": {"a": {"name": "Maya", "color": "#7CC4FF"}, "b": {"name": "Jordan", "color": "#FFC567"}},
    "lines": [
        {"id": 1, "start": 0.5, "end": 2.0, "spk": "a", "text": "Okay so this is the *new* grinder."},
        {"id": 2, "start": 2.4, "end": 4.1, "spk": "b", "text": "Wait, really?"},
        {"id": 3, "start": 4.3, "end": 5.6, "spk": "b", "text": "Show me."},
    ],
    "style": {},
}


def test_timestamps():
    assert E.ts(0) == "00:00:00,000"
    assert E.ts(3725.5) == "01:02:05,500"
    assert E.ts(1.2345, ".") == "00:00:01.234" or E.ts(1.2345, ".") == "00:00:01.235"
    assert E.ts(-1) == "00:00:00,000"
    assert E.ass_ts(65.43) == "0:01:05.43"


def test_ass_color_is_bgr_with_inverted_alpha():
    assert E.ass_col("#FF8000") == "&H000080FF"
    assert E.ass_col("#FFFFFF", 0) == "&HFFFFFFFF"


def test_srt_tags_names_and_strips_emphasis():
    out = E.srt(DATA["lines"], {"a": "Maya", "b": "Jordan"})
    assert out.startswith("1\n00:00:00,500 --> 00:00:02,000\nMaya: Okay so this is the new grinder.\n")
    assert "*" not in out
    assert "3\n" in out


def test_transcript_merges_same_speaker():
    out = E.transcript(DATA["lines"], {"a": "Maya", "b": "Jordan"})
    rows = out.strip().splitlines()
    assert len(rows) == 2
    assert rows[1].endswith("Jordan: Wait, really? Show me.")


def test_export_all_writes_every_format(tmp_path):
    out, files = E.export(DATA, "all", tmp_path / "export")
    names = set(files)
    assert {"all_speakers.srt", "plain.srt", "a_maya.srt", "b_jordan.srt", "captions.vtt", "transcript.txt", "captions.ass"} <= names
    vtt = (tmp_path / "export" / "captions.vtt").read_text()
    assert vtt.startswith("WEBVTT") and "<v Jordan>Wait, really?" in vtt
    ass = (tmp_path / "export" / "captions.ass").read_text()
    assert "Style: a," in ass and "Dialogue: 0," in ass


def test_export_rejects_unknown_format(tmp_path):
    try:
        E.export(DATA, "docx", tmp_path)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
