# tools.py
# finds ffmpeg / ffprobe even when the app was started from Finder (launchd PATH has no homebrew in it)
# and reads video info, falling back to parsing ffmpeg -i if ffprobe isnt around
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

for d in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".homebrew/bin"), str(Path.home() / "homebrew/bin")):
    if os.path.isdir(d) and d not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = d + ":" + os.environ.get("PATH", "")


def need(tool):
    p = shutil.which(tool)
    if not p:
        raise RuntimeError(f"{tool} not found. install it with: brew install ffmpeg")
    return p


def probe(path):
    path = str(path)
    if shutil.which("ffprobe"):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate:format=duration", "-of", "json", path],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise ValueError("cant read that file as video")
        j = json.loads(r.stdout or "{}")
        st = (j.get("streams") or [{}])[0]
        num, den = (st.get("r_frame_rate") or "30/1").split("/")
        dur = float(j.get("format", {}).get("duration", 0) or 0)
        fps = float(num) / float(den or 1)
        w, h = st.get("width"), st.get("height")
    else:
        # no ffprobe, ffmpeg prints the same info to stderr when given no output
        r = subprocess.run([need("ffmpeg"), "-hide_banner", "-i", path], capture_output=True, text=True, timeout=30)
        err = r.stderr
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
        if not m:
            raise ValueError("cant read that file as video")
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        v = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
        w, h = (int(v.group(1)), int(v.group(2))) if v else (None, None)
        f = re.search(r"([\d.]+) fps", err)
        fps = float(f.group(1)) if f else 30.0
    return {"path": path, "name": Path(path).stem, "duration": round(dur, 2), "fps": round(fps, 3) if fps > 0 else 30.0,
            "width": w, "height": h, "size_mb": round(Path(path).stat().st_size / 1e6, 1)}
