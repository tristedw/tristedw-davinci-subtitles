# exporters.py
# captions_edit.json -> srt (combined, plain and per speaker), vtt, ass, plain transcript
import re

from . import render as R

FORMATS = ("srt", "vtt", "ass", "txt", "all")


def ts(t, sep=","):
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"


def ass_ts(t):
    # ass wants h:mm:ss.cc
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def ass_col(hexc, alpha=255):
    r, g, b = R.hex2rgb(hexc)
    return f"&H{255 - int(alpha):02X}{b:02X}{g:02X}{r:02X}"


def clean(t):
    return t.replace("*", "")


def slug(s):
    return re.sub(r"\W+", "_", s).strip("_").lower()


def srt(items, names=None):
    # names given = prefix each line with who is talking
    out = []
    for i, line in enumerate(items, 1):
        txt = clean(line["text"])
        if names is not None:
            txt = f"{names.get(line['spk'], line['spk'])}: {txt}"
        out.append(f"{i}\n{ts(line['start'])} --> {ts(line['end'])}\n{txt}\n")
    return "\n".join(out)


def vtt(lines, names):
    body = "".join(f"{ts(line['start'], '.')} --> {ts(line['end'], '.')}\n<v {names.get(line['spk'], line['spk'])}>{clean(line['text'])}\n\n"
                   for line in lines)
    return "WEBVTT\n\n" + body


def transcript(lines, names):
    rows, last = [], None
    for line in lines:
        if line["spk"] == last:
            rows[-1] += " " + clean(line["text"])
        else:
            rows.append(f"[{ts(line['start'])[3:8]}] {names.get(line['spk'], line['spk'])}: {clean(line['text'])}")
        last = line["spk"]
    return "\n".join(rows) + "\n"


def ass(data, lines, names, S):
    try:
        fam = R.font(S["font"], 20).getname()[0]
    except Exception:
        fam = "Arial"
    styles = []
    for k, sp in data["speakers"].items():
        St = {**S, **(sp.get("style") or {})}
        txt = sp.get("color", "#FFFFFF") if St["speaker_color_use"] == "text" else St["text_color"]
        boxed = St["box"]
        outline = ass_col(St["box_color"], St["box_opacity"]) if boxed else ass_col(St["stroke_color"])
        back = ass_col(St["box_color"], St["box_opacity"]) if boxed else ass_col(St["shadow_color"], St["shadow_opacity"])
        styles.append(f"Style: {k},{fam},{St['size']},{ass_col(txt)},{ass_col(txt)},{outline},{back},0,0,0,0,100,100,{St['letter_spacing']},0,"
                      f"{3 if boxed else 1},{St['pad_y'] // 2 if boxed else St['stroke_w']},{0 if boxed else (2 if St['shadow_opacity'] else 0)},2,60,60,{St['bottom']},1")
    ev = "".join(f"Dialogue: 0,{ass_ts(line['start'])},{ass_ts(line['end'])},{line['spk']},{names.get(line['spk'], '')},0,0,0,,{clean(line['text'])}\n"
                 for line in lines)
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            + "\n".join(styles) + "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + ev)


def export(data, fmt, out):
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt}, use one of {', '.join(FORMATS)}")
    S = R.migrate(data.get("style"))
    lines = R.timed(data["lines"], S)
    names = {k: v.get("name", k) for k, v in data["speakers"].items()}
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    if fmt in ("srt", "all"):
        files["all_speakers.srt"] = srt(lines, names)
        files["plain.srt"] = srt(lines)
        for k in data["speakers"]:
            mine = [line for line in lines if line["spk"] == k]
            if mine:
                files[f"{k}_{slug(names[k])}.srt"] = srt(mine)
    if fmt in ("vtt", "all"):
        files["captions.vtt"] = vtt(lines, names)
    if fmt in ("txt", "all"):
        files["transcript.txt"] = transcript(lines, names)
    if fmt in ("ass", "all"):
        files["captions.ass"] = ass(data, lines, names, S)
    for name, body in files.items():
        (out / name).write_text(body)
    return str(out), list(files)
