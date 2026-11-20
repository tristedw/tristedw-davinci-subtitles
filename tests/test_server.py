import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from subtitles import server


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECTS", tmp_path)
    monkeypatch.setattr(server, "TRASH", tmp_path / ".trash")
    d = tmp_path / "clip"
    d.mkdir()
    (d / "project.json").write_text(json.dumps({"name": "Clip", "video": str(tmp_path / "missing.mp4"), "status": "ready"}))
    (d / "captions_edit.json").write_text(json.dumps({"speakers": {}, "lines": [], "style": {}}))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_lists_projects(app):
    code, body = get(app + "/api/projects")
    assert code == 200
    projects = json.loads(body)["projects"]
    assert [p["slug"] for p in projects] == ["clip"]
    assert projects[0]["video_ok"] is False


def test_serves_pages(app):
    assert get(app + "/")[0] == 200
    assert get(app + "/p/clip/")[0] == 200
    assert get(app + "/app.css")[0] == 200


def test_blocks_other_sites(app):
    assert post(app + "/quit", {}, {"Origin": "https://example.com"})[0] == 403
    assert get(app + "/api/projects", {"Host": "evil.example"})[0] == 403


def test_rejects_path_tricks(app):
    assert get(app + "/p/..%2F..%2Fetc/data")[0] == 404
    assert get(app + "/p/.trash/data")[0] == 404


def test_save_refuses_stale_writes(app):
    code, body = get(app + "/p/clip/data")
    data = json.loads(body)
    base = data.pop("_mtime")
    assert post(app + "/p/clip/save", {**data, "_base": base})[0] == 200
    # same base again is now stale, the file moved on
    code, body = post(app + "/p/clip/save", {**data, "_base": base - 100})
    assert code == 409 and json.loads(body)["conflict"]
