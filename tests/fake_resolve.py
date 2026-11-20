# just enough of Resolve's scripting api to exercise resolve_tasks without Resolve
import os


class Item:
    def __init__(self, path, start, end):
        self.path, self.start, self.end = path, start, end

    def GetMediaPoolItem(self):
        return Clip(os.path.basename(self.path), self.path)

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end


class Clip:
    def __init__(self, name, path):
        self.name, self.path, self.replaced = name, path, None

    def GetName(self):
        return self.name

    def GetClipProperty(self, k):
        return self.path if k == "File Path" else ""

    def ReplaceClip(self, p):
        self.replaced = p
        return True


class Folder:
    def __init__(self, name, clips=None):
        self.name, self.clips, self.subs = name, clips or [], []

    def GetName(self):
        return self.name

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.subs


class Timeline:
    def __init__(self, name="Edit 1"):
        self.name, self.tracks = name, [[Item("/media/a.mov", 0, 300), Item("/media/b.mov", 300, 400), Item("/media/logo.png", 0, 400)]]
        self.appended = []

    def GetName(self):
        return self.name

    def GetSetting(self, k):
        return {"timelineFrameRate": "30", "timelineResolutionWidth": "1080", "timelineResolutionHeight": "1920"}.get(k)

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return 450

    def GetTrackCount(self, kind):
        return len(self.tracks)

    def GetItemListInTrack(self, kind, i):
        return self.tracks[i - 1]

    def AddTrack(self, kind):
        self.tracks.append([])
        return True


class MediaPool:
    def __init__(self, tl):
        self.root, self.tl = Folder("Master"), tl

    def GetRootFolder(self):
        return self.root

    def AddSubFolder(self, parent, name):
        f = Folder(name)
        parent.subs.append(f)
        return f

    def SetCurrentFolder(self, f):
        self.current = f

    def ImportMedia(self, paths):
        clips = [Clip(os.path.basename(p), p) for p in paths]
        self.current.clips += clips
        return clips

    def AppendToTimeline(self, infos):
        self.tl.appended += infos
        return [object()]


class Project:
    def __init__(self):
        self.tl = Timeline()
        self.pool = MediaPool(self.tl)
        self.settings, self.jobs = {}, {}

    def GetName(self):
        return "Client cut"

    def GetCurrentTimeline(self):
        return self.tl

    def GetTimelineCount(self):
        return 1

    def GetTimelineByIndex(self, i):
        return self.tl

    def SetCurrentTimeline(self, t):
        return True

    def GetMediaPool(self):
        return self.pool

    def SetCurrentRenderMode(self, m):
        return True

    def SetCurrentRenderFormatAndCodec(self, f, c):
        return True

    def SetRenderSettings(self, s):
        self.settings.update(s)
        return True

    def AddRenderJob(self):
        self.jobs["j1"] = "Ready"
        return "j1"

    def StartRendering(self, jobs, isInteractiveMode=False):
        out = os.path.join(self.settings["TargetDir"], self.settings["CustomName"] + ".mp4")
        with open(out, "wb") as f:
            f.write(b"fake")
        self.jobs["j1"] = "Complete"
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, j):
        return {"JobStatus": self.jobs.get(j), "CompletionPercentage": 100}

    def DeleteRenderJob(self, j):
        self.jobs.pop(j, None)
        return True


class ProjectManager:
    def __init__(self):
        self.project = Project()

    def GetCurrentProject(self):
        return self.project


class FakeResolve:
    def __init__(self):
        self.pm = ProjectManager()

    def GetProjectManager(self):
        return self.pm
