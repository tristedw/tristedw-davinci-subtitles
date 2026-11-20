# resolve_menu.py
# the Workspace > Scripts entries. scripts/install-resolve.sh drops tiny stubs into Resolve's Scripts
# folder that import this and pass in the live `resolve` object Resolve hands them.
# runs under Resolve's own python, so stdlib only. works on Studio and on the free version (through 21.0),
# since nothing here needs external scripting.
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from . import resolve_tasks as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("SUBS_PORT") or 8765)
BASE = "http://localhost:%d" % PORT


def _get(path, timeout=1.5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _up():
    try:
        return bool(_get("/mtime", 0.8).get("ok"))
    except Exception:
        return False


def _say(msg, title="Subtitles"):
    # resolve's console is easy to miss, so tell the user with a mac notification too
    print("[subtitles] " + msg)
    try:
        subprocess.Popen(["osascript", "-e", "display notification %s with title %s" % (json.dumps(msg), json.dumps(title))])
    except OSError:
        pass


def _alert(msg):
    print("[subtitles] " + msg)
    try:
        subprocess.call(["osascript", "-e", 'display alert "Subtitles" message %s as warning' % json.dumps(msg)])
    except OSError:
        pass


def ensure_server():
    if _up():
        return True
    py = os.path.join(ROOT, ".venv", "bin", "python")
    if not os.path.exists(py):
        _alert("Cant find %s. Run ./scripts/install.sh in the davinci-subtitles folder first." % py)
        return False
    log = open(os.path.join(ROOT, "editor.log"), "a")
    env = dict(os.environ)
    # resolve's python env leaks into child processes and confuses the venv
    for k in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(k, None)
    subprocess.Popen([py, "-m", "subtitles", str(PORT)], cwd=ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     env=env, start_new_session=True)
    for _ in range(60):
        time.sleep(0.25)
        if _up():
            return True
    _alert("The editor didnt start. Check editor.log in %s" % ROOT)
    return False


def open_url(path="/"):
    subprocess.Popen(["open", BASE + path])


def get_resolve(scope):
    # Resolve puts `resolve` (and `bmd`) in the script's globals. fall back to asking for it
    r = scope.get("resolve")
    if r is None and scope.get("bmd") is not None:
        r = scope["bmd"].scriptapp("Resolve")
    if r is None:
        try:
            import DaVinciResolveScript as d  # noqa: N813
            r = d.scriptapp("Resolve")
        except Exception:
            r = None
    return r


# ---------- menu entries ----------
def open_app(scope):
    if ensure_server():
        open_url("/")


def caption_timeline(scope):
    # renders the open timeline from inside Resolve, then starts a new project on it.
    # same result as New project > From DaVinci Resolve, but without needing external scripting
    resolve = get_resolve(scope)
    try:
        st = T.status(resolve)
    except Exception as e:
        return _alert(str(e))
    if not st.get("timeline"):
        return _alert("Open the timeline you want captioned first.")
    if not ensure_server():
        return
    incoming = _get("/api/paths")["incoming"]
    name = "%s_%s" % ("".join(c if c.isalnum() or c in "-_" else "_" for c in st["timeline"])[:40], time.strftime("%m%d_%H%M%S"))
    _say("Rendering %s for transcription" % st["timeline"])
    last = {"p": -1}

    def progress(f):
        p = int(f * 100) // 10 * 10
        if p != last["p"]:
            last["p"] = p
            print("[subtitles] rendering %d%%" % p)

    res = T.render_timeline(resolve, incoming, name, progress=progress)
    if not res.get("ok"):
        return _alert(res.get("msg") or "Resolve render failed")
    q = urllib.parse.urlencode({"new": res["file"], "name": st["timeline"], "project": st["project"]})
    open_url("/#" + q)


def place_captions(scope):
    # puts the newest caption render (from any project) onto the open timeline
    resolve = get_resolve(scope)
    if not ensure_server():
        return
    last = _get("/api/last-render")
    if not last.get("file"):
        return _alert("Nothing rendered yet. Hit Render in the editor first.")
    try:
        res = T.place(resolve, last["file"], last["clip_name"])
    except Exception as e:
        return _alert(str(e))
    (_say if res.get("ok") else _alert)("%s: %s" % (last.get("name", ""), res.get("msg", "")))


ENTRIES = {"open": open_app, "caption": caption_timeline, "place": place_captions}


def run(entry, scope):
    try:
        ENTRIES[entry](scope)
    except Exception as e:
        _alert("%s: %s" % (type(e).__name__, e))
        raise


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "open", globals())
