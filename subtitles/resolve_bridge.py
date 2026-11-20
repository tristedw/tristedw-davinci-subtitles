# resolve_bridge.py
# talks to the open DaVinci Resolve project. the scripting api only loads in some python builds,
# so the snippets run in whichever system python can import it, not the venv.
import json
import os
import subprocess

MOD = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
ENV = {**os.environ,
       "RESOLVE_SCRIPT_API": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
       "RESOLVE_SCRIPT_LIB": "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"}
PYS = ["/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3", "python3"]

HEAD = f"""
import sys, json
sys.path.append({MOD!r})
def out(**k):
    print("RESULT:" + json.dumps(k))
    sys.exit(0)
try:
    import DaVinciResolveScript as d
except Exception as e:
    out(ok=False, msg="resolve scripting module not found")
r = d.scriptapp("Resolve")
if r is None:
    out(ok=False, msg="Resolve isnt open (or Preferences > System > General > External scripting is off)")
proj = r.GetProjectManager().GetCurrentProject()
if proj is None:
    out(ok=False, msg="no project open in Resolve")
mp = proj.GetMediaPool()
def walk(f):
    for c in f.GetClipList() or []:
        yield c
    for s in f.GetSubFolderList() or []:
        yield from walk(s)
"""


def _run(body, timeout=40):
    code = HEAD + body
    last = "couldnt reach Resolve"
    for py in PYS:
        try:
            r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=timeout, env=ENV)
        except Exception:
            continue
        for line in r.stdout.splitlines():
            if line.startswith("RESULT:"):
                res = json.loads(line[7:])
                if res.get("msg") == "resolve scripting module not found":
                    last = res["msg"]
                    break
                return res
    return {"ok": False, "msg": last}


def status():
    return _run("""
tl = proj.GetCurrentTimeline()
out(ok=True, project=proj.GetName(), timeline=tl.GetName() if tl else None)
""", timeout=15)


def timeline_videos():
    # source files used on the current timeline, longest first. used to start a project from what's already cut
    return _run("""
tl = proj.GetCurrentTimeline()
if tl is None:
    out(ok=False, msg="no timeline open")
seen = {}
for i in range(1, tl.GetTrackCount("video") + 1):
    for it in tl.GetItemListInTrack("video", i) or []:
        mpi = it.GetMediaPoolItem()
        if not mpi:
            continue
        p = mpi.GetClipProperty("File Path")
        if not p or p.lower().endswith((".png", ".jpg", ".tif", ".tiff")) or "_captions" in p:
            continue
        seen[p] = seen.get(p, 0) + (it.GetEnd() - it.GetStart())
files = sorted(seen, key=lambda p: -seen[p])
fps = float(tl.GetSetting("timelineFrameRate") or 30)
out(ok=True, files=files, timeline=tl.GetName(), project=proj.GetName(), fps=fps,
    duration=round((tl.GetEndFrame() - tl.GetStartFrame()) / fps, 2),
    width=int(tl.GetSetting("timelineResolutionWidth") or 1080), height=int(tl.GetSetting("timelineResolutionHeight") or 1920))
""")


def place(mov, name):
    # swap the new render into the captions clip if its already in the pool, otherwise import it
    # into a Captions bin and drop it on a new top video track at the start of the current timeline
    return _run(f"""
MOV, NAME = {str(mov)!r}, {name!r}
hit = [c for c in walk(mp.GetRootFolder()) if c.GetName().startswith(NAME)]
if hit:
    ok = [bool(c.ReplaceClip(MOV)) for c in hit]
    out(ok=all(ok), msg=f"swapped into {{sum(ok)}} clip(s) in Resolve")
root = mp.GetRootFolder()
b = next((f for f in root.GetSubFolderList() or [] if f.GetName() == "Captions"), None) or mp.AddSubFolder(root, "Captions")
mp.SetCurrentFolder(b)
items = mp.ImportMedia([MOV])
if not items:
    out(ok=False, msg="Resolve wouldnt import the render")
tl = proj.GetCurrentTimeline()
if tl is None:
    out(ok=True, msg="imported into the Captions bin (no timeline open to place it on)")
tl.AddTrack("video")
n = tl.GetTrackCount("video")
placed = mp.AppendToTimeline([{{"mediaPoolItem": items[0], "trackIndex": n, "recordFrame": tl.GetStartFrame()}}])
out(ok=bool(placed), msg=f"added to V{{n}} on {{tl.GetName()}}" if placed else "imported into the Captions bin, drag it onto the timeline")
""")


def render_timeline(out_dir, name, timeline=None, progress=None):
    # renders a light h264 copy of the timeline (picture + mixed audio) so captions line up with the edit,
    # not with one source clip. progress(pct) gets called while it goes
    body = f"""
OUT, NAME, WANT = {str(out_dir)!r}, {name!r}, {timeline!r}
import time
if WANT:
    for i in range(1, proj.GetTimelineCount() + 1):
        t = proj.GetTimelineByIndex(i)
        if t and t.GetName() == WANT:
            proj.SetCurrentTimeline(t)
            break
tl = proj.GetCurrentTimeline()
if tl is None:
    out(ok=False, msg="no timeline open in Resolve")
if WANT and tl.GetName() != WANT:
    out(ok=False, msg=f"cant find a timeline called {{WANT}} in the open Resolve project")
w = int(tl.GetSetting("timelineResolutionWidth") or 1080)
h = int(tl.GetSetting("timelineResolutionHeight") or 1920)
k = min(1.0, 1280 / max(w, h))  # small copy, its only for transcribing + the editor preview
rw, rh = int(w * k) // 2 * 2, int(h * k) // 2 * 2
proj.SetCurrentRenderMode(1)
proj.SetCurrentRenderFormatAndCodec("mp4", "H264")
ok = proj.SetRenderSettings({{"SelectAllFrames": True, "TargetDir": OUT, "CustomName": NAME, "ExportVideo": True,
                              "ExportAudio": True, "FormatWidth": rw, "FormatHeight": rh}})
if not ok:
    out(ok=False, msg="Resolve rejected the render settings")
jid = proj.AddRenderJob()
if not jid:
    out(ok=False, msg="Resolve wouldnt add the render job")
proj.StartRendering([jid], isInteractiveMode=False)
while proj.IsRenderingInProgress():
    st = proj.GetRenderJobStatus(jid) or {{}}
    print("PCT", st.get("CompletionPercentage", 0), flush=True)
    time.sleep(1)
st = proj.GetRenderJobStatus(jid) or {{}}
proj.DeleteRenderJob(jid)
if st.get("JobStatus") != "Complete":
    out(ok=False, msg="Resolve render " + str(st.get("JobStatus")) + " " + str(st.get("Error", "")))
out(ok=True, timeline=tl.GetName(), file=OUT.rstrip("/") + "/" + NAME + ".mp4")
"""
    code = HEAD + body
    last = "couldnt reach Resolve"
    for py in PYS:
        try:
            p = subprocess.Popen([py, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV)
        except Exception:
            continue
        res = None
        for line in p.stdout:
            if line.startswith("PCT ") and progress:
                try:
                    progress(float(line.split()[1]) / 100)
                except ValueError:
                    pass
            elif line.startswith("RESULT:"):
                res = json.loads(line[7:])
        p.wait()
        if res is None:
            continue
        if res.get("msg") == "resolve scripting module not found":
            last = res["msg"]
            continue
        return res
    return {"ok": False, "msg": last}
