from subtitles import pipeline as P


def W(w, start, dur=0.25, spk=0, p=0.9):
    return {"w": w, "start": start, "end": start + dur, "p": p, "spk": spk, "sure": 1.0, "src": "whisper"}


def sentence(text, start, spk=0, gap=0.05):
    out, t = [], start
    for w in text.split():
        out.append(W(w, t, spk=spk))
        t += 0.25 + gap
    return out


def test_plan_skips_steps_from_settings():
    start, steps = P.make_plan("probe", {"quality": "fast", "quiet": False, "speakers": 1})
    assert start == "probe"
    assert steps == ["probe", "audio", "transcribe", "lines"]


def test_plan_resolve_projects_rerender_the_timeline():
    start, steps = P.make_plan("probe", {"quality": "best", "quiet": True}, source="resolve")
    assert start == "resolve" and steps[0] == "resolve" and "refine" in steps and "quiet" in steps


def test_glue_rejoins_whisper_fragments():
    words = [{"w": "y", "start": 0, "end": .1, "p": .9}, {"w": "'all", "start": .1, "end": .2, "p": .5},
             {"w": "$40", "start": .3, "end": .4, "p": .9}, {"w": ",000", "start": .4, "end": .5, "p": .9}]
    out = P.glue(words)
    assert [w["w"] for w in out] == ["y'all", "$40,000"]
    assert out[0]["p"] == .5


def test_chunk_keeps_lines_under_max_and_never_orphans():
    ws = sentence("this is a fairly long sentence that should get split into a couple of even lines", 0)
    parts = P.chunk(ws, 30)
    assert len(parts) > 1
    assert all(len(p) >= 2 for p in parts)
    assert sum(len(p) for p in parts) == len(ws)


def test_split_lines_gives_each_sentence_one_speaker():
    P.cfg.clear()
    P.cfg.update({"max_chars": 42, "max_dur": 4.5})
    words = sentence("Okay so this is the new grinder.", 0, spk=0) + sentence("Wait, really?", 3.0, spk=1)
    lines = P.split_lines(words)
    assert [ln["spk"] for ln in lines] == [0, 1]
    assert " ".join(w["w"] for w in lines[1]["words"]) == "Wait, really?"
