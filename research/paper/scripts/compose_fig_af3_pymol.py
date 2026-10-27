"""Compose the PyMOL AF3 renders into the 2x2 3'-terminus figure.

Reads figures_pymol/markers.json (from render_af3_pymol.py).  Each cell is the
magnified 3' end with a solid connector and labels drawn in 2D; a context circle
in the corner carries the whole fold with a ring over the magnified region.
Marker PNGs are isolated single spheres rendered under the same camera as their
image, so a blob centroid IS that atom's screen position.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

ST = Path("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca13")
SRC = ST / "figures_pymol"
WEB = ST / "webview" / "structures"
OUT = ST / "figures"

GREEN, ORANGE, INK, GREY = "#127a42", "#e28118", "#2c2f36", "#6a6f78"
RIM = "#9ba3ad"
D = "/usr/share/fonts/truetype/dejavu/DejaVuSans"
BOLD, REG = f"{D}-Bold.ttf", f"{D}.ttf"

TILE = 900          # displayed side of the magnified 3' view
CTX = 300           # displayed diameter of the whole-fold context circle
LABELS = ["A", "B", "C", "D"]


def tail_letters(sid: str, n: int) -> str:
    """Composition of the terminal three residues, in Sprinzl numbering."""
    seen: list[tuple[str, str]] = []
    for line in (WEB / f"{sid}_superposed.pdb").read_text().splitlines():
        if line.startswith(("ATOM", "HETATM")):
            key = (line[22:27].strip(), line[17:20].strip())
            if key not in seen:
                seen.append(key)
    return "-".join(r for _i, r in seen if n - 3 < int(_i) <= n)


def blob_centroid(path: Path) -> tuple[float, float] | None:
    """Centroid of the red sphere in an isolated-marker render, in that PNG's pixels."""
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


def common_crop(images, pad: int):
    """One square box for every image, so scale and placement stay comparable.

    Returns the crops plus the box origin in the ORIGINAL images' coordinates --
    it goes negative when the padding runs past the render, which is exactly what
    the marker transform needs.
    """
    border = 400
    padded = [ImageOps.expand(im, border=border, fill="white") for im in images]
    boxes = [b for b in (content_box(im) for im in padded) if b]
    if not boxes:
        return images, (0, 0)
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
    """Drop label boxes into the white space of a tile, away from ink and each other."""

    def __init__(self, ink: np.ndarray, origin: tuple[int, int], inner: tuple[int, int, int, int]):
        self.ink = ink
        self.origin = origin
        self.inner = inner          # figure coordinates
        self.block = np.zeros_like(ink, dtype=bool)
        self.failed = 0

    def occupy(self, rect, pad=6):
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
        """Nearest leader length wins; structure ink is penalised, blocked space rejected."""
        b = font.getbbox(text)
        w, h = b[2] - b[0] + 14, b[3] - b[1] + 12
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
            if best is not None and bs < 300:
                break
        if best is None:
            self.failed += 1
            return None
        self.occupy(best)
        return best


def leader(dr, anchor, rect, colour, width=3):
    ax, ay = anchor
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = ax - cx, ay - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return
    t = min((x1 - x0) / 2 / (abs(dx) + 1e-9), (y1 - y0) / 2 / (abs(dy) + 1e-9))
    dr.line([(cx + dx * t, cy + dy * t), (ax, ay)], fill="white", width=width + 4)
    dr.line([(cx + dx * t, cy + dy * t), (ax, ay)], fill=colour, width=width)
    r = width + 1.5
    dr.ellipse([ax - r, ay - r, ax + r, ay + r], fill=colour, outline="white", width=2)


def badge(dr, rect, text, font, colour):
    dr.rectangle(tuple(v - 1 for v in rect), fill="white", outline=colour, width=2)
    dr.text((rect[0] + 7, rect[1] + 4), text, font=font, fill=colour)


def main() -> None:
    spec = json.loads((SRC / "markers.json").read_text())
    recs = spec["panels"]
    for rec in recs:
        for key in ("panel", "inset"):
            if not (SRC / rec[key]).exists():
                raise SystemExit(f"missing {SRC / rec[key]}; run render_af3_pymol.py first")

    zooms, zbox = common_crop([Image.open(SRC / r["inset"]).convert("RGB") for r in recs], pad=130)
    mains, mbox = common_crop([Image.open(SRC / r["panel"]).convert("RGB") for r in recs], pad=30)
    zs = TILE / zooms[0].width
    ms = CTX / mains[0].width

    cap, gap, margin = 108, 46, 46
    title_h, legend_h = 116, 140
    top = margin + title_h
    W = margin * 2 + TILE * 2 + gap
    H = top + (TILE + cap) * 2 + legend_h
    fig = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(fig)

    f_title = ImageFont.truetype(BOLD, 38)
    f_sub = ImageFont.truetype(REG, 24)
    f_lab = ImageFont.truetype(BOLD, 32)
    f_cap = ImageFont.truetype(REG, 25)
    f_key = ImageFont.truetype(BOLD, 24)
    f_leg = ImageFont.truetype(REG, 24)

    dr.text((margin, margin), "AlphaFold 3 3\u2032 terminus vs the 1EHZ crystal", font=f_title, fill=INK)
    dr.text((margin, margin + 48),
            "Predictions (colour) Kabsch-superposed on 1EHZ (grey) over Sprinzl core 10\u201370.  "
            "The panel magnifies the 3\u2032 end; the circle shows the whole fold.",
            font=f_sub, fill=GREY)

    for i, rec in enumerate(recs):
        sid, grp, n, d376 = rec["sid"], rec["group"], rec["length"], rec["d376"]
        r, c = divmod(i, 2)
        x = margin + c * (TILE + gap)
        y = top + r * (TILE + cap)
        tile = zooms[i].resize((TILE, TILE), Image.LANCZOS)
        fig.paste(tile, (x, y))
        col = GREEN if grp == "true" else ORANGE

        ink = (np.asarray(ImageChops.difference(
            tile, Image.new("RGB", tile.size, (255, 255, 255))).convert("L")) > 12).astype(np.float32)
        ctx_c = (x + margin + CTX // 2, y + margin + CTX // 2)
        placer = Placer(ink, (x, y), (x + 12, y + 12, x + TILE - 12, y + TILE - 12))
        placer.block_disc(ctx_c[0], ctx_c[1], CTX / 2 + 16)

        def pt(view, key):
            got = blob_centroid(Path(rec[view][key]["png"]))
            if got is None:
                placer.failed += 1
                return None
            if view == "zoom":
                return ((got[0] - zbox[0]) * zs + x, (got[1] - zbox[1]) * zs + y)
            ox, oy = ctx_c[0] - CTX // 2, ctx_c[1] - CTX // 2
            return ((got[0] - mbox[0]) * ms + ox, (got[1] - mbox[1]) * ms + oy)

        a3, aref, atail = pt("zoom", "model_3p"), pt("zoom", "ref_3p"), pt("zoom", "model_tail")

        mid = ((a3[0] + aref[0]) / 2, (a3[1] + aref[1]) / 2) if (a3 and aref) else None
        dist_rect = placer.place(mid, f"{d376:.1f} \u00c5", f_key, 40, 300) if mid else None

        tl = tail_letters(sid, n)
        labels = []
        for anchor, text, colour in (
                (atail, f"3\u2032 tail {n - 2}\u2013{n} = {tl}", col),
                (aref, "1EHZ A76", "#4a4f58")):
            if anchor is None:
                continue
            rect = placer.place(anchor, text, f_key, 150, 460)
            if rect:
                labels.append((anchor, rect, text, colour))

        # context circle: whole fold, ringed over the region the panel magnifies
        ctx = mains[i].resize((CTX, CTX), Image.LANCZOS)
        mask = Image.new("L", (CTX, CTX), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, CTX - 1, CTX - 1], fill=255)
        fig.paste(ctx, (ctx_c[0] - CTX // 2, ctx_c[1] - CTX // 2), mask)
        dr.ellipse([ctx_c[0] - CTX // 2, ctx_c[1] - CTX // 2,
                    ctx_c[0] + CTX // 2, ctx_c[1] + CTX // 2], outline=RIM, width=4)
        p3, pr = pt("main", "model_3p"), pt("main", "ref_3p")
        if p3 and pr:
            # a fixed-proportion ring: the projected 3'-to-3' vector is parallel to
            # the view axis in some panels, so it carries no usable scale
            rc = ((p3[0] + pr[0]) / 2, (p3[1] + pr[1]) / 2)
            rr = CTX * 0.15
            rc = (min(max(rc[0], ctx_c[0] - CTX // 2 + rr), ctx_c[0] + CTX // 2 - rr),
                  min(max(rc[1], ctx_c[1] - CTX // 2 + rr), ctx_c[1] + CTX // 2 - rr))
            dr.ellipse([rc[0] - rr, rc[1] - rr, rc[0] + rr, rc[1] + rr], outline=col, width=4)

        if a3 and aref:
            dr.line([a3, aref], fill="white", width=12)
            dr.line([a3, aref], fill=col, width=6)
            for a in (a3, aref):
                dr.ellipse([a[0] - 4, a[1] - 4, a[0] + 4, a[1] + 4], fill=col, outline="white", width=2)
        for anchor, rect, text, colour in labels:
            leader(dr, anchor, rect, colour)
            badge(dr, rect, text, f_key, colour)
        if mid and dist_rect:
            badge(dr, dist_rect, f"{d376:.1f} \u00c5", f_key, col)

        name = "true CCA (gold 74\u201376)" if grp == "true" else "CCA-like end (no gold 74\u201376)"
        dr.text((x, y - 8), LABELS[i], font=f_lab, fill=INK)
        dr.text((x + 48, y - 6), sid, font=f_lab, fill=col)
        dr.text((x, y + TILE + 10), f"{name}   {n} nt", font=f_cap, fill=GREY)
        dr.text((x, y + TILE + 44),
                f"3\u2032 C3\u2032 \u2194 1EHZ A76 C3\u2032 = {d376:.1f} \u00c5", font=f_lab, fill=col)
        if placer.failed:
            print(f"  ! {sid}: {placer.failed} label(s) unplaceable")

    ly = top + (TILE + cap) * 2 + 16
    dr.line([(margin, ly + 12), (margin + 46, ly + 12)], fill=RIM, width=8)
    dr.text((margin + 58, ly), "1EHZ crystal (ground truth)", font=f_leg, fill=GREY)
    for j, (colour, txt) in enumerate([(GREEN, "AF3, gold CCA 74\u201376"),
                                       (ORANGE, "AF3, CCA-like 3\u2032 end")]):
        xx = margin + 470 + j * 430
        dr.ellipse([xx, ly + 2, xx + 22, ly + 24], fill=colour)
        dr.text((xx + 32, ly), txt, font=f_leg, fill=INK)
    dr.line([(margin, ly + 68), (margin + 46, ly + 68)], fill=INK, width=6)
    dr.text((margin + 58, ly + 56),
            "solid line = 3\u2032 C3\u2032 \u2194 1EHZ A76 C3\u2032   \u2022   thick sticks + spheres = "
            "modelled terminal 3 nt   \u2022   dark sticks = 1EHZ A76",
            font=f_leg, fill=INK)

    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / "fig_af3_cca_pymol.png"
    fig.save(dest)
    print(f"{dest}  {fig.size[0]}x{fig.size[1]}")


if __name__ == "__main__":
    main()
