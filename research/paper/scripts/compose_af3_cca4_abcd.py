"""Compose the four A-D validation panels as standalone figures.

Each output keeps the original ABCD panel composition: a magnified 3' end
(prediction over 1EHZ), a whole-fold context circle, and 2D labels.  The four
are written separately rather than as a grid.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

ST = Path("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019")
SRC = ST / "figures_abcd_pymol"
CLEAN = "--clean" in sys.argv
OUT = ST / ("figures_abcd_clean" if CLEAN else "figures_abcd")

GREEN, ORANGE, INK, GREY = "#127a42", "#e28118", "#2c2f36", "#6a6f78"
RIM = "#9ba3ad"
D = "/usr/share/fonts/truetype/dejavu/DejaVuSans"
BOLD, REG = f"{D}-Bold.ttf", f"{D}.ttf"

TILE = 1300
CTX = 400
LETTERS = {"DA1110": "A", "DA0940": "B", "DX1480": "C", "DL2150": "D"}
INFO = {
    "DA1110": dict(n=76, d=8.55, kind="bona fide 3\u2032-CCA", seq="C-C-A",
                   note="", colour=GREEN),
    "DA0940": dict(n=77, d=6.95, kind="bona fide 3\u2032-CCA", seq="C-C-A",
                   note="", colour=GREEN),
    "DX1480": dict(n=74, d=18.28, kind="3\u2032-CAA, no CCA", seq="C-A-A",
                   note="", colour=ORANGE),
    "DL2150": dict(n=84, d=18.68, kind="3\u2032-CCA, displaced", seq="C-C-A",
                   note="8-nt 3\u2032 extension", colour=ORANGE),
}


def tail_letters(sid: str, n: int) -> str:
    seen = []
    for line in (SRC / ".." / "structures" / f"{sid}.pdb").read_text().splitlines():
        if line.startswith(("ATOM", "HETATM")):
            key = (line[22:27].strip(), line[17:20].strip())
            if key not in seen:
                seen.append(key)
    return "-".join(r for i, r in seen if n - 3 < int(i) <= n)


def blob_centroid(path: Path):
    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = (r > g + 45) & (r > b + 45) & (r > 110)
    if mask.sum() < 12:
        return None
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


def content_box(im: Image.Image, thresh: int = 8):
    diff = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, (255, 255, 255)))
    return diff.convert("L").point(lambda v: 255 if v > thresh else 0).getbbox()


def white_bg(im: Image.Image) -> Image.Image:
    a = np.asarray(im.convert("RGB")).copy()
    a[(a >= 244).all(axis=2)] = 255
    return Image.fromarray(a)


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


class Placer:
    def __init__(self, ink, origin, inner, scale=1.0):
        self.ink = ink
        self.origin = origin
        self.inner = inner
        self.block = np.zeros_like(ink, dtype=bool)
        self.failed = 0
        self.scale = scale

    def occupy(self, rect, pad=None):
        pad = int(6 * self.scale) if pad is None else pad
        x0, y0 = max(0, rect[0] - self.origin[0] - pad), max(0, rect[1] - self.origin[1] - pad)
        x1 = min(self.block.shape[1], rect[2] - self.origin[0] + pad)
        y1 = min(self.block.shape[0], rect[3] - self.origin[1] + pad)
        if x1 > x0 and y1 > y0:
            self.block[y0:y1, x0:x1] = True

    def _region(self, rect):
        ox, oy = self.origin
        return (max(0, rect[0] - ox), max(0, rect[1] - oy),
                min(self.ink.shape[1], rect[2] - ox), min(self.ink.shape[0], rect[3] - oy))

    def block_disc(self, cx, cy, r):
        x0, y0 = max(0, int(cx - r - self.origin[0])), max(0, int(cy - r - self.origin[1]))
        x1 = min(self.block.shape[1], int(cx + r - self.origin[0]))
        y1 = min(self.block.shape[0], int(cy + r - self.origin[1]))
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1]
        self.block[y0:y1, x0:x1] |= (xx + self.origin[0] - cx) ** 2 + (yy + self.origin[1] - cy) ** 2 < r * r

    def place(self, anchor, text, font, rmin, rmax, rings=7, spokes=36):
        b = font.getbbox(text)
        w, h = b[2] - b[0] + int(14 * self.scale), b[3] - b[1] + int(12 * self.scale)
        ix0, iy0, ix1, iy1 = self.inner
        best, bs = None, 1e9
        for k in range(rings):
            rad = rmin if rings == 1 else rmin + (rmax - rmin) * k / (rings - 1)
            for s in range(spokes):
                ang = 2 * math.pi * s / spokes
                cx = anchor[0] + rad * math.cos(ang)
                cy = anchor[1] + rad * math.sin(ang)
                x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
                rect = (x0, y0, x0 + int(round(w)), y0 + int(round(h)))
                if rect[0] < ix0 or rect[1] < iy0 or rect[2] > ix1 or rect[3] > iy1:
                    continue
                lx0, ly0, lx1, ly1 = self._region(rect)
                if self.block[ly0:ly1, lx0:lx1].any():
                    continue
                sc = 9000.0 * float(self.ink[ly0:ly1, lx0:lx1].mean()) + 2.0 * rad
                if sc < bs:
                    best, bs = rect, sc
            if best is not None and bs < 300 * self.scale:
                break
        if best is None:
            self.failed += 1
            return None
        self.occupy(best)
        return best


def leader(dr, anchor, rect, colour, width=4):
    ax, ay = anchor
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = ax - cx, ay - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return
    t = min((x1 - x0) / 2 / (abs(dx) + 1e-9), (y1 - y0) / 2 / (abs(dy) + 1e-9))
    dr.line([(cx + dx * t, cy + dy * t), (ax, ay)], fill="white", width=width + 5)
    dr.line([(cx + dx * t, cy + dy * t), (ax, ay)], fill=colour, width=width)
    r = width + 2
    dr.ellipse([ax - r, ay - r, ax + r, ay + r], fill=colour, outline="white", width=3)


def badge(dr, rect, text, font, colour):
    dr.rectangle(tuple(v - 1 for v in rect), fill="white", outline=colour, width=3)
    dr.text((rect[0] + 10, rect[1] + 6), text, font=font, fill=colour)


def rule(dr, x0, y0, x1, colour):
    dr.line([(x0, y0), (x1, y0)], fill=colour, width=2)


def main() -> None:
    spec = json.loads((SRC / "markers.json").read_text())
    recs = spec["panels"]
    zooms, zbox = common_crop([white_bg(Image.open(SRC / r["inset"])) for r in recs], pad=130)
    mains, mbox = common_crop([white_bg(Image.open(SRC / r["panel"])) for r in recs], pad=30)
    zs = TILE / zooms[0].width
    ms = CTX / mains[0].width
    s = TILE / 900.0

    if CLEAN:
        W, H = 1460, 1440
        margin = 80
        top = 118
    else:
        W, H = 1560, 1990
        margin = 80
        top = 290
    fig = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(fig)

    f_overline = ImageFont.truetype(REG, 20)
    f_id = ImageFont.truetype(BOLD, 62)
    f_title = ImageFont.truetype(BOLD, 38)
    f_sub = ImageFont.truetype(REG, 23)
    f_key = ImageFont.truetype(BOLD, int(24 * s))
    f_cap = ImageFont.truetype(REG, 30)
    f_status = ImageFont.truetype(BOLD, 32)
    f_eq = ImageFont.truetype(BOLD, 38)
    f_leg = ImageFont.truetype(REG, 23)
    f_note = ImageFont.truetype(REG, 21)

    OUT.mkdir(parents=True, exist_ok=True)

    for i, rec in enumerate(recs):
        sid = rec["sid"]
        info = INFO[sid]
        col = info["colour"]
        n = info["n"]
        d376 = info["d"]

        zoom = zooms[i].resize((TILE, TILE), Image.LANCZOS)
        tx, ty = (W - TILE) // 2, top
        fig.paste(zoom, (tx, ty))

        ink = (np.asarray(ImageChops.difference(
            zoom, Image.new("RGB", zoom.size, (255, 255, 255))).convert("L")) > 12).astype(np.float32)
        ctx_c = (tx + 42 + CTX // 2, ty + 42 + CTX // 2)
        placer = Placer(ink, (tx, ty), (tx + 14, ty + 14, tx + TILE - 14, ty + TILE - 14), scale=s)
        placer.block_disc(ctx_c[0], ctx_c[1], CTX / 2 + 20)

        def pt(view, key):
            got = blob_centroid(Path(rec[view][key]["png"]))
            if got is None:
                placer.failed += 1
                return None
            if view == "zoom":
                return ((got[0] - zbox[0]) * zs + tx, (got[1] - zbox[1]) * zs + ty)
            ox, oy = ctx_c[0] - CTX // 2, ctx_c[1] - CTX // 2
            return ((got[0] - mbox[0]) * ms + ox, (got[1] - mbox[1]) * ms + oy)

        a3, aref, atail = pt("zoom", "model_3p"), pt("zoom", "ref_3p"), pt("zoom", "model_tail")

        mid = ((a3[0] + aref[0]) / 2, (a3[1] + aref[1]) / 2) if (a3 and aref) else None
        dist_rect = placer.place(mid, f"{d376:.1f} \u00c5", f_key,
                                 40 * s, 300 * s) if mid else None

        tl = tail_letters(sid, n)
        labels = []
        for anchor, text, colour in (
                (atail, f"3\u2032 tail {n - 2}\u2013{n} = {tl}", col),
                (aref, "1EHZ A76", "#4a4f58")):
            if anchor is None:
                continue
            rect = placer.place(anchor, text, f_key, 150 * s, 460 * s)
            if rect:
                labels.append((anchor, rect, text, colour))

        ctx = mains[i].resize((CTX, CTX), Image.LANCZOS)
        mask = Image.new("L", (CTX, CTX), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, CTX - 1, CTX - 1], fill=255)
        fig.paste(ctx, (ctx_c[0] - CTX // 2, ctx_c[1] - CTX // 2), mask)
        p3, pr = pt("main", "model_3p"), pt("main", "ref_3p")
        if p3 and pr:
            rc = ((p3[0] + pr[0]) / 2, (p3[1] + pr[1]) / 2)
            rr = CTX * 0.15
            rc = (min(max(rc[0], ctx_c[0] - CTX // 2 + rr), ctx_c[0] + CTX // 2 - rr),
                  min(max(rc[1], ctx_c[1] - CTX // 2 + rr), ctx_c[1] + CTX // 2 - rr))
            dr.ellipse([rc[0] - rr, rc[1] - rr, rc[0] + rr, rc[1] + rr], outline=col, width=4)

        if a3 and aref:
            dr.line([a3, aref], fill="white", width=14)
            dr.line([a3, aref], fill=col, width=7)
            for a in (a3, aref):
                dr.ellipse([a[0] - 5, a[1] - 5, a[0] + 5, a[1] + 5], fill=col, outline="white", width=2)
        for anchor, rect, text, colour in labels:
            leader(dr, anchor, rect, colour)
            badge(dr, rect, text, f_key, colour)
        if mid and dist_rect:
            badge(dr, dist_rect, f"{d376:.1f} \u00c5", f_key, col)

        letter = LETTERS[sid]
        # header: only the panel letter and model id
        dr.text((margin, 26 if CLEAN else 70), letter, font=f_id, fill=col)
        lw = dr.textlength(letter, font=f_id)
        dr.text((margin + lw + 22, 26 if CLEAN else 70), sid, font=f_id, fill=INK)
        if not CLEAN:
            dr.text((margin, 46), "ALPHAFOLD3 \u00b7 tRNA 3\u2032-END", font=f_overline, fill=GREY)
            dr.text((margin, 148),
                    "3\u2032 terminus vs the 1EHZ crystal", font=f_title, fill=INK)
            dr.text((margin, 194),
                    "AF3 prediction (colour) superposed on 1EHZ (grey).  "
                    "Magnified 3\u2032 end; the circle shows the whole fold.",
                    font=f_sub, fill=GREY)
            dr.line([(margin, 252), (W - margin, 252)], fill="#e6e6e2", width=2)

            # caption
            cy = ty + TILE + 28
            status = info["kind"] + (f" \u00b7 {n} nt" if not info["note"]
                                     else f" \u00b7 {n} nt ({info['note']})")
            dr.text((tx, cy), status, font=f_status, fill=col)
            dr.text((tx, cy + 46),
                    f"3\u2032 C3\u2032 \u2194 1EHZ A76 C3\u2032 = {d376:.1f} \u00c5",
                    font=f_eq, fill=col)
            dr.text((tx, cy + 100),
                    f"terminal triplet 5\u2032-{tl}-3\u2032 \u00b7 residues {n-2}\u2013{n}",
                    font=f_cap, fill=GREY)

            # legend
            ly = cy + 156
            dr.line([(tx, ly + 12), (tx + 46, ly + 12)], fill=RIM, width=8)
            dr.text((tx + 58, ly), "1EHZ crystal (reference)", font=f_leg, fill=GREY)
            x2 = tx + 400
            dr.line([(x2, ly + 12), (x2 + 46, ly + 12)], fill=col, width=8)
            dr.text((x2 + 58, ly), "AF3 prediction", font=f_leg, fill=INK)
            x3 = tx + 710
            dr.line([(x3, ly + 4), (x3 + 10, ly + 22)], fill="#2c2f36", width=6)
            dr.text((x3 + 20, ly), "1EHZ A76", font=f_leg, fill=INK)
            x4 = tx + 900
            for k in range(5):
                rr = 7
                cx = x4 + (k % 3) * 12
                cyy = ly + 8 + (k // 3) * 12
                dr.ellipse([cx - rr, cyy - rr, cx + rr, cyy + rr], fill=col)
            dr.text((x4 + 42, ly), "modelled terminal 3 nt", font=f_leg, fill=INK)
            dr.line([(tx, ly + 44), (tx + 46, ly + 44)], fill=INK, width=6)
            dr.text((tx + 58, ly + 34),
                    "solid line = 3\u2032 C3\u2032 \u2194 1EHZ A76 C3\u2032 (3D, after superposition)",
                    font=f_leg, fill=INK)

            # footnote
            dr.line([(margin, 1884), (W - margin, 1884)], fill="#e6e6e2", width=2)
            dr.text((margin, 1900),
                    "1EHZ A76 is the external crystal reference used for the distance; it is not part of the AF3 input.",
                    font=f_note, fill=GREY)
            dr.text((margin, 1928),
                    "The prediction is superposed on the 1EHZ core (Sprinzl 10\u201370); distances are C3\u2032\u2013C3\u2032 in 3D.",
                    font=f_note, fill=GREY)

        suffix = "_clean.png" if CLEAN else "_abcd.png"
        dest = OUT / f"fig_af3_cca_{letter}_{sid}{suffix}"
        fig.save(dest, dpi=(200, 200))
        print(dest, fig.size)

        # reset for the next panel
        fig.paste((255, 255, 255), (0, 0, W, H))


if __name__ == "__main__":
    main()
