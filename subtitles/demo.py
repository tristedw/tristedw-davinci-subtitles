# demo.py
# builds a sample project so you can poke at the editor without whisper or the speaker models.
#   python -m subtitles.demo
# makes a 30s vertical test clip with ffmpeg and a short two person script with word timings.
import json
import re
import subprocess
import time

from . import tools
from .config import PROJECTS

SCRIPT = [
    ("a", "Okay so this is the new grinder."),
    ("b", "Wait, that's the one you've been talking about for months?"),
    ("a", "Three months. I'm not even exaggerating."),
    ("b", "What makes it so different from the old one?"),
    ("a", "The burrs are flat instead of conical, so the grind comes out way more even."),
    ("b", "And you can actually taste that?"),
    ("a", "Honestly? Pull two shots side by side and tell me you can't."),
    ("b", "Alright, you're on. Loser buys beans."),
]
SPEAKERS = {
    "a": {"name": "Maya", "color": "#7CC4FF", "show_name": True, "style": {}},
    "b": {"name": "Jordan", "color": "#FFC567", "show_name": True, "style": {}},
}


def timed_lines(script, start=0.8, wps=2.7, gap=0.45):
    t, out = start, []
    for i, (spk, text) in enumerate(script, 1):
        words = []
        for w in text.split():
            d = max(0.14, len(re.sub(r"\W", "", w)) / (wps * 4.2))
            words.append({"w": w, "start": round(t, 3), "end": round(t + d, 3), "src": "demo", "p": 0.97})
            t += d + 0.06
        out.append({"id": i, "start": words[0]["start"], "end": words[-1]["end"], "spk": spk, "text": text,
                    "words": words, "review": i == 5})
        t += gap
    return out, t


def make_video(path, dur):
    # slow moving gradient + a soft tone so the waveform has something in it
    tools.need("ffmpeg")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"gradients=s=540x960:c0=0x3b3a36:c1=0x6d5b47:c2=0x2c3a3a:n=3:speed=0.004:d={dur}",
        "-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:a=0.05",
        "-af", "volume='0.3+0.7*abs(sin(2*PI*t*0.9))':eval=frame",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-shortest", str(path)], check=True)


def main(name="demo"):
    d = PROJECTS / name
    if (d / "project.json").exists():
        print(f"{d} already exists, delete it first to rebuild")
        return d
    (d / "work").mkdir(parents=True, exist_ok=True)
    lines, end = timed_lines(SCRIPT)
    dur = round(end + 1.5, 2)
    video = d / "work" / "demo.mp4"
    make_video(video, dur)
    style = {"font": "fonts/Poppins-SemiBold.ttf", "size": 54, "tag": "above", "word_mode": "highlight"}
    (d / "captions_edit.json").write_text(json.dumps(
        {"video": str(video), "fps": 30, "duration": dur, "speakers": SPEAKERS, "lines": lines, "style": style}, indent=2))
    (d / "project.json").write_text(json.dumps({
        "name": "Coffee grinder demo", "video": str(video), "clip_name": "demo_captions", "fps": 30, "duration": dur,
        "width": 540, "height": 960, "created": time.time(), "status": "ready",
        "settings": {"lang": "en", "speakers": 2, "names": "Maya, Jordan", "vocab": "", "quality": "fast", "quiet": False,
                     "preset": "", "max_chars": 42, "max_dur": 4.5}}, indent=2))
    print(f"demo project in {d}")
    return d


if __name__ == "__main__":
    main()
