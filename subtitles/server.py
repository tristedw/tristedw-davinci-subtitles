# server.py
# local caption app. project launcher on /, subtitle editor on /p/<project>/
# run:  python -m subtitles   then open http://localhost:8765
#
# every clip is its own folder in the projects dir (see config.py):
#   project.json        name, source video, resolve clip name, transcribe settings, status
#   captions_edit.json  lines, words, speakers, style (what the editor + renderer use)
#   work/               audio, whisper + diarization output (rebuildable)
#   export/ renders/ .history/
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import render as R
from . import resolve_bridge as RB
from . import tools  # noqa: F401  (puts homebrew ffmpeg on PATH)
from .config import FONTS, PORT, PRESETS, PROJECTS, ROOT, SYNC, TRASH, WEB
from .exporters import export

PY = sys.executable
KEEP_RENDERS = 3
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mxf")

DEFAULT_SETTINGS = {"lang": "en", "speakers": 0, "names": "", "vocab": "", "quality": "best", "quiet": False,
                    "preset": "", "max_chars": 42, "max_dur": 4.5}

# ---------- git sync ----------
# optional. if the projects folder is its own git repo with a remote (a private one, your
# captions are client work), saves get committed and pushed there a little while later and the
# app pulls on start. the app code repo is never touched.
SYNC_DELAY = 20
sync = {"state": "idle", "msg": "", "at": 0}
_sync_timer = {"t": None}
_sync_lock = threading.Lock()


def git(*a, timeout=60):
    return subprocess.run(["git", *a], cwd=PROJECTS, capture_output=True, text=True, timeout=timeout)


def git_ok():
    if SYNC == "off" or not (PROJECTS / ".git").exists():
        return False
    if SYNC == "on":
        return True
    return bool(git("remote").stdout.strip())


def run_sync(msg="captions edit"):
    if not git_ok():
        return
    with _sync_lock:
        sync.update(state="syncing", msg="")
        try:
            git("add", "-A")
            if git("diff", "--cached", "--quiet").returncode != 0:
                git("commit", "-q", "-m", msg)
            r = git("pull", "-q", "--rebase", "--autostash")
            if r.returncode != 0:
                git("rebase", "--abort")
                raise RuntimeError("pull failed, nothing pushed: " + r.stderr.strip()[-200:])
            r = git("push", "-q", timeout=120)
            if r.returncode != 0:
                raise RuntimeError("push failed: " + r.stderr.strip()[-200:])
            sync.update(state="ok", msg="synced", at=time.time())
        except Exception as e:
            sync.update(state="error", msg=str(e), at=time.time())


def schedule_sync(msg="captions edit"):
    if not git_ok():
        return
    if _sync_timer["t"]:
        _sync_timer["t"].cancel()
    t = threading.Timer(SYNC_DELAY, run_sync, args=(msg,))
    t.daemon = True
    _sync_timer["t"] = t
    sync.update(state="pending", msg="")
    t.start()


def pull_on_start():
    if not git_ok():
        return
    r = git("pull", "-q", "--rebase", "--autostash")
    if r.returncode != 0:
        git("rebase", "--abort")
        print("projects pull failed:", r.stderr.strip())
    else:
        print("pulled latest projects")


# ---------- fonts ----------
FONT_DIRS = [("Project", FONTS), ("Yours", Path.home() / "Library/Fonts"), ("Mac", Path("/Library/Fonts")),
             ("Mac", Path("/System/Library/Fonts/Supplemental"))]
_font_index = {"stamp": None, "list": []}


def font_index():
    stamp = tuple((str(d), d.stat().st_mtime if d.exists() else 0) for _, d in FONT_DIRS)
    if _font_index["stamp"] == stamp:
        return _font_index["list"]
    out = []
    for group, d in FONT_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                fam, sty = R.ImageFont.truetype(str(f), 12).getname()
            except Exception:
                continue
            rel = str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f)
            out.append({"path": rel, "family": fam, "style": sty or "Regular", "group": group})
    out.sort(key=lambda x: (["Project", "Yours", "Mac"].index(x["group"]), x["family"].lower(), x["style"]))
    _font_index.update(stamp=stamp, list=out)
    return out


def get_google_font(family, weight=400, italic=False):
    fam = family.strip()
    q = f"{fam.replace(' ', '+')}:{'ital,' if italic else ''}wght@{'1,' if italic else ''}{int(weight)}"
    req = urllib.request.Request(f"https://fonts.googleapis.com/css2?family={q}", headers={"User-Agent": ""})
    css = urllib.request.urlopen(req, timeout=20).read().decode()
    m = re.search(r"url\((https://[^)]+\.ttf)\)", css)
    if not m:
        raise ValueError("no ttf for that font / weight")
    names = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
    out = FONTS / f"{fam.replace(' ', '')}-{names.get(int(weight), str(weight))}{'Italic' if italic else ''}.ttf"
    FONTS.mkdir(exist_ok=True)
    out.write_bytes(urllib.request.urlopen(m.group(1), timeout=30).read())
    _font_index["stamp"] = None
    return str(out.relative_to(ROOT))


# ---------- projects ----------
def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "clip"
    out, n = s, 2
    while (PROJECTS / out).exists():
        out, n = f"{s}-{n}", n + 1
    return out


def pdir(slug):
    d = (PROJECTS / slug).resolve()
    if d.parent != PROJECTS.resolve() or slug.startswith(".") or not (d / "project.json").exists():
        raise KeyError(slug)
    return d


def meta_of(slug):
    return json.loads((pdir(slug) / "project.json").read_text())


def write_meta(slug, m):
    m["updated"] = time.time()
    (pdir(slug) / "project.json").write_text(json.dumps(m, indent=2))


def data_path(slug):
    return pdir(slug) / "captions_edit.json"


def probe(path):
    return tools.probe(path)


def summary(slug):
    d = PROJECTS / slug
    m = json.loads((d / "project.json").read_text())
    data = d / "captions_edit.json"
    lines = spk = flagged = 0
    if data.exists():
        try:
            j = json.loads(data.read_text())
            lines, spk = len(j.get("lines", [])), len(j.get("speakers", {}))
            flagged = sum(1 for l in j.get("lines", []) if l.get("review"))
        except Exception:
            pass
    job = {k: v for k, v in jobs.get(slug, {}).items() if k != "tail"}
    status = m.get("status", "ready")
    if status == "processing" and not (job and job.get("state") == "running"):
        status = "error" if not data.exists() else "ready"  # app was closed mid run
    return {"slug": slug, "name": m.get("name", slug), "video": m.get("video"), "video_ok": Path(m.get("video", "")).exists(),
            "duration": m.get("duration"), "created": m.get("created"), "updated": max(m.get("updated", 0), data.stat().st_mtime if data.exists() else 0),
            "status": status, "error": m.get("error", ""), "lines": lines, "speakers": spk, "flagged": flagged,
            "has_captions": data.exists(), "clip_name": m.get("clip_name"), "source": m.get("source", "file"), "timeline": m.get("timeline"), "job": job, "settings": m.get("settings", {})}


def list_projects():
    PROJECTS.mkdir(exist_ok=True)
    out = []
    for d in PROJECTS.iterdir():
        if d.is_dir() and not d.name.startswith(".") and (d / "project.json").exists():
            try:
                out.append(summary(d.name))
            except Exception as e:
                out.append({"slug": d.name, "name": d.name, "status": "error", "error": str(e), "updated": 0})
    return sorted(out, key=lambda p: -(p.get("updated") or 0))


def create_project(body):
    settings = {**DEFAULT_SETTINGS, **{k: body[k] for k in DEFAULT_SETTINGS if k in body}}
    if body.get("source") == "resolve":
        # whole timeline: the pipeline renders a copy of the edit out of Resolve first
        tl = body.get("timeline") or "timeline"
        name = (body.get("name") or tl).strip()
        slug = slugify(name)
        d = PROJECTS / slug
        (d / "work").mkdir(parents=True)
        m = {"name": name, "source": "resolve", "timeline": body.get("timeline"), "resolve_project": body.get("resolve_project"),
             "video": str(d / "work" / "timeline.mp4"), "clip_name": re.sub(r"\W+", "_", slug) + "_captions",
             "fps": body.get("fps", 30), "duration": body.get("duration"), "created": time.time(), "status": "processing", "settings": settings}
        (d / "project.json").write_text(json.dumps(m, indent=2))
        start_pipeline(slug, "resolve")
        return slug
    video = (body.get("video") or "").strip()
    if not video or not Path(video).exists():
        raise ValueError("pick a video first")
    info = probe(video)
    name = (body.get("name") or info["name"]).strip()
    slug = slugify(name)
    d = PROJECTS / slug
    (d / "work").mkdir(parents=True)
    if Path(video).resolve().parent == (PROJECTS / ".incoming").resolve():
        # a timeline render from the Resolve menu, it belongs to this project now
        dst = d / "work" / "timeline.mp4"
        shutil.move(video, dst)
        video = str(dst)
    m = {"name": name, "video": video, "clip_name": re.sub(r"\W+", "_", slug) + "_captions", "fps": info["fps"],
         "duration": info["duration"], "width": info["width"], "height": info["height"], "created": time.time(),
         "status": "processing", "settings": settings}
    (d / "project.json").write_text(json.dumps(m, indent=2))
    start_pipeline(slug, "probe")
    return slug


def last_render():
    # newest caption render across projects, for Workspace > Scripts > Subtitles - Place Captions
    best = {}
    for d in PROJECTS.iterdir() if PROJECTS.exists() else []:
        try:
            m = json.loads((d / "project.json").read_text())
        except Exception:
            continue
        lr = m.get("last_render") or {}
        if lr.get("file") and Path(lr["file"]).exists() and lr.get("at", 0) > best.get("at", 0):
            best = {"at": lr["at"], "file": lr["file"], "name": m.get("name", d.name), "slug": d.name,
                    "clip_name": m.get("clip_name") or d.name + "_captions"}
    return best


def recent_videos():
    out = []
    for base in [Path.home() / "Downloads", Path.home() / "Movies", Path.home() / "Desktop"]:
        try:
            for f in base.glob("*"):
                if f.suffix.lower() in VIDEO_EXT and f.is_file() and "_captions" not in f.name:
                    out.append(f)
            for f in base.glob("*/*"):
                if f.suffix.lower() in VIDEO_EXT and f.is_file() and "_captions" not in f.name and "DaVinci" not in str(f):
                    out.append(f)
        except Exception:
            continue
    out = sorted(set(out), key=lambda f: -f.stat().st_mtime)[:10]
    used = {json.loads((p / "project.json").read_text()).get("video") for p in PROJECTS.glob("*") if (p / "project.json").exists()}
    return [{"path": str(f), "name": f.name, "mtime": f.stat().st_mtime, "size_mb": round(f.stat().st_size / 1e6, 1), "used": str(f) in used} for f in out]


def pick_file():
    # native mac file chooser. system events puts it in front of the browser
    script = ('tell application "System Events"\nactivate\nset f to choose file with prompt "Pick the video to caption" '
              'of type {"public.movie", "public.mpeg-4", "com.apple.quicktime-movie"}\nend tell\nreturn POSIX path of f')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=900)
    return r.stdout.strip() if r.returncode == 0 else ""


def thumb(slug):
    # poster for the project grid: a frame from the first caption line with the captions drawn on,
    # so the grid shows what each project actually looks like. rebuilt when the captions change
    d = pdir(slug)
    out = d / "work" / "poster.jpg"
    m = meta_of(slug)
    video = m.get("video", "")
    if not Path(video).exists():
        return out.read_bytes() if out.exists() else None
    dp = d / "captions_edit.json"
    if out.exists() and (not dp.exists() or out.stat().st_mtime >= dp.stat().st_mtime):
        return out.read_bytes()
    (d / "work").mkdir(exist_ok=True)
    t = min(1.5, (m.get("duration") or 3) / 3)
    data = None
    if dp.exists():
        try:
            data = json.loads(dp.read_text())
            shown = [ln for ln in data.get("lines", []) if not ln.get("hidden")]
            if shown:
                t = shown[0]["start"] + (shown[0]["end"] - shown[0]["start"]) * 0.7
        except Exception:
            data = None
    try:
        im = R.video_frame(video, t)
        if data:
            cap = R.Renderer(data).frame(t)
            if cap:
                im.alpha_composite(cap)
        im.convert("RGB").resize((360, 640), R.Image.LANCZOS).save(out, "JPEG", quality=82)
    except Exception as e:
        print("poster failed for", slug, e)
    return out.read_bytes() if out.exists() else None


# ---------- jobs (transcribe pipeline + renders), one at a time per project ----------
jobs = {}
procs = {}


def busy(slug):
    return jobs.get(slug, {}).get("state") == "running"


def start_pipeline(slug, start):
    if busy(slug):
        raise ValueError("this project is already working on something")
    m = meta_of(slug)
    m["status"], m["error"] = "processing", ""
    write_meta(slug, m)
    jobs[slug] = {"kind": "pipeline", "state": "running", "pct": 0, "msg": "starting", "started": time.time(), "tail": []}
    threading.Thread(target=run_pipeline, args=(slug, start), daemon=True).start()


def run_pipeline(slug, start):
    j = jobs[slug]
    d = pdir(slug)
    p = subprocess.Popen([PY, "-m", "subtitles.pipeline", str(d), "--from", start], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    procs[slug] = p
    err = ""
    for line in p.stdout:
        line = line.rstrip()
        if line.startswith("@prog "):
            _, pct, *msg = line.split(" ", 2)
            j["pct"], j["msg"] = float(pct), (msg[0] if msg else "")
        elif line.startswith("@error "):
            err = line[7:]
        elif line and not line.startswith("@"):
            j["tail"] = (j["tail"] + [line])[-12:]
    p.wait()
    procs.pop(slug, None)
    _peaks.pop(slug, None)
    if j.get("cancelled"):
        m = meta_of(slug)
        m["status"], m["error"] = ("ready" if data_path(slug).exists() else "error"), "cancelled"
        write_meta(slug, m)
        j.update(state="cancelled", msg="cancelled")
    elif p.returncode == 0 and not err:
        j.update(state="done", pct=1, msg=f"done in {int(time.time() - j['started'])}s")
        schedule_sync("new captions " + slug)
    else:
        if not err:
            err = " | ".join(j["tail"][-3:]) or f"exit {p.returncode}"
        j.update(state="error", msg=err)
        m = meta_of(slug)
        if m.get("status") != "error":
            m["status"], m["error"] = "error", err
            write_meta(slug, m)


def start_render(slug):
    if busy(slug):
        raise ValueError("already rendering" if jobs[slug]["kind"] == "render" else "still transcribing")
    jobs[slug] = {"kind": "render", "state": "running", "pct": 0, "msg": "rendering", "started": time.time(), "tail": []}
    threading.Thread(target=run_render, args=(slug,), daemon=True).start()


def run_render(slug):
    j = jobs[slug]
    d = pdir(slug)
    m = meta_of(slug)
    name = m.get("clip_name") or slug + "_captions"
    rd = d / "renders"
    rd.mkdir(exist_ok=True)
    mov = rd / f"{name}_{time.strftime('%m%d_%H%M%S')}.mov"
    p = subprocess.Popen([PY, "-m", "subtitles.render", "--data", str(d / "captions_edit.json"), str(mov)], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    procs[slug] = p
    tail = []
    for line in p.stdout:
        tail = (tail + [line.strip()])[-8:]
        mm = re.match(r"(\d+)/(\d+)", line.strip())
        if mm:
            j["pct"] = int(mm.group(1)) / int(mm.group(2))
            j["msg"] = f"{int(j['pct'] * 100)}%"
    p.wait()
    procs.pop(slug, None)
    if p.returncode != 0 or not mov.exists():
        j.update(state="error", msg="render failed: " + " | ".join(tail[-3:]))
        return
    shutil.copyfile(mov, d / f"{name}.mov")
    j["msg"] = "sending to Resolve"
    res = RB.place(mov, name)
    for old in sorted(rd.glob(f"{name}_*.mov"), key=lambda f: f.stat().st_mtime)[:-KEEP_RENDERS]:
        try:
            old.unlink()
        except OSError:
            pass
    m = meta_of(slug)
    m["last_render"] = {"at": time.time(), "file": str(mov), "resolve": res.get("msg", "")}
    write_meta(slug, m)
    hint = "" if res.get("ok") else f". The file is in {d.name}/renders, or use Workspace > Scripts > Subtitles - Place Captions in Resolve"
    j.update(state="done", pct=1, out=str(mov), msg=f"done in {int(time.time() - j['started'])}s, " + (res.get("msg") or "") + hint)


_peaks = {}


def peaks(slug):
    if slug in _peaks:
        return _peaks[slug]
    import numpy as np
    import soundfile as sf
    d = pdir(slug)
    wav = d / "work" / "audio16k.wav"
    if not wav.exists():
        (d / "work").mkdir(exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", meta_of(slug)["video"], "-ac", "1", "-ar", "16000", str(wav)], check=True)
    a, sr = sf.read(str(wav), dtype="float32")
    if a.ndim > 1:
        a = a.mean(1)
    hop = sr // 100
    n = len(a) // hop
    p = np.abs(a[:n * hop].reshape(n, hop)).max(1)
    p = np.sqrt(p / (p.max() + 1e-9))  # sqrt so quiet people still show up
    _peaks[slug] = json.dumps({"rate": 100, "peaks": (p * 255).astype(int).tolist()})
    return _peaks[slug]


def relink(slug, path):
    if not Path(path).exists():
        raise ValueError("that file doesnt exist")
    info = probe(path)
    m = meta_of(slug)
    m["video"] = path
    write_meta(slug, m)
    dp = data_path(slug)
    if dp.exists():
        d = json.loads(dp.read_text())
        d["video"] = path
        dp.write_text(json.dumps(d, indent=2))
    (pdir(slug) / "work" / "poster.jpg").unlink(missing_ok=True)
    return info


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def trusted(self):
        # only localhost pages get to talk to this. stops a random website from posting to the
        # app (cross site requests) or reading it through a rebound dns name
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False
        return True

    def send(self, code, body, ctype="application/json", extra=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def j(self, obj, code=200):
        self.send(code, json.dumps(obj))

    def redirect(self, to):
        self.send(302, b"", "text/plain", {"Location": to})

    # ---- GET ----
    def do_GET(self):
        if not self.trusted():
            return self.send(403, "forbidden", "text/plain")
        u = urlparse(self.path)
        path, q = unquote(u.path), parse_qs(u.query)
        try:
            if path in ("/", "/index.html"):
                return self.send(200, (WEB / "home.html").read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/ui/fonts/") and path.endswith(".woff2"):
                f = WEB / "fonts" / Path(path).name
                if f.exists():
                    return self.send(200, f.read_bytes(), "font/woff2")
            if path in ("/app.css", "/favicon.svg"):
                ctype = "text/css" if path.endswith(".css") else "image/svg+xml"
                return self.send(200, (WEB / path[1:]).read_bytes(), ctype + "; charset=utf-8")
            if path == "/mtime":  # the mac launcher pings this to see if the app is up
                return self.j({"ok": True, "sync": sync, "git": git_ok()})
            if path == "/api/projects":
                return self.j({"projects": list_projects(), "sync": sync, "git": git_ok()})
            if path == "/api/paths":
                # where the Resolve menu scripts drop timeline renders before a project picks them up
                (PROJECTS / ".incoming").mkdir(parents=True, exist_ok=True)
                return self.j({"projects": str(PROJECTS), "incoming": str(PROJECTS / ".incoming")})
            if path == "/api/last-render":
                return self.j(last_render())
            if path == "/api/recent":
                return self.j(recent_videos())
            if path == "/defaults":
                return self.j(R.DEFAULTS)
            if path == "/fonts":
                return self.j(font_index())
            if path == "/fontfile":
                want = q.get("p", [""])[0]
                if any(f["path"] == want for f in font_index()):
                    return self.send(200, R.font_path(want).read_bytes(), "font/otf" if want.lower().endswith(".otf") else "font/ttf")
                return self.send(404, "nope", "text/plain")
            if path == "/presets":
                PRESETS.mkdir(exist_ok=True)
                return self.j({p.stem: json.loads(p.read_text()) for p in sorted(PRESETS.glob("*.json"))})
            if path.startswith("/fonts/"):
                f = FONTS / Path(path).name
                if f.exists():
                    return self.send(200, f.read_bytes(), "font/ttf")
            m = re.match(r"^/p/([^/]+)(/.*)?$", path)
            if m:
                return self.project_get(m.group(1), m.group(2) or "", q)
        except KeyError:
            return self.j({"ok": False, "msg": "no such project"}, 404)
        except Exception as e:
            return self.j({"ok": False, "msg": f"{type(e).__name__}: {e}"}, 500)
        self.send(404, "nope", "text/plain")

    def project_get(self, slug, sub, q):
        d = pdir(slug)
        dp = d / "captions_edit.json"
        if sub == "":
            return self.redirect(f"/p/{quote(slug)}/")
        if sub == "/":
            if not dp.exists():
                return self.redirect(f"/#{quote(slug)}")
            return self.send(200, (WEB / "editor.html").read_bytes(), "text/html; charset=utf-8")
        if sub == "/data":
            dd = json.loads(dp.read_text())
            dd["style"] = R.migrate(dd.get("style"))
            dd["_mtime"] = dp.stat().st_mtime
            return self.j(dd)
        if sub == "/meta":
            return self.j({**summary(slug), "meta": meta_of(slug)})
        if sub == "/mtime":
            return self.j({"mtime": dp.stat().st_mtime if dp.exists() else 0, "sync": sync, "git": git_ok()})
        if sub == "/status":
            return self.j({k: v for k, v in jobs.get(slug, {"state": "idle", "msg": ""}).items()})
        if sub == "/video":
            v = Path(meta_of(slug)["video"])
            if not v.exists():
                return self.send(404, "video missing", "text/plain")
            return self.stream(v)
        if sub == "/peaks":
            return self.send(200, peaks(slug))
        if sub == "/thumb.jpg":
            b = thumb(slug)
            return self.send(200, b, "image/jpeg") if b else self.send(404, "no thumb", "text/plain")
        self.send(404, "nope", "text/plain")

    def stream(self, f):
        # range support so the video element can seek
        size = f.stat().st_size
        rng = self.headers.get("Range")
        a, b = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m.group(1):
                a = int(m.group(1))
            if m.group(2):
                b = int(m.group(2))
        b = min(b, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", mimetypes.guess_type(str(f))[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(b - a + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        with open(f, "rb") as fh:
            fh.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = fh.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    # ---- POST ----
    def do_POST(self):
        if not self.trusted():
            return self.send(403, "forbidden", "text/plain")
        path = unquote(urlparse(self.path).path)
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        body = json.loads(raw) if raw else {}
        try:
            m = re.match(r"^/p/([^/]+)(/.*)$", path)
            if m:
                return self.project_post(m.group(1), m.group(2), body)
            if path == "/api/pick-video":
                p = pick_file()
                return self.j({"ok": bool(p), "info": probe(p) if p else None})
            if path == "/api/probe":
                p = os.path.expanduser(body.get("path", "").strip().strip("'\""))
                if not Path(p).is_file():
                    return self.j({"ok": False, "msg": "no file at that path"}, 400)
                return self.j({"ok": True, "info": probe(p)})
            if path == "/api/resolve/timeline":
                res = RB.timeline_videos()
                if res.get("ok"):
                    infos = []
                    for p in res["files"][:8]:
                        try:
                            infos.append(probe(p))
                        except Exception:
                            pass
                    res["infos"] = infos
                return self.j(res)
            if path == "/api/projects":
                return self.j({"ok": True, "slug": create_project(body)})
            if path == "/quit":
                running = [s for s in jobs if busy(s)]
                if running and not body.get("force"):
                    return self.j({"ok": False, "msg": "still working on: " + ", ".join(running)}, 409)

                def bye():
                    time.sleep(0.4)
                    for p in list(procs.values()):
                        p.terminate()
                    if _sync_timer["t"] and sync["state"] == "pending":
                        _sync_timer["t"].cancel()
                        try:
                            run_sync("captions edit")
                        except Exception:
                            pass
                    print("editor closed")
                    os._exit(0)

                threading.Thread(target=bye, daemon=True).start()
                return self.j({"ok": True})
            if path == "/sync":
                threading.Thread(target=run_sync, args=(body.get("msg") or "manual sync",), daemon=True).start()
                return self.j({"ok": True})
            if path == "/gfont":
                p = get_google_font(body["family"], body.get("weight", 400), body.get("italic", False))
                schedule_sync("font " + body["family"])
                return self.j({"ok": True, "path": p})
            if path == "/presets":
                PRESETS.mkdir(exist_ok=True)
                name = re.sub(r"[^\w \-]", "", body["name"]).strip() or "preset"
                (PRESETS / f"{name}.json").write_text(json.dumps(body["style"], indent=2))
                schedule_sync("preset " + name)
                return self.j({"ok": True, "name": name})
            if path == "/presets/delete":
                f = PRESETS / (re.sub(r"[^\w \-]", "", body["name"]).strip() + ".json")
                if f.exists():
                    f.unlink()
                return self.j({"ok": True})
        except KeyError:
            return self.j({"ok": False, "msg": "no such project"}, 404)
        except Exception as e:
            return self.j({"ok": False, "msg": f"{type(e).__name__}: {e}"}, 500)
        self.send(404, "nope", "text/plain")

    def project_post(self, slug, sub, body):
        d = pdir(slug)
        dp = d / "captions_edit.json"
        hist = d / ".history"
        if sub == "/save":
            hist.mkdir(exist_ok=True)
            base = body.pop("_base", None)
            force = body.pop("_force", False)
            body.pop("_mtime", None)
            cur = dp.stat().st_mtime if dp.exists() else None
            if cur is not None and not force and (base is None or abs(base - cur) > 1e-6):
                # file changed since this tab loaded it (another tab, a rebuild, a script). keep their copy, dont overwrite
                keep = hist / f"conflict_{time.strftime('%Y%m%d_%H%M%S')}.json"
                keep.write_text(json.dumps(body, indent=2))
                msg = "the captions changed outside this tab" if base is not None else "editor was updated, refresh the page"
                return self.j({"ok": False, "conflict": True, "msg": f"{msg} (your version is in .history/{keep.name})"}, 409)
            if dp.exists():
                shutil.copyfile(dp, hist / f"captions_edit_{time.strftime('%Y%m%d_%H%M%S')}.json")
                for old in sorted(hist.glob("captions_edit_*.json"))[:-200]:
                    old.unlink()
            dp.write_text(json.dumps(body, indent=2))
            mm = meta_of(slug)
            write_meta(slug, mm)
            schedule_sync(f"captions edit {slug}")
            return self.j({"ok": True, "mtime": dp.stat().st_mtime})
        if sub == "/frame":
            im = R.Renderer(body["data"]).frame(float(body["t"]))
            buf = io.BytesIO()
            (im or R.Image.new("RGBA", (R.W, R.H), (0, 0, 0, 0))).save(buf, "PNG", compress_level=1)
            return self.send(200, buf.getvalue(), "image/png")
        if sub == "/render":
            try:
                start_render(slug)
            except ValueError as e:
                return self.j({"ok": False, "msg": str(e)}, 409)
            return self.j({"ok": True})
        if sub == "/export":
            out, files = export(json.loads(dp.read_text()), body.get("fmt", "all"), d / "export")
            return self.j({"ok": True, "dir": out, "files": files})
        if sub == "/reveal":
            what = body.get("dir", "")
            target = d / what if what in ("export", "renders") else d
            if what == "video":
                subprocess.run(["open", "-R", meta_of(slug)["video"]])
            else:
                target.mkdir(exist_ok=True)
                subprocess.run(["open", str(target)])
            return self.j({"ok": True})
        if sub == "/rerun":
            # settings changes + rebuild. from: probe (everything), speakers, lines
            mm = meta_of(slug)
            mm["settings"] = {**DEFAULT_SETTINGS, **mm.get("settings", {}), **(body.get("settings") or {})}
            write_meta(slug, mm)
            start = body.get("from", "probe")
            if start != "probe" and not (d / "work" / "whisper.json").exists():
                start = "probe"
            if start == "speakers" and not (d / "work" / "audio16k.wav").exists():
                start = "audio"
            if mm.get("source") == "resolve" and not Path(mm.get("video", "")).exists():
                start = "probe"
            try:
                start_pipeline(slug, start)
            except ValueError as e:
                return self.j({"ok": False, "msg": str(e)}, 409)
            return self.j({"ok": True})
        if sub == "/cancel":
            jb = jobs.get(slug)
            if jb and jb.get("state") == "running" and slug in procs:
                jb["cancelled"] = True
                procs[slug].terminate()
            return self.j({"ok": True})
        if sub == "/meta":
            mm = meta_of(slug)
            for k in ("name", "clip_name"):
                if body.get(k):
                    mm[k] = re.sub(r"[^\w\-]", "_", body[k].strip()) if k == "clip_name" else body[k].strip()
            write_meta(slug, mm)
            return self.j({"ok": True})
        if sub == "/relink":
            p = body.get("path") or pick_file()
            if not p:
                return self.j({"ok": False, "msg": "cancelled"})
            return self.j({"ok": True, "info": relink(slug, os.path.expanduser(p))})
        if sub == "/duplicate":
            if busy(slug):
                return self.j({"ok": False, "msg": "wait for it to finish first"}, 409)
            mm = meta_of(slug)
            name = body.get("name") or mm["name"] + " copy"
            new = slugify(name)
            shutil.copytree(d, PROJECTS / new, ignore=shutil.ignore_patterns("renders", "export", ".history", "*.mov"))
            m2 = json.loads((PROJECTS / new / "project.json").read_text())
            m2.update(name=name, clip_name=re.sub(r"\W+", "_", new) + "_captions", created=time.time())
            m2.pop("last_render", None)
            (PROJECTS / new / "project.json").write_text(json.dumps(m2, indent=2))
            return self.j({"ok": True, "slug": new})
        if sub == "/delete":
            if busy(slug):
                return self.j({"ok": False, "msg": "wait for it to finish first"}, 409)
            TRASH.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(TRASH / f"{slug}_{time.strftime('%Y%m%d_%H%M%S')}"))
            jobs.pop(slug, None)
            schedule_sync(f"removed {slug}")
            return self.j({"ok": True})
        if sub == "/resolve":
            res = RB.status()
            return self.j(res)
        self.send(404, "nope", "text/plain")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    PROJECTS.mkdir(parents=True, exist_ok=True)
    pull_on_start()
    print(f"projects in {PROJECTS}")
    print(f"caption app on http://localhost:{port}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
