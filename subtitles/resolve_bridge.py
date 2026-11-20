# resolve_bridge.py
# runs resolve_tasks against the open Resolve from outside it, through external scripting.
# that needs Resolve Studio with Preferences > System > General > External scripting using set to Local.
# the scripting module only loads in some python builds, so each call runs in whichever system python
# can import it, not the venv. on the free version use the Workspace > Scripts entries instead (resolve_menu.py).
import json
import os
import subprocess

from .config import ROOT

MOD = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
ENV = {**os.environ,
       "RESOLVE_SCRIPT_API": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
       "RESOLVE_SCRIPT_LIB": "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"}
PYS = ["/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3", "python3"]

NO_MODULE = "resolve scripting module not found"
NOT_REACHABLE = ("cant reach Resolve. Open it with a project loaded and set Preferences > System > General > "
                 "External scripting using to Local (Studio only). On the free version use Workspace > Scripts > Subtitles")

RUNNER = """
import json, sys
sys.path.append({mod!r})
sys.path.insert(0, {root!r})
def out(res):
    print("RESULT:" + json.dumps(res), flush=True)
    sys.exit(0)
try:
    import DaVinciResolveScript as d
except Exception:
    out({{"ok": False, "msg": {nomod!r}}})
r = d.scriptapp("Resolve")
if r is None:
    out({{"ok": False, "msg": {unreachable!r}}})
from subtitles import resolve_tasks as T
def progress(f):
    print("PCT %s" % f, flush=True)
kw = json.loads({kw!r})
if {wants_progress!r}:
    kw["progress"] = progress
try:
    out(T.{task}(r, **kw))
except Exception as e:
    out({{"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}})
"""


def _run(task, timeout=40, progress=None, **kw):
    code = RUNNER.format(mod=MOD, root=str(ROOT), nomod=NO_MODULE, unreachable=NOT_REACHABLE, kw=json.dumps(kw),
                         wants_progress=progress is not None, task=task)
    last = NOT_REACHABLE
    for py in PYS:
        try:
            lines = _lines(py, code, timeout, progress)
        except OSError:
            continue
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "Resolve took too long to answer (is a dialog open in it?)"}
        res = next((json.loads(ln[7:]) for ln in lines if ln.startswith("RESULT:")), None)
        if res is None:
            continue
        if res.get("msg") == NO_MODULE:
            last = NO_MODULE
            continue
        return res
    return {"ok": False, "msg": last}


def _lines(py, code, timeout, progress):
    # quick calls run with a timeout, renders stream so progress can update as they go
    if progress is None:
        r = subprocess.run([py, "-c", code], capture_output=True, text=True, env=ENV, timeout=timeout)
        return r.stdout.splitlines()
    p = subprocess.Popen([py, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV)
    out = []
    for line in p.stdout:
        if line.startswith("PCT ") and progress:
            try:
                progress(float(line.split()[1]))
            except ValueError:
                pass
        else:
            out.append(line)
    p.wait()
    return out


def status():
    return _run("status", timeout=15)


def timeline_videos():
    return _run("timeline_videos")


def place(mov, name):
    return _run("place", mov=str(mov), name=name)


def render_timeline(out_dir, name, timeline=None, progress=None):
    # no timeout worth having on a render, it takes as long as the edit is
    return _run("render_timeline", timeout=None, progress=progress, out_dir=str(out_dir), name=name, timeline=timeline)
