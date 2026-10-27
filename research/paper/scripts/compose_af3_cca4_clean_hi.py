"""Compose the four ABCD figures at high resolution.

Annotation: a four-corner focus frame around the modelled CCA/CAA bases,
with the sequence and residue range set directly beside it.  The only other
marks are the reference label, a dashed C3'-C3' measurement line, and the
distance in the top-right corner.

Reference = 5UD5 chain D (genuine mature 3'-CCA), matching metrics.json's
``d_3prime_to_A76``; the 1EHZ-based v3 panels are kept for traceability.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter, ImageOps

ST = Path("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019")
SRC = ST / "figures_abcd_pymol_5ud5"
OUT = ST / "figures_abcd"

GREEN, ORANGE, INK, GREY, DGREY = "#127a42", "#e28118", "#2c2f36", "#6a6f78", "#4a4f58"
D = "/usr/share/fonts/truetype/dejavu/DejaVuSans"
BOLD, REG = f"{D}-Bold.ttf", f"{D}.ttf"

REF_LABEL = "5UD5 D"
VERSION = "v4"

TILE = 2800
CTX = 700
W, H = 3120, 3160
TOP = 220
MARGIN = 160
LETTERS = {"DA1110": "A", "DA0940": "B", "DX1480": "C", "DL2150": "D"}
INFO = {
    "DA1110": dict(n=76, d=4.75, seq="CCA", colour=GREEN),
    "DA0940": dict(n=77, d=5.46, seq="CCA", colour=GREEN),
    "DX1480": dict(n=74, d=14.46, seq="CAA", colour=ORANGE),
    "DL2150": dict(n=84, d=14.56, seq="CCA", colour=ORANGE),
}


def white_bg(im: Image.Image) -> Image.Image:
    a = np.asarray(im.convert("RGB")).copy()
    a[(a >= 244).all(axis=2)] = 255
    return Image.fromarray(a)


def content_box(im: Image.Image, thresh: int = 8):
    diff = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, (255, 255, 255)))
    return diff.convert("L").point(lambda v: 255 if v > thresh else 0).getbbox()


def common_crop(images, pad: int):
    border = 400
    padded = [ImageOps.expand(im, border=border, fill="white") for im in images]
    boxes = [b for b in (content_box(im) for im in padded) if b]
    x0, y0 = min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad
    x1, y1 = max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad
    side = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    w, h = padded[0].size
    side = min(side, w, h)
    cx = min(max(cx, side // 2), w - side // 2)
    cy = min(max(cy, side // 2), h - side // 2)
    half = side // 2
    box = (cx - half, cy - half, cx + half, cy + half)
    return [im.crop(box) for im in padded], (box[0] - border, box[1] - border)


def blob_centroid(path: Path):
    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = (r > g + 45) & (r > b + 45) & (r > 110)
    if mask.sum() < 12:
        return None
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


def marker_bbox(path: Path):
    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = (r > g + 45) & (r > b + 45) & (r > 110)
    if mask.sum() < 12:
        return None
    ys, xs = np.nonzero(mask)
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def dashed(dr, p0, p1, fill, width=6, dash=22, gap=15):
    x0, y0 = p0
    x1, y1 = p1
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 1:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    t = 0.0
    while t < length:
        e = min(t + dash, length)
        dr.line([(x0 + ux * t, y0 + uy * t), (x0 + ux * e, y0 + uy * e)],
                fill=fill, width=width)
        t += dash + gap


def corner_bracket(dr, box, colour, arm=115, width=16, halo=5):
    x0, y0, x1, y1 = box
    arms = min(arm, int(0.26 * min(x1 - x0, y1 - y0)))
    corners = ((x0, y0, 1, 1), (x1, y0, -1, 1),
               (x0, y1, 1, -1), (x1, y1, -1, -1))
    for x, y, sx, sy in corners:
        for w, c in ((width + 2 * halo, "white"), (width, colour)):
            dr.line([(x, y), (x + sx * arms, y)], fill=c, width=w)
            dr.line([(x, y), (x, y + sy * arms)], fill=c, width=w)


def text_block(seq, num, f_seq, f_num, gap, stroke):
    w1 = f_seq.getlength(seq)
    w2 = f_num.getlength(num)
    a1, d1 = f_seq.getmetrics()
    a2, d2 = f_num.getmetrics()
    return (w1 + gap + w2 + 2 * stroke,
            max(a1, a2) + max(d1, d2) + 2 * stroke, w1)


def draw_block(dr, x, y, seq, num, f_seq, f_num, gap, colour, stroke):
    _, h, w1 = text_block(seq, num, f_seq, f_num, gap, stroke)
    baseline = y + max(f_seq.getmetrics()[0], f_num.getmetrics()[0])
    dr.text((x, baseline), seq, font=f_seq, fill=colour, anchor="ls",
            stroke_width=stroke, stroke_fill="white")
    dr.text((x + w1 + gap, baseline), num, font=f_num, fill=DGREY, anchor="ls",
            stroke_width=stroke, stroke_fill="white")


def place_block(ink, bounds, anchor_box, w, h, side_pref):
    x0, y0, x1, y1 = anchor_box
    bx0, by0, bx1, by1 = bounds
    cands = []
    for off in (48, 92, 150, 220):
        for fy in (0.0, 0.5, 1.0):
            cy = y0 + fy * max(0.0, (y1 - y0) - h)
            cands.append((x1 + off, cy, "right", off, 0 if fy == 0.5 else 15))
        for fx in (0.0, 0.5, 1.0):
            cx = x0 + fx * max(0.0, (x1 - x0) - w)
            cands.append((cx, y1 + off, "below", off, 0 if fx == 1.0 else (15 if fx == 0.5 else 32)))
        for fx in (0.0, 0.5, 1.0):
            cx = x0 + fx * max(0.0, (x1 - x0) - w)
            cands.append((cx, y0 - off - h, "above", off, 40))
        for fy in (0.0, 0.5, 1.0):
            cy = y0 + fy * max(0.0, (y1 - y0) - h)
            cands.append((x0 - off - w, cy, "left", off, 40))
    best = None
    for x, y, side, off, bias in cands:
        if x < bx0 or y < by0 or x + w > bx1 or y + h > by1:
            continue
        px0, py0 = int(max(0, x)), int(max(0, y))
        px1, py1 = int(min(ink.shape[1], x + w)), int(min(ink.shape[0], y + h))
        if px1 <= px0 or py1 <= py0:
            continue
        frac = float(ink[py0:py1, px0:px1].mean())
        score = frac * 30000 + off * 0.6 + side_pref[side] + bias
        if best is None or score < best[0]:
            best = (score, int(round(x)), int(round(y)), side)
    return best


def main() -> None:
    spec = json.loads((SRC / "markers.json").read_text())
    recs = spec["panels"]
    side = int(spec["side"])
    mains, mbox = common_crop([white_bg(Image.open(SRC / r["panel"])) for r in recs], pad=60)
    ms = CTX / mains[0].width
    zs = TILE / side

    tx, ty = (W - TILE) // 2, TOP
    ctx_ox, ctx_oy = tx + 70, ty + 70
    ctx_c = (ctx_ox + CTX // 2, ctx_oy + CTX // 2)

    def zmap(x, y):
        return x * zs + tx, y * zs + ty

    def mmap(x, y):
        return (x - mbox[0]) * ms + ctx_ox, (y - mbox[1]) * ms + ctx_oy

    f_id = ImageFont.truetype(BOLD, 114)
    f_dist = ImageFont.truetype(BOLD, 60)
    f_seq = ImageFont.truetype(BOLD, 84)
    f_num = ImageFont.truetype(REG, 48)
    f_ref = ImageFont.truetype(BOLD, 46)

    OUT.mkdir(parents=True, exist_ok=True)

    for i, rec in enumerate(recs):
        sid = rec["sid"]
        info = INFO[sid]
        col = info["colour"]
        n = info["n"]
        d376 = info["d"]

        fig = Image.new("RGB", (W, H), "white")
        dr = ImageDraw.Draw(fig)

        zoom = white_bg(Image.open(SRC / rec["inset"])).resize((TILE, TILE), Image.LANCZOS)
        fig.paste(zoom, (tx, ty))

        ctx = mains[i].resize((CTX, CTX), Image.LANCZOS)
        mask = Image.new("L", (CTX, CTX), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, CTX - 1, CTX - 1], fill=255)
        fig.paste(ctx, (ctx_ox, ctx_oy), mask)

        p3 = blob_centroid(Path(rec["main"]["model_3p"]["png"]))
        pr = blob_centroid(Path(rec["main"]["ref_3p"]["png"]))
        if p3 and pr:
            q3, qr = mmap(*p3), mmap(*pr)
            rc = ((q3[0] + qr[0]) / 2, (q3[1] + qr[1]) / 2)
            rr = CTX * 0.15
            rc = (min(max(rc[0], ctx_ox + rr), ctx_ox + CTX - rr),
                  min(max(rc[1], ctx_oy + rr), ctx_oy + CTX - rr))
            dr.ellipse([rc[0] - rr, rc[1] - rr, rc[0] + rr, rc[1] + rr],
                       outline=col, width=9)

        zoom_ink = (np.asarray(ImageChops.difference(
            zoom, Image.new("RGB", zoom.size, (255, 255, 255))).convert("L")) > 10)
        ink = np.zeros((H, W), dtype=bool)
        ink[ty:ty + TILE, tx:tx + TILE] = np.asarray(
            Image.fromarray((zoom_ink * 255).astype(np.uint8))
            .filter(ImageFilter.MaxFilter(11))) > 0
        bounds = (tx + 30, ty + 30, tx + TILE - 30, H - 42)

        a3 = blob_centroid(Path(rec["zoom"]["model_3p"]["png"]))
        aref = blob_centroid(Path(rec["zoom"]["ref_3p"]["png"]))
        if a3 and aref:
            q3, qr = zmap(*a3), zmap(*aref)
            dashed(dr, q3, qr, "white", width=19, dash=22, gap=15)
            dashed(dr, q3, qr, GREY, width=7, dash=22, gap=15)
            for p, c in ((q3, col), (qr, DGREY)):
                dr.ellipse([p[0] - 13, p[1] - 13, p[0] + 13, p[1] + 13], fill="white")
                dr.ellipse([p[0] - 9, p[1] - 9, p[0] + 9, p[1] + 9], fill=c)

        bracket = None
        tb = marker_bbox(Path(rec["zoom"]["model_tail_bbox"]["png"]))
        if tb:
            q0 = zmap(tb[0], tb[1])
            q1 = zmap(tb[2], tb[3])
            x0, x1 = sorted((q0[0], q1[0]))
            y0, y1 = sorted((q0[1], q1[1]))
            pad = 90
            bracket = (max(x0 - pad, tx + 26), max(y0 - pad, ty + 26),
                       min(x1 + pad, tx + TILE - 26), min(y1 + pad, H - 26))
            corner_bracket(dr, bracket, col, width=16)

        if bracket:
            seq = info["seq"]
            num = f"{n - 2}\u2013{n}"
            w, h, _ = text_block(seq, num, f_seq, f_num, 18, 13)
            side_pref = {"right": 35, "below": -40, "above": 80, "left": 110}
            best = place_block(ink, bounds, bracket, w, h, side_pref)
            if best:
                _, lx, ly, _ = best
                draw_block(dr, lx, ly, seq, num, f_seq, f_num, 18, col, 13)
                # the reference label must not land on top of this text
                pad = 14
                y0, y1 = max(0, int(ly - pad)), min(H, int(ly + h + pad))
                x0, x1 = max(0, int(lx - pad)), min(W, int(lx + w + pad))
                ink[y0:y1, x0:x1] = True

        rb = marker_bbox(Path(rec["zoom"]["ref_bbox"]["png"]))
        if rb:
            q0 = zmap(rb[0], rb[1])
            q1 = zmap(rb[2], rb[3])
            rx0, rx1 = sorted((q0[0], q1[0]))
            ry0, ry1 = sorted((q0[1], q1[1]))
            ref_box = (rx0, ry0, rx1, ry1)
            w = f_ref.getlength(REF_LABEL)
            a, d = f_ref.getmetrics()
            h = a + d
            best = place_block(ink, bounds, ref_box, w, h,
                               {"left": 0, "below": 40, "right": 120, "above": 200})
            if best:
                _, lx, ly, _ = best
                baseline = ly + a
                dr.text((lx, baseline), REF_LABEL, font=f_ref, fill=DGREY, anchor="ls",
                        stroke_width=6, stroke_fill="white")

        letter = LETTERS[sid]
        dr.text((MARGIN, 36), letter, font=f_id, fill=col)
        lw = dr.textlength(letter, font=f_id)
        dr.text((MARGIN + lw + 30, 36), sid, font=f_id, fill=INK)

        dtxt = f"{d376:.1f} \u00c5"
        dr.text((W - MARGIN, 96), dtxt, font=f_dist, fill=col, anchor="rm")

        dest = OUT / f"fig_af3_cca_{letter}_{sid}_{VERSION}.png"
        fig.save(dest, dpi=(300, 300))
        print(dest, fig.size)


if __name__ == "__main__":
    main()
