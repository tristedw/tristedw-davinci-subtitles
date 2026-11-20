# pipeline.py
# video -> captions_edit.json for one project, no hand steps
#   python -m subtitles.pipeline projects/<slug>                  everything
#   python -m subtitles.pipeline projects/<slug> --from lines     just rebuild lines from the cached whisper + diarization
#   python -m subtitles.pipeline projects/<slug> --from speakers  redo diarization + lines (keeps the transcript)
#
# settings come from projects/<slug>/project.json. progress goes to stdout as
#   @prog <0..1> <label>      @error <msg>      @done
# the editor server reads those.
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import tools
from .config import MODELS as MODELS_ROOT
from .config import PRESETS

MODELS = MODELS_ROOT / "sherpa"
WHISPER = {"best": "mlx-community/whisper-large-v3-mlx", "fast": "mlx-community/whisper-large-v3-turbo"}
PALETTE = ["#7CC4FF", "#FFC567", "#F49AC1", "#9ADBA6", "#C7A6FF", "#FF9F7A", "#7FE0D8", "#E6E68A"]
ORDER = ["resolve", "probe", "audio", "transcribe", "refine", "speakers", "quiet", "lines"]

WEIGHT = {"resolve": 15, "probe": 1, "audio": 3, "transcribe": 50, "refine": 10, "speakers": 14, "quiet": 18, "lines": 4}
LABEL = {"resolve": "Rendering the timeline in Resolve", "probe": "Reading video", "audio": "Pulling audio", "transcribe": "Transcribing", "refine": "Re-checking shaky bits",
         "speakers": "Working out who is talking", "quiet": "Listening for quiet voices", "lines": "Building caption lines"}

# set by setup() for the project being processed. module level so the steps stay short
PROJ = WORK = META = DATA = None
meta, cfg = {}, {}
START = "probe"
plan, total = list(ORDER), 1


def make_plan(start, settings, source="file"):
    # which steps run for these settings
    if start == "probe" and source == "resolve":
        start = "resolve"  # full redo of a timeline project re-renders the edit too
    steps = ORDER[ORDER.index(start):]
    if settings.get("quality", "best") != "best":
        steps = [s for s in steps if s != "refine"]
    if not settings.get("quiet"):
        steps = [s for s in steps if s != "quiet"]
    if int(settings.get("speakers") or 0) == 1:
        steps = [s for s in steps if s not in ("speakers", "quiet")]
    return start, steps


def setup(project_dir, start="probe"):
    global PROJ, WORK, META, DATA, meta, cfg, START, plan, total
    PROJ = Path(project_dir).resolve()
    WORK, META, DATA = PROJ / "work", PROJ / "project.json", PROJ / "captions_edit.json"
    meta = json.loads(META.read_text())
    cfg = meta.get("settings", {})
    START, plan = make_plan(start, cfg, meta.get("source", "file"))
    total = sum(WEIGHT[s] for s in plan) or 1


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


_last = {"p": -1, "label": ""}


def prog(step, frac=0.0, extra=""):
    done = sum(WEIGHT[s] for s in plan[:plan.index(step)])
    p = (done + WEIGHT[step] * min(max(frac, 0), 1)) / total
    label = LABEL[step] + (f" {extra}" if extra else "")
    if int(p * 100) != _last["p"] or label != _last["label"]:
        _last.update(p=int(p * 100), label=label)
        print(f"@prog {p:.3f} {label}", flush=True)


def save_meta(**kw):
    m = json.loads(META.read_text())
    m.update(kw)
    m["updated"] = time.time()
    META.write_text(json.dumps(m, indent=2))
    return m


def ff(*a):
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + r.stderr.strip()[-300:])


def whisper_bar(step):
    # mlx_whisper uses tqdm for its progress, swap in one that reports to us instead of the terminal
    try:
        import mlx_whisper.transcribe as T
        base = T.tqdm.tqdm

        class Bar(base):
            def __init__(self, *a, **k):
                k["file"] = open(os.devnull, "w")
                super().__init__(*a, **k)

            def update(self, n=1):
                super().update(n)
                if self.total:
                    prog(step, self.n / self.total, f"{int(self.n * 100 / self.total)}%")

        T.tqdm.tqdm = Bar
    except Exception:
        pass


def glue(words):
    # whisper splits y'all, $40,000 etc into bits, stick them back together
    out = []
    for w in words:
        t = w["w"]
        if out and (t.startswith("'") or re.match(r"^[,.]\d", t) or t in {",", ".", "?", "!"}):
            out[-1]["w"] += t
            out[-1]["end"] = w["end"]
            out[-1]["p"] = min(out[-1]["p"], w["p"])
        elif t:
            out.append(dict(w))
    return out


# ---------- steps ----------
def step_resolve():
    from . import resolve_bridge as RB
    prog("resolve", 0)
    WORK.mkdir(exist_ok=True)
    out = WORK / "timeline.mp4"
    out.unlink(missing_ok=True)
    res = RB.render_timeline(WORK, "timeline", meta.get("timeline"), lambda f: prog("resolve", f, f"{int(f * 100)}%"))
    if not res.get("ok"):
        raise RuntimeError(res.get("msg") or "Resolve render failed")
    if not out.exists():
        raise RuntimeError(f"Resolve said it rendered but {out} isnt there")
    (WORK / "poster.jpg").unlink(missing_ok=True)
    save_meta(video=str(out), timeline=res.get("timeline") or meta.get("timeline"))


def step_probe():
    prog("probe")
    video = meta["video"]
    if not Path(video).exists():
        raise RuntimeError(f"video not found: {video}")
    tools.need("ffmpeg")
    info = tools.probe(video)
    save_meta(fps=info["fps"], duration=info["duration"], width=info["width"], height=info["height"])


def step_audio():
    prog("audio")
    WORK.mkdir(exist_ok=True)
    ff("-i", meta["video"], "-vn", "-ac", "1", "-ar", "16000", str(WORK / "audio16k.wav"))
    prog("audio", 0.5)
    # loudness normed copy so far away people are easier to hear
    ff("-i", str(WORK / "audio16k.wav"), "-af", "highpass=f=70,dynaudnorm=f=150:g=15:p=0.9:m=20", str(WORK / "audio_norm.wav"))


def prompt_text():
    hint = (cfg.get("vocab") or "").strip()
    names = [n.strip() for n in (cfg.get("names") or "").split(",") if n.strip()]
    bits = [hint] + names
    return " ".join(b for b in bits if b) or None


def step_transcribe():
    import mlx_whisper
    prog("transcribe", 0, "(loading model)")
    whisper_bar("transcribe")
    lang = cfg.get("lang") or None
    r = mlx_whisper.transcribe(
        str(WORK / "audio16k.wav"), path_or_hf_repo=WHISPER.get(cfg.get("quality", "best"), WHISPER["best"]),
        language=lang if lang != "auto" else None, word_timestamps=True, condition_on_previous_text=False,
        initial_prompt=prompt_text(), temperature=(0.0, 0.2, 0.4, 0.6), compression_ratio_threshold=2.2,
        no_speech_threshold=0.5, hallucination_silence_threshold=1.0, verbose=False)
    segs = []
    for s in r["segments"]:
        ws = [{"w": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3), "p": round(w.get("probability", 0), 3)}
              for w in s.get("words", [])]
        if s["text"].strip() and ws:
            segs.append({"start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"].strip(), "words": ws})
    (WORK / "whisper.json").write_text(json.dumps({"language": r.get("language"), "segments": segs}, indent=1))
    if r.get("language"):
        cfg["lang_found"] = r["language"]


def step_refine():
    # redo segments that have really low confidence words, keep whichever pass is surer
    import mlx_whisper
    data = json.loads((WORK / "whisper.json").read_text())
    segs = data["segments"]
    bad = [i for i, s in enumerate(segs) if any(w["p"] < 0.1 for w in s["words"])]
    if not bad:
        return
    wav = str(WORK / "audio16k.wav")
    win = WORK / "_win.wav"
    out = list(segs)
    lang = cfg.get("lang") or cfg.get("lang_found") or None
    for n, i in enumerate(bad):
        prog("refine", n / len(bad), f"{n + 1}/{len(bad)}")
        s = segs[i]
        a = max(0.0, s["start"] - 2.0)
        ff("-ss", str(a), "-t", str(s["end"] - a + 2.0), "-i", wav, str(win))
        r = mlx_whisper.transcribe(str(win), path_or_hf_repo=WHISPER["best"], language=lang if lang != "auto" else None,
                                   word_timestamps=True, condition_on_previous_text=False, initial_prompt=prompt_text())
        new = []
        for t in r["segments"]:
            ws = t.get("words", [])
            mid = (t["start"] + t["end"]) / 2 + a
            if ws and s["start"] - 0.5 <= mid <= s["end"] + 0.5:
                new.append({"start": round(t["start"] + a, 3), "end": round(t["end"] + a, 3), "text": t["text"].strip(),
                            "words": [{"w": w["word"].strip(), "start": round(w["start"] + a, 3), "end": round(w["end"] + a, 3),
                                       "p": round(w["probability"], 3)} for w in ws]})
        if new and min(w["p"] for x in new for w in x["words"]) > min(w["p"] for w in s["words"]):
            out[out.index(s):out.index(s) + 1] = new
    win.unlink(missing_ok=True)
    data["segments"] = out
    (WORK / "whisper.json").write_text(json.dumps(data, indent=1))


def step_speakers():
    import numpy as np
    import sherpa_onnx
    import soundfile as sf
    prog("speakers", 0, "(loading)")
    seg_model = MODELS / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    emb_model = MODELS / "nemo_en_titanet_large.onnx"
    if not seg_model.exists() or not emb_model.exists():
        raise RuntimeError("speaker models missing in .models/sherpa, run scripts/setup.sh")
    n = int(cfg.get("speakers") or 0)
    sd = sherpa_onnx.OfflineSpeakerDiarization(sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg_model)), num_threads=4),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model), num_threads=4),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=n if n > 1 else -1, threshold=float(cfg.get("spk_threshold", 0.5))),
        min_duration_on=0.2, min_duration_off=0.3))
    wav, sr = sf.read(str(WORK / "audio16k.wav"), dtype="float32")

    def cb(done, total, arg=None):
        prog("speakers", done / max(total, 1))
        return 0

    try:
        res = sd.process(np.ascontiguousarray(wav), callback=cb)
    except TypeError:
        res = sd.process(np.ascontiguousarray(wav))
    out = [{"start": round(s.start, 3), "end": round(s.end, 3), "spk": int(s.speaker)} for s in res.sort_by_start_time()]
    (WORK / "diar.json").write_text(json.dumps(out, indent=1))


def step_quiet():
    # whisper again on each speaker turn of the normed audio, picks up people the full pass skipped
    import mlx_whisper
    import numpy as np
    import soundfile as sf
    turns = json.loads((WORK / "diar.json").read_text())
    wav, sr = sf.read(str(WORK / "audio_norm.wav"), dtype="float32")
    lang = cfg.get("lang") or cfg.get("lang_found") or None
    out = []
    for i, t in enumerate(turns):
        prog("quiet", i / max(len(turns), 1), f"{i + 1}/{len(turns)}")
        a = max(0.0, t["start"] - 0.25)
        b = min(len(wav) / sr, t["end"] + 0.25)
        clip = wav[int(a * sr):int(b * sr)]
        if len(clip) < sr * 0.3:
            clip = np.pad(clip, (0, int(sr * 0.3) - len(clip)))
        r = mlx_whisper.transcribe(clip, path_or_hf_repo=WHISPER["best"], language=lang if lang != "auto" else None, word_timestamps=True,
                                   condition_on_previous_text=False, initial_prompt=prompt_text(), no_speech_threshold=0.8,
                                   temperature=(0.0, 0.2, 0.4), verbose=None)
        ws = []
        for s in r["segments"]:
            for w in s.get("words", []):
                st, en = w["start"] + a, w["end"] + a
                if en > t["start"] - 0.1 and st < t["end"] + 0.1:
                    ws.append({"w": w["word"].strip(), "start": round(st, 3), "end": round(en, 3), "p": round(w["probability"], 3)})
        out.append({**t, "words": ws})
    (WORK / "turns.json").write_text(json.dumps(out, indent=1))


# ---------- lines ----------
def norm(w):
    return re.sub(r"\W", "", w).lower()


def build_words():
    full = glue([w for s in json.loads((WORK / "whisper.json").read_text())["segments"] for w in s["words"]])
    diar = json.loads((WORK / "diar.json").read_text()) if (WORK / "diar.json").exists() and int(cfg.get("speakers") or 0) != 1 else []
    for w in full:
        w["src"] = "whisper"

    extra = []
    tp = WORK / "turns.json"
    if cfg.get("quiet") and tp.exists() and diar:
        for t in json.loads(tp.read_text()):
            for w in glue(t["words"]):
                if w["p"] < 0.4 or len(norm(w["w"])) < 2:
                    continue
                hit = any(f["start"] - 0.15 < w["end"] and f["end"] + 0.15 > w["start"] for f in full)
                hit = hit or any(e["start"] < w["end"] and e["end"] > w["start"] for e in extra)
                echo = any(norm(f["w"]) == norm(w["w"]) for f in full if abs(f["start"] - w["start"]) < 1.5)
                prev = [f for f in full if f["end"] <= w["start"]]
                mid = bool(prev) and w["start"] - prev[-1]["end"] < 1.0 and not re.search(r"[.?!]$", prev[-1]["w"])
                if not (hit or echo or mid):
                    extra.append({**w, "src": "quiet", "_spk": t["spk"]})
    words = sorted(full + extra, key=lambda w: w["start"])

    # speaker per word = the diarized turn it overlaps most
    for w in words:
        if "_spk" in w:
            w["spk"], w["sure"] = w.pop("_spk"), 1.0
            continue
        ov = {}
        for s in diar:
            o = min(w["end"], s["end"]) - max(w["start"], s["start"])
            if o > 0:
                ov[s["spk"]] = ov.get(s["spk"], 0) + o
        if ov:
            best = max(ov, key=ov.get)
            w["spk"], w["sure"] = best, ov[best] / sum(ov.values())
        elif diar:
            mid = (w["start"] + w["end"]) / 2
            near = min(diar, key=lambda s: min(abs(s["start"] - mid), abs(s["end"] - mid)))
            w["spk"], w["sure"] = near["spk"], 0.4
        else:
            w["spk"], w["sure"] = 0, 1.0
    return words


def vote(ws):
    v = {}
    for w in ws:
        v[w["spk"]] = v.get(w["spk"], 0) + (w["end"] - w["start"] + 0.05) * w["sure"]
    best = max(v, key=v.get)
    return best, v[best] / (sum(v.values()) or 1)


def phrases(words):
    # whisper's punctuation and pauses are better at finding sentence edges than diarization is,
    # so cut into sentences first and give each one a single speaker by vote
    out, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > 0.5 or w["src"] != cur[-1]["src"]):
            out.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w["w"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)

    res = []

    def place(ph, depth=0):
        spk, share = vote(ph)
        # two people inside one whisper sentence: only split where diarization is clearly sure on both sides
        best = None
        if depth < 2 and len(ph) >= 4:
            for k in range(2, len(ph) - 1):
                if ph[k]["start"] - ph[k - 1]["end"] < 0.08:
                    continue
                a, sa = vote(ph[:k])
                b, sb = vote(ph[k:])
                if a != b and sa > 0.75 and sb > 0.75 and (best is None or sa + sb > best[0]):
                    best = (sa + sb, k)
        if best:
            place(ph[:best[1]], depth + 1)
            place(ph[best[1]:], depth + 1)
            return
        res.append({"spk": spk, "sure": share, "words": ph})

    for ph in out:
        place(ph)
    return res


def chunk(ws, maxc):
    # split one speaker's run into even-ish lines, nudged toward commas, so no one word orphans
    text = " ".join(w["w"] for w in ws)
    if len(text) <= maxc or len(ws) < 2:
        return [ws]
    n = -(-len(text) // maxc)
    target = len(text) / n
    out, cur, acc = [], [], 0
    for i, w in enumerate(ws):
        cur.append(w)
        acc += len(w["w"]) + 1
        left = len(ws) - i - 1
        if len(out) < n - 1 and left:
            comma = re.search(r"[,;:]$", w["w"]) and acc >= target * 0.6
            if acc >= target or comma:
                out.append(cur)
                cur, acc = [], 0
    if cur:
        out.append(cur)
    return out


def split_lines(words):
    maxc = int(cfg.get("max_chars", 42))
    maxd = float(cfg.get("max_dur", 4.5))
    phs = phrases(words)
    # a sentence cut in two by a pause goes back together if it fits. finished sentences stay their own line,
    # that's also where a missed speaker change usually hides
    runs = []
    for ph in phs:
        if runs:
            r = runs[-1]
            gap = ph["words"][0]["start"] - r["words"][-1]["end"]
            if r["spk"] == ph["spk"] and gap < 0.8 and not re.search(r"[.?!]$", r["words"][-1]["w"]) and len(" ".join(w["w"] for w in r["words"] + ph["words"])) <= maxc \
                    and ph["words"][-1]["end"] - r["words"][0]["start"] <= maxd:
                r["words"] += ph["words"]
                r["sure"] = min(r["sure"], ph["sure"])
                continue
        runs.append({"spk": ph["spk"], "sure": ph["sure"], "words": list(ph["words"])})
    lines = []
    for r in runs:
        for part in chunk(r["words"], maxc):
            # still too long on screen, halve it
            while part[-1]["end"] - part[0]["start"] > maxd and len(part) > 3:
                h = len(part) // 2
                lines.append({"spk": r["spk"], "sure": r["sure"], "words": part[:h]})
                part = part[h:]
            lines.append({"spk": r["spk"], "sure": r["sure"], "words": part})
    return lines


def step_lines():
    prog("lines")
    words = build_words()
    groups = split_lines(words)

    # speaker keys in order of who talks first
    order = []
    for g in groups:
        if g["spk"] not in order:
            order.append(g["spk"])
    names = [n.strip() for n in (cfg.get("names") or "").split(",") if n.strip()]
    old = {}
    if DATA.exists():
        try:
            old = json.loads(DATA.read_text())
        except Exception:
            old = {}
    keymap, speakers = {}, {}
    for i, s in enumerate(order):
        k = f"s{i + 1}"
        keymap[s] = k
        prev = (old.get("speakers") or {}).get(k)
        if prev and START in ("lines",):
            speakers[k] = prev  # rebuilding lines only, keep names / colors they already set
        else:
            speakers[k] = {"name": names[i] if i < len(names) else f"Speaker {i + 1}", "color": PALETTE[i % len(PALETTE)],
                           "show_name": len(order) > 1, "style": {}}

    out = []
    for g in groups:
        gw = g["words"]
        ws = [{"w": w["w"], "start": w["start"], "end": max(w["end"], w["start"] + 0.06), "src": w["src"], "p": w["p"]} for w in gw]
        review = any(w["p"] < 0.45 for w in gw) or g["sure"] < 0.6 or gw[0]["src"] == "quiet"
        out.append({"start": ws[0]["start"], "end": ws[-1]["end"], "spk": keymap[g["spk"]], "text": " ".join(w["w"] for w in gw),
                    "words": ws, "review": bool(review)})
    # long enough on screen to read, without running into the same person's next line
    for L in out:
        need = max(0.8, len(L["text"]) / 15.0)
        if L["end"] - L["start"] < need:
            nxt = min([M["start"] for M in out if M["start"] > L["start"] and M["spk"] == L["spk"]] + [L["start"] + need])
            L["end"] = round(max(L["end"], min(L["start"] + need, nxt - 0.05)), 3)
    for k, L in enumerate(out, 1):
        L["id"] = k

    m = json.loads(META.read_text())
    style = old.get("style")
    if not style:
        pre = cfg.get("preset")
        pf = PRESETS / f"{pre}.json"
        style = json.loads(pf.read_text()) if pre and pf.exists() else {}
    data = {"video": m["video"], "fps": m.get("fps", 30), "duration": m.get("duration", out[-1]["end"] if out else 1),
            "speakers": speakers, "lines": out, "style": style}
    if DATA.exists():
        hist = PROJ / ".history"
        hist.mkdir(exist_ok=True)
        shutil.copyfile(DATA, hist / f"captions_edit_{time.strftime('%Y%m%d_%H%M%S')}_before_rebuild.json")
    DATA.write_text(json.dumps(data, indent=2))
    print(f"lines {len(out)}  speakers {len(order)}  flagged {sum(L['review'] for L in out)}", flush=True)


STEPS = {"resolve": step_resolve, "probe": step_probe, "audio": step_audio, "transcribe": step_transcribe, "refine": step_refine,
         "speakers": step_speakers, "quiet": step_quiet, "lines": step_lines}

def main():
    global meta
    if len(sys.argv) < 2:
        sys.exit("usage: python -m subtitles.pipeline projects/<slug> [--from probe|audio|speakers|lines]")
    setup(sys.argv[1], arg("--from", "probe"))
    save_meta(status="processing", error="")
    try:
        for s in plan:
            meta = json.loads(META.read_text())
            STEPS[s]()
        if cfg.get("lang_found"):
            m = json.loads(META.read_text())
            m.setdefault("settings", {})["lang_found"] = cfg["lang_found"]
            META.write_text(json.dumps(m, indent=2))
        save_meta(status="ready")
        print("@prog 1 Done", flush=True)
        print("@done", flush=True)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        save_meta(status="error", error=msg)
        print(f"@error {msg}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
