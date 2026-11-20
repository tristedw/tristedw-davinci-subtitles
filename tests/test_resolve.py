import json
import sys
from pathlib import Path

from fake_resolve import Clip, FakeResolve

from subtitles import resolve_bridge
from subtitles import resolve_tasks as T

TESTS = Path(__file__).resolve().parent


def test_status_and_timeline_videos():
    r = FakeResolve()
    assert T.status(r) == {"ok": True, "project": "Client cut", "timeline": "Edit 1"}
    res = T.timeline_videos(r)
    # longest use first, stills skipped
    assert res["files"] == ["/media/a.mov", "/media/b.mov"]
    assert res["duration"] == 15.0 and res["width"] == 1080


def test_render_timeline_writes_a_small_copy(tmp_path):
    r = FakeResolve()
    seen = []
    res = T.render_timeline(r, str(tmp_path), "tl", progress=seen.append)
    assert res["ok"] and Path(res["file"]).exists()
    s = r.pm.project.settings
    assert (s["FormatWidth"], s["FormatHeight"]) == (720, 1280)
    assert r.pm.project.jobs == {}  # cleaned up its job


def test_place_imports_then_swaps():
    r = FakeResolve()
    first = T.place(r, "/renders/clip_captions_1.mov", "clip_captions")
    assert first["ok"] and "V2" in first["msg"]
    pool = r.pm.project.pool.root
    assert pool.subs[0].GetName() == "Captions"
    second = T.place(r, "/renders/clip_captions_2.mov", "clip_captions")
    assert second["msg"].startswith("swapped into 1")
    assert pool.subs[0].clips[0].replaced == "/renders/clip_captions_2.mov"


def test_no_project_is_a_clear_error():
    r = FakeResolve()
    r.pm.project = None
    try:
        T.status(r)
    except RuntimeError as e:
        assert "no project" in str(e)
    else:
        raise AssertionError


def test_bridge_runs_tasks_in_a_separate_python(tmp_path, monkeypatch):
    # stand in for Blackmagic's scripting module, the bridge runs the same code against it
    mod = tmp_path / "mod"
    mod.mkdir()
    (mod / "DaVinciResolveScript.py").write_text(
        f"import sys\nsys.path.insert(0, {str(TESTS)!r})\nfrom fake_resolve import FakeResolve\n"
        "def scriptapp(name):\n    return FakeResolve()\n")
    monkeypatch.setattr(resolve_bridge, "MOD", str(mod))
    monkeypatch.setattr(resolve_bridge, "PYS", [sys.executable])
    assert resolve_bridge.status()["timeline"] == "Edit 1"
    seen = []
    res = resolve_bridge.render_timeline(tmp_path / "out", "tl", progress=seen.append)
    assert res["ok"] and Path(res["file"]).exists()
    assert resolve_bridge.place("/x/c_captions.mov", "c_captions")["ok"]


def test_bridge_explains_when_resolve_is_unreachable(tmp_path, monkeypatch):
    mod = tmp_path / "mod"
    mod.mkdir()
    (mod / "DaVinciResolveScript.py").write_text("def scriptapp(name):\n    return None\n")
    monkeypatch.setattr(resolve_bridge, "MOD", str(mod))
    monkeypatch.setattr(resolve_bridge, "PYS", [sys.executable])
    res = resolve_bridge.status()
    assert not res["ok"] and "External scripting" in res["msg"]


def test_menu_entries_pick_up_the_resolve_global(monkeypatch):
    from subtitles import resolve_menu as M
    fake = FakeResolve()
    assert M.get_resolve({"resolve": fake}) is fake

    class Bmd:
        def scriptapp(self, name):
            return fake

    assert M.get_resolve({"bmd": Bmd()}) is fake


def test_place_captions_uses_the_newest_render(monkeypatch):
    from subtitles import resolve_menu as M
    fake = FakeResolve()
    said = []
    monkeypatch.setattr(M, "ensure_server", lambda: True)
    monkeypatch.setattr(M, "_get", lambda path, timeout=1.5: {"file": "/r/x_captions_1.mov", "clip_name": "x_captions", "name": "X"})
    monkeypatch.setattr(M, "_say", said.append)
    monkeypatch.setattr(M, "_alert", lambda m: said.append("ALERT " + m))
    M.place_captions({"resolve": fake})
    assert said and said[0].startswith("X: added to V2")
    assert json.dumps(fake.pm.project.tl.appended[0]["trackIndex"]) == "2"
    assert isinstance(fake.pm.project.pool.root.subs[0].clips[0], Clip)
