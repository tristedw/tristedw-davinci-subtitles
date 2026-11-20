# resolve_tasks.py
# everything this app asks Resolve to do, written against a live `resolve` object.
# two callers use it:
#   resolve_bridge.py  from outside Resolve, through external scripting (Studio)
#   resolve_menu.py    from inside Resolve, through Workspace > Scripts (Studio and free)
# Resolve runs these under whatever system python it finds, so stdlib only and nothing newer than 3.6.
import os
import time

STILLS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx")


def _project(resolve):
    if resolve is None:
        raise RuntimeError("couldnt reach Resolve")
    proj = resolve.GetProjectManager().GetCurrentProject()
    if proj is None:
        raise RuntimeError("no project open in Resolve")
    return proj


def _walk(folder):
    for c in folder.GetClipList() or []:
        yield c
    for s in folder.GetSubFolderList() or []:
        for c in _walk(s):
            yield c


def _timeline_info(tl):
    fps = float(tl.GetSetting("timelineFrameRate") or 30)
    return {
        "timeline": tl.GetName(),
        "fps": fps,
        "duration": round((tl.GetEndFrame() - tl.GetStartFrame()) / fps, 2),
        "width": int(tl.GetSetting("timelineResolutionWidth") or 1080),
        "height": int(tl.GetSetting("timelineResolutionHeight") or 1920),
    }


def status(resolve):
    proj = _project(resolve)
    tl = proj.GetCurrentTimeline()
    return {"ok": True, "project": proj.GetName(), "timeline": tl.GetName() if tl else None}


def timeline_videos(resolve):
    # source files used on the current timeline, longest first. used to start a project from what's already cut
    proj = _project(resolve)
    tl = proj.GetCurrentTimeline()
    if tl is None:
        return {"ok": False, "msg": "no timeline open"}
    seen = {}
    for i in range(1, tl.GetTrackCount("video") + 1):
        for it in tl.GetItemListInTrack("video", i) or []:
            mpi = it.GetMediaPoolItem()
            if not mpi:
                continue
            p = mpi.GetClipProperty("File Path")
            if not p or p.lower().endswith(STILLS) or "_captions" in p:
                continue
            seen[p] = seen.get(p, 0) + (it.GetEnd() - it.GetStart())
    out = {"ok": True, "files": sorted(seen, key=lambda p: -seen[p]), "project": proj.GetName()}
    out.update(_timeline_info(tl))
    return out


def render_timeline(resolve, out_dir, name, timeline=None, progress=None):
    # renders a light h264 copy of the timeline (picture + mixed audio) so captions line up with the edit,
    # not with one source clip. progress(0..1) gets called while it goes
    proj = _project(resolve)
    if timeline:
        for i in range(1, proj.GetTimelineCount() + 1):
            t = proj.GetTimelineByIndex(i)
            if t and t.GetName() == timeline:
                proj.SetCurrentTimeline(t)
                break
    tl = proj.GetCurrentTimeline()
    if tl is None:
        return {"ok": False, "msg": "no timeline open in Resolve"}
    if timeline and tl.GetName() != timeline:
        return {"ok": False, "msg": "cant find a timeline called %s in the open Resolve project" % timeline}
    info = _timeline_info(tl)
    k = min(1.0, 1280.0 / max(info["width"], info["height"]))  # small copy, its only for transcribing + the editor preview
    rw, rh = int(info["width"] * k) // 2 * 2, int(info["height"] * k) // 2 * 2
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    proj.SetCurrentRenderMode(1)
    proj.SetCurrentRenderFormatAndCodec("mp4", "H264")
    ok = proj.SetRenderSettings({"SelectAllFrames": True, "TargetDir": str(out_dir), "CustomName": name, "ExportVideo": True,
                                 "ExportAudio": True, "FormatWidth": rw, "FormatHeight": rh})
    if not ok:
        return {"ok": False, "msg": "Resolve rejected the render settings"}
    jid = proj.AddRenderJob()
    if not jid:
        return {"ok": False, "msg": "Resolve wouldnt add the render job"}
    proj.StartRendering([jid], isInteractiveMode=False)
    while proj.IsRenderingInProgress():
        st = proj.GetRenderJobStatus(jid) or {}
        if progress:
            progress(float(st.get("CompletionPercentage", 0)) / 100)
        time.sleep(1)
    st = proj.GetRenderJobStatus(jid) or {}
    proj.DeleteRenderJob(jid)
    if st.get("JobStatus") != "Complete":
        return {"ok": False, "msg": "Resolve render %s %s" % (st.get("JobStatus"), st.get("Error", ""))}
    out = {"ok": True, "file": os.path.join(str(out_dir), name + ".mp4"), "project": proj.GetName()}
    out.update(info)
    return out


def place(resolve, mov, name):
    # swap the new render into the captions clip if its already in the pool, otherwise import it
    # into a Captions bin and drop it on a new top video track at the start of the current timeline
    proj = _project(resolve)
    mp = proj.GetMediaPool()
    root = mp.GetRootFolder()
    hit = [c for c in _walk(root) if (c.GetName() or "").startswith(name)]
    if hit:
        ok = [bool(c.ReplaceClip(str(mov))) for c in hit]
        return {"ok": all(ok), "msg": "swapped into %d clip(s) in Resolve" % sum(ok)}
    b = next((f for f in root.GetSubFolderList() or [] if f.GetName() == "Captions"), None) or mp.AddSubFolder(root, "Captions")
    mp.SetCurrentFolder(b)
    items = mp.ImportMedia([str(mov)])
    if not items:
        return {"ok": False, "msg": "Resolve wouldnt import the render"}
    tl = proj.GetCurrentTimeline()
    if tl is None:
        return {"ok": True, "msg": "imported into the Captions bin (no timeline open to place it on)"}
    tl.AddTrack("video")
    n = tl.GetTrackCount("video")
    placed = mp.AppendToTimeline([{"mediaPoolItem": items[0], "trackIndex": n, "recordFrame": tl.GetStartFrame()}])
    if placed:
        return {"ok": True, "msg": "added to V%d on %s" % (n, tl.GetName())}
    return {"ok": True, "msg": "imported into the Captions bin, drag it onto the timeline"}
