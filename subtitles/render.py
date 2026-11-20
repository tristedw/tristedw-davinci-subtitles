# render.py
# caption overlay renderer, 1080x1920 with alpha
# the editor imports this for its exact preview, and it does the final prores 4444 render
#
#   python -m subtitles.render --data captions_edit.json out.mov
#   python -m subtitles.render --data captions_edit.json --still 55.6 out.png   (overlay on top of the video frame)
#
# style comes from captions_edit.json "style" (see DEFAULTS), speakers can override any key
# in speakers[x]["style"], and lines can carry bottom / x / scale.
# *words in asterisks* get the emphasis color.
import copy
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import tools  # noqa: F401  (homebrew ffmpeg on PATH)
from .config import ROOT

W, H = 1080, 1920

DEFAULTS = {
    # text
    "font": "fonts/Poppins-SemiBold.ttf",
    "size": 50,
    "text_color": "#FFFFFF",
    "transform": "none",  # none | upper | lower
    "letter_spacing": 0.0,
    "line_h": 1.28,
    "align": "center",  # center | left
    "max_text_w": 900,
    "balance": True,
    # outline + shadow
    "stroke_w": 0,
    "stroke_color": "#000000",
    "shadow_opacity": 0,
    "shadow_color": "#000000",
    "shadow_blur": 10,
    "shadow_x": 0,
    "shadow_y": 6,
    # box
    "box": True,
    "box_color": "#0C0C0E",
    "box_opacity": 185,
    "radius": 24,
    "pad_x": 34,
    "pad_y": 20,
    "box_full_width": False,
    # speaker color
    "speaker_color_use": "accent",  # accent | text | box | none
    "accent": "left",  # left | bottom | none
    "accent_w": 6,
    # name tag
    "tag": "above",  # above | inline | none
    "tag_font": "",  # empty = same as caption font
    "tag_size": 24,
    "tag_upper": True,
    "tag_spacing": 2.5,
    "tag_color": "speaker",  # speaker | text
    # position
    "anchor": "bottom",  # bottom | middle | top
    "bottom": 250,  # px up from the bottom edge (anchor bottom)
    "offset_top": 220,  # px down from the top edge (anchor top)
    "offset_middle": 0,  # px up from the center (anchor middle)
    "x_offset": 0,
    "stack_gap": 18,
    "max_stack": 2,
    # animation
    "anim_in": "rise",  # none | fade | rise | slide | pop
    "in_frames": 5,
    "rise": 10,
    "pop_from": 0.85,
    "anim_out": "fade",  # none | fade | drop
    "out_frames": 4,
    # words
    "word_mode": "none",  # none | dim | reveal | highlight
    "dim": 0.55,
    "highlight_color": "speaker",
    "highlight_scale": 1.0,
    "emph_color": "speaker",
    # timing, applied at render time only, saved times are left alone
    "offset": 0.0,
    "lead_in": 0.0,
    "linger": 0.0,
    "min_dur": 0.0,
    "close_gaps": 0.0,
    "cps_max": 20,
}


def migrate(style):
    # older captions_edit.json files used a few different keys
    s = dict(style or {})
    if "box_rgba" in s:
        r, g, b, a = s.pop("box_rgba")
        s.setdefault("box_color", "#%02X%02X%02X" % (r, g, b))
        s.setdefault("box_opacity", a)
    if "name_size" in s:
        s.setdefault("tag_size", s.pop("name_size"))
    if "name_font" in s:
        s.pop("name_font")
    if "line_h" in s and s["line_h"] > 3:
        s["line_h"] = 1.28
    if s.get("dim", 1.0) < 1.0 and "word_mode" not in s:
        s["word_mode"] = "dim"
    out = dict(DEFAULTS)
    out.update({k: v for k, v in s.items() if k in DEFAULTS})
    return out


def hex2rgb(h):
    h = (h or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def font_path(p):
    q = Path(p).expanduser()
    return q if q.is_absolute() else ROOT / q


@lru_cache(None)
def font(path, size):
    try:
        return ImageFont.truetype(str(font_path(path)), max(4, int(round(size))))
    except OSError:
        return ImageFont.truetype(str(ROOT / DEFAULTS["font"]), max(4, int(round(size))))


def tw(f, t, ls):
    if not ls:
        return f.getlength(t)
    return sum(f.getlength(c) for c in t) + ls * max(len(t) - 1, 0)


def draw_spaced(d, x, y, t, f, fill, ls, anchor="lm", **kw):
    if not ls:
        d.text((x, y), t, font=f, fill=fill, anchor=anchor, **kw)
        return
    if anchor == "mm":
        x -= tw(f, t, ls) / 2
    for c in t:
        d.text((x, y), c, font=f, fill=fill, anchor="lm", **kw)
        x += f.getlength(c) + ls


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_back(x):
    x = min(max(x, 0.0), 1.0)
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def timed(lines, S):
    # timing rules from the style, on copies
    vis = sorted([copy.deepcopy(l) for l in lines if not l.get("hidden")], key=lambda l: l["start"])
    off = float(S["offset"])
    for l in vis:
        l["words"] = [dict(w, start=w["start"] + off, end=w["end"] + off) for w in (l.get("words") or [])]
        l["start"] = l["start"] + off - float(S["lead_in"])
        l["end"] = l["end"] + off
    for i, l in enumerate(vis):
        nxt = next((m["start"] for m in vis[i + 1:] if m["spk"] == l["spk"]), None)
        cap = nxt - 0.04 if nxt is not None else 1e9
        end = l["end"]
        if S["min_dur"]:
            end = max(end, min(l["start"] + float(S["min_dur"]), cap))
        if S["linger"]:
            end = max(end, min(end + float(S["linger"]), cap))
        if S["close_gaps"] and nxt is not None and 0 < nxt - end <= float(S["close_gaps"]):
            end = nxt
        l["end"] = end
    return vis


def wrap(widths, space, maxw, balance):
    lines, cur, cw = [], [], 0
    for i, w in enumerate(widths):
        if cur and cw + space + w > maxw:
            lines.append(cur)
            cur, cw = [], 0
        cw = cw + (space if cur else 0) + w
        cur.append(i)
    if cur:
        lines.append(cur)
    n, N = len(lines), len(widths)
    if not balance or n < 2:
        return lines

    def lw(a, b):
        return sum(widths[a:b]) + space * (b - a - 1)

    @lru_cache(None)
    def best(a, k):
        # smallest possible widest row when widths[a:] is split into k rows
        if k == 1:
            w = lw(a, N)
            return (w, (N,)) if w <= maxw else (1e18, None)
        res = (1e18, None)
        for b in range(a + 1, N - k + 2):
            w = lw(a, b)
            if w > maxw:
                break
            m, br = best(b, k - 1)
            if br is not None and max(w, m) < res[0]:
                res = (max(w, m), (b,) + br)
        return res

    _, br = best(0, n)
    if br is None:
        return lines
    out, a = [], 0
    for b in br:
        out.append(list(range(a, b)))
        a = b
    return out


class Renderer:
    def __init__(self, data):
        self.data = data
        self.S = migrate(data.get("style"))
        self.fps = data.get("fps", 30)
        self.dur = data.get("duration", 117.7)
        self.spk = data.get("speakers", {})
        self.lines = timed(data.get("lines", []), self.S)
        for i, l in enumerate(self.lines):
            l["_k"] = i
        self.cache = {}

    def style_for(self, spk):
        sp = self.spk.get(spk, {})
        return {**self.S, **{k: v for k, v in (sp.get("style") or {}).items() if k in DEFAULTS and v not in (None, "")}}

    def tokens(self, L, St, sp):
        raw = L["text"].split()
        ws = L.get("words") or []
        if len(ws) != len(raw):
            n = sum(len(t) for t in raw) or 1
            acc, ws = 0, []
            for t in raw:
                ws.append({"start": L["start"] + (L["end"] - L["start"]) * acc / n})
                acc += len(t)
        toks, emph = [], False
        if St["tag"] == "inline" and sp.get("show_name") and sp.get("name"):
            name = sp["name"].upper() if St["tag_upper"] else sp["name"]
            toks.append({"t": name + ":", "start": -1e9, "tag": True, "emph": False})
        for t, w in zip(raw, ws, strict=True):
            if t.startswith("*"):
                emph = True
            this = emph
            if re.search(r"\*[^\w]*$", t):
                emph = False
            clean = t.replace("*", "")
            if not clean:
                continue
            if St["transform"] == "upper":
                clean = clean.upper()
            elif St["transform"] == "lower":
                clean = clean.lower()
            toks.append({"t": clean, "start": w["start"], "emph": this, "tag": False})
        return toks

    def block(self, L, t):
        """returns (image, margin, content_w, content_h) for one caption at time t"""
        St = self.style_for(L["spk"])
        sp = self.spk.get(L["spk"], {"name": "", "color": "#FFFFFF", "show_name": False})
        scol = hex2rgb(sp.get("color"))
        use = St["speaker_color_use"]
        tcol = scol if use == "text" else hex2rgb(St["text_color"])
        toks = self.tokens(L, St, sp)
        spoken = [tk for tk in toks if tk["start"] <= t + 0.02 and not tk["tag"]]
        active = spoken[-1] if spoken else None
        mode = St["word_mode"]
        key = (L["_k"], len(spoken) if mode != "none" else 0)
        if key in self.cache:
            return self.cache[key]

        f = font(St["font"], St["size"])
        ls = float(St["letter_spacing"])
        widths = [tw(f, tk["t"], ls) for tk in toks] or [0]
        space = f.getlength(" ") + ls
        rows = wrap(widths, space, St["max_text_w"], St["balance"]) if toks else [[]]
        LH = int(round(St["size"] * St["line_h"]))
        roww = [sum(widths[i] for i in r) + space * max(len(r) - 1, 0) for r in rows]
        tmax = max(roww) if roww else 0
        inner = max(tmax, St["max_text_w"]) if St["box_full_width"] else tmax
        acc_on = use == "accent" and St["accent"] != "none" and St["accent_w"] > 0
        acc_l = St["accent_w"] if acc_on and St["accent"] == "left" else 0
        acc_b = St["accent_w"] if acc_on and St["accent"] == "bottom" else 0
        bw = int(inner + St["pad_x"] * 2 + acc_l)
        bh = int(LH * len(rows) + St["pad_y"] * 2 + acc_b)
        show_tag = St["tag"] == "above" and sp.get("show_name") and sp.get("name")
        tf = font(St["tag_font"] or St["font"], St["tag_size"])
        tag_h = int(St["tag_size"] * 1.9) if show_tag else 0
        tag_txt = (sp.get("name", "").upper() if St["tag_upper"] else sp.get("name", "")) if show_tag else ""
        tag_w = tw(tf, tag_txt, St["tag_spacing"]) + 8 if show_tag else 0
        cw = max(bw, int(tag_w))
        ch = bh + tag_h
        hs = St["highlight_scale"] if mode == "highlight" else 1.0
        M = int(St["stroke_w"] + St["shadow_blur"] * 2 + abs(St["shadow_x"]) + abs(St["shadow_y"]) + St["size"] * (hs - 1) + 8)
        img = Image.new("RGBA", (cw + 2 * M, ch + 2 * M), (0, 0, 0, 0))
        bx0 = M + (cw - bw) // 2 if St["align"] == "center" else M
        by0 = M + tag_h

        # name tag above the box, lined up with the box's left edge
        if show_tag:
            tcolr = scol if St["tag_color"] == "speaker" else tcol
            d = ImageDraw.Draw(img)
            tag_x = bx0 + 4 if St["box"] else bx0 + acc_l + St["pad_x"]  # no box: line up with the text
            draw_spaced(d, tag_x, M + tag_h / 2, tag_txt, tf, tcolr + (255,), St["tag_spacing"],
                        stroke_width=int(St["stroke_w"] * St["tag_size"] / max(St["size"], 1)), stroke_fill=hex2rgb(St["stroke_color"]) + (255,))

        # box + accent
        if St["box"]:
            fill = scol if use == "box" else hex2rgb(St["box_color"])
            boxl = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(boxl).rounded_rectangle((bx0, by0, bx0 + bw - 1, by0 + bh - 1), radius=int(St["radius"]), fill=fill + (int(St["box_opacity"]),))
            img.alpha_composite(boxl)
        if acc_on:
            shape = Image.new("L", img.size, 0)
            ImageDraw.Draw(shape).rounded_rectangle((bx0, by0, bx0 + bw - 1, by0 + bh - 1), radius=int(St["radius"]), fill=255)
            bar = Image.new("L", img.size, 0)
            if acc_l:
                ImageDraw.Draw(bar).rectangle((bx0, by0, bx0 + acc_l - 1, by0 + bh), fill=255)
            else:
                ImageDraw.Draw(bar).rectangle((bx0, by0 + bh - acc_b, bx0 + bw, by0 + bh), fill=255)
            if not St["box"]:
                shape = Image.new("L", img.size, 255)
            img.alpha_composite(Image.composite(Image.new("RGBA", img.size, scol + (255,)), Image.new("RGBA", img.size, (0, 0, 0, 0)), ImageChops.multiply(bar, shape)))

        # text, drawn on its own layers so see through words dont punch holes in the box
        full = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dimL = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dF, dD = ImageDraw.Draw(full), ImageDraw.Draw(dimL)
        stroke = dict(stroke_width=int(St["stroke_w"]), stroke_fill=hex2rgb(St["stroke_color"]) + (255,))
        ecol = scol if St["emph_color"] == "speaker" else hex2rgb(St["emph_color"])
        hcol = scol if St["highlight_color"] == "speaker" else hex2rgb(St["highlight_color"])
        for ri, r in enumerate(rows):
            x = bx0 + acc_l + St["pad_x"] + ((inner - roww[ri]) / 2 if St["align"] == "center" else 0)
            y = by0 + St["pad_y"] + LH * ri + LH / 2
            for i in r:
                tk = toks[i]
                col = scol if tk["tag"] else (ecol if tk["emph"] else tcol)
                is_spoken = tk["tag"] or tk["start"] <= t + 0.02
                layer = dF
                if mode == "reveal" and not is_spoken:
                    x += widths[i] + space
                    continue
                if mode == "dim" and not is_spoken:
                    layer = dD
                if mode == "highlight" and tk is active:
                    col = hcol
                    if hs != 1.0:
                        draw_spaced(layer, x + widths[i] / 2, y, tk["t"], font(St["font"], St["size"] * hs), col + (255,), ls * hs, anchor="mm", **stroke)
                        x += widths[i] + space
                        continue
                draw_spaced(layer, x, y, tk["t"], f, col + (255,), ls, **stroke)
                x += widths[i] + space
        if mode == "dim":
            dimL.putalpha(dimL.getchannel("A").point(lambda v: int(v * St["dim"])))
            img.alpha_composite(dimL)
        img.alpha_composite(full)

        # drop shadow of the whole thing
        if St["shadow_opacity"] > 0:
            a = img.getchannel("A")
            if St["shadow_blur"] > 0:
                a = a.filter(ImageFilter.GaussianBlur(St["shadow_blur"]))
            a = a.point(lambda v: int(v * St["shadow_opacity"] / 255))
            sh = Image.new("RGBA", img.size, hex2rgb(St["shadow_color"]) + (0,))
            sh.putalpha(a)
            base = Image.new("RGBA", img.size, (0, 0, 0, 0))
            base.alpha_composite(sh, (int(St["shadow_x"]), int(St["shadow_y"])))
            base.alpha_composite(img)
            img = base
        res = (img, M, cw, ch)
        self.cache[key] = res
        return res

    def anim(self, L, t, St):
        fps = self.fps
        k = (t - L["start"]) * fps / St["in_frames"] if St["in_frames"] > 0 else 1.0
        e = ease(k)
        a = St["anim_in"]
        op, dy, sc = 1.0, 0.0, 1.0
        if a == "fade":
            op = e
        elif a == "rise":
            op, dy = e, St["rise"] * (1 - e)
        elif a == "slide":
            op, dy = min(1.0, e * 1.6), St["rise"] * 4 * (1 - e)
        elif a == "pop":
            op, sc = e, St["pop_from"] + (1 - St["pop_from"]) * ease_back(k)
        if t > L["end"] and St["anim_out"] != "none" and St["out_frames"] > 0:
            o = min(1.0, (t - L["end"]) * fps / St["out_frames"])
            op *= 1 - o
            if St["anim_out"] == "drop":
                dy += St["rise"] * o
        return op, dy, sc

    def active(self, t):
        out = []
        for L in self.lines:
            St = self.style_for(L["spk"])
            tail = St["out_frames"] / self.fps if St["anim_out"] != "none" else 0
            if L["start"] <= t < L["end"] + tail:
                out.append(L)
        return out

    def layout(self, t):
        """[(line, center_x, center_y, content_w, content_h)] for everything on screen at t, no animation"""
        S = self.S
        act = self.active(t)
        pinned = [L for L in act if L.get("bottom") is not None]
        stack = [L for L in act if L.get("bottom") is None][-int(S["max_stack"]):]
        out = []
        sizes = []
        for L in stack:
            _, _, cw, ch = self.block(L, t)
            sc = L.get("scale", 1.0)
            sizes.append((L, cw * sc, ch * sc))
        gap = S["stack_gap"]
        total = sum(h for _, _, h in sizes) + gap * max(len(sizes) - 1, 0)
        if S["anchor"] == "top":
            y = S["offset_top"]
        elif S["anchor"] == "middle":
            y = H / 2 - S["offset_middle"] - total / 2
        else:
            y = H - S["bottom"] - total
        for L, cw, ch in sizes:  # oldest on top, newest underneath
            out.append((L, W / 2 + S["x_offset"] + L.get("x", 0), y + ch / 2, cw, ch))
            y += ch + gap
        for L in pinned:
            _, _, cw, ch = self.block(L, t)
            sc = L.get("scale", 1.0)
            out.append((L, W / 2 + S["x_offset"] + L.get("x", 0), H - L["bottom"] - ch * sc / 2, cw * sc, ch * sc))
        return out

    def frame(self, t):
        items = self.layout(t)
        if not items:
            return None
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for L, cx, cy, _, _ in items:
            St = self.style_for(L["spk"])
            im, M, cw, ch = self.block(L, t)
            op, dy, sc = self.anim(L, t, St)
            sc *= L.get("scale", 1.0)
            if op <= 0.003:
                continue
            if abs(sc - 1) > 0.002:
                im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
            if op < 0.999:
                im = im.copy()
                im.putalpha(im.getchannel("A").point(lambda v, op=op: int(v * op)))
            canvas.alpha_composite(im, (int(round(cx - im.width / 2)), int(round(cy + dy - im.height / 2))))
        return canvas


def load(path):
    with open(path) as f:
        return json.load(f)


def video_frame(video, t):
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", video, "-frames:v", "1",
                        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
                        "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True, check=True)
    return Image.frombytes("RGBA", (W, H), r.stdout)


def main():
    args = sys.argv[1:]
    if "--data" not in args:
        sys.exit("usage: python -m subtitles.render --data captions_edit.json [out.mov | --still <sec> out.png]")
    i = args.index("--data")
    data = load(args[i + 1])
    del args[i:i + 2]
    R = Renderer(data)
    if args and args[0] == "--still":
        t, out = float(args[1]), args[2]
        bg = video_frame(data["video"], t)
        im = R.frame(t)
        if im:
            bg.alpha_composite(im)
        bg.convert("RGB").save(out)
        return
    OUT = args[0] if args else "captions.mov"
    n = int(round(R.dur * R.fps))
    blank = bytes(W * H * 4)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(R.fps), "-i", "-",
                           "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0", OUT], stdin=subprocess.PIPE)
    for fr in range(n):
        im = R.frame(fr / R.fps)
        ff.stdin.write(blank if im is None else im.tobytes())
        if fr % 600 == 0:
            print(f"{fr}/{n}", flush=True)
    ff.stdin.close()
    ff.wait()
    print("done", OUT)


if __name__ == "__main__":
    main()
