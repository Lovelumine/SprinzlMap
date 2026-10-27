"""Render AF3 tRNA models as PNG without any plotting dependency.

Projection is done along the molecule's principal axes so the classic L shape
faces the viewer. Text uses a built-in 5x7 bitmap font.
"""
from __future__ import annotations

import math
import sys

import numpy as np

sys.path.insert(0, "/tmp")
from af3_mol3d_parser import kabsch, plddt_color, read_residues, write_png

REF = "/mnt/raid/alphafold3/databases/mmcif_files/1ehz.cif"
MODELS = "/mnt/raid/alphafold3/outputs/cca13"
OUT = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca13/figures"

GREEN = (18, 122, 66)
ORANGE = (226, 129, 24)
GREY = (168, 168, 174)
DARK = (40, 40, 48)


# ------------------------------------------------------------------------- font
GLYPHS = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 11001 10101 10011 10001 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10011 01111", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 11011 10001", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "01110 10000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00001 01110",
    " ": "00000 00000 00000 00000 00000 00000 00000", "-": "00000 00000 00000 11111 00000 00000 00000",
    "=": "00000 11111 00000 00000 11111 00000 00000", ".": "00000 00000 00000 00000 00000 01100 01100",
    ",": "00000 00000 00000 00000 01100 00100 01000", ":": "00000 01100 01100 00000 01100 01100 00000",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "/": "00001 00010 00010 00100 01000 01000 10000", "'": "00100 00100 00000 00000 00000 00000 00000",
    "+": "00000 00100 00100 11111 00100 00100 00000", "%": "11001 11010 00010 00100 01000 01011 10011",
    ">": "01000 00100 00010 00001 00010 00100 01000", "<": "00010 00100 01000 10000 01000 00100 00010",
    "_": "00000 00000 00000 00000 00000 00000 11111",
}


def draw_text(img, x, y, text, color=DARK, s=2):
    h, w, _ = img.shape
    cx = int(round(x))
    y = int(round(y))
    for ch in text.upper():
        g = GLYPHS.get(ch, GLYPHS[" "]).split()
        for r, row in enumerate(g):
            for c, bit in enumerate(row):
                if bit == "1":
                    x0, y0 = cx + c * s, y + r * s
                    if 0 <= x0 < w and 0 <= y0 < h:
                        img[y0:y0 + s, x0:x0 + s] = color
        cx += 6 * s
    return cx


# ------------------------------------------------------------------- projection
class View:
    def __init__(self, coords, axes, W, H, margin=0.13, cam=320.0, f=900.0, flip_y=True):
        self.cen = coords.mean(0)
        self.R = np.asarray(axes, float)
        q = (coords - self.cen) @ self.R.T
        self.flip_y = flip_y
        k = f / (cam - q[:, 2])
        sx, sy = q[:, 0] * k / f, q[:, 1] * k / f
        span = max(np.ptp(sx), np.ptp(sy) * (H / W))
        self.scale = min(W, H) * (1 - 2 * margin) / span
        self.cam, self.f, self.cx, self.cy = cam, f, W / 2, H / 2

    def to2d(self, p):
        q = (np.asarray(p, float) - self.cen) @ self.R.T
        k = self.f / (self.cam - q[2])
        x = self.cx + q[0] * self.scale * k / self.f
        y = self.cy + (-1 if self.flip_y else 1) * q[1] * self.scale * k / self.f
        return float(x), float(y), float(self.cam - q[2])


def principal_axes(coords):
    c = coords - coords.mean(0)
    U, S, Vt = np.linalg.svd(c, full_matrices=False)
    v = Vt
    if np.linalg.det(v) < 0:
        v = v * np.array([1, 1, -1])
    return v


def raster_disc(img, x, y, r, color):
    h, w, _ = img.shape
    x0, x1 = max(0, int(x - r)), min(w, int(x + r) + 2)
    y0, y1 = max(0, int(y - r)), min(h, int(y + r) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d2 = (xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2
    m = d2 <= r * r
    r2 = max(r * r, 1e-6)
    shade = 0.62 + 0.38 * np.sqrt(np.clip(1 - d2 / r2, 0, 1))
    col = np.asarray(color, float)
    patch = np.stack([col[a] * shade for a in range(3)], -1)
    reg = img[y0:y1, x0:x1]
    reg[m] = patch[m].clip(0, 255).astype(np.uint8)


def draw_model(img, res, view, color_fn, radius, atoms=("C3'",), head_radius=0.0):
    """Depth-sorted backbone tube for one chain."""
    ids = [i for i in sorted(res) if "C3'" in res[i]]
    prims = []
    for a in atoms:
        for i in range(len(ids) - 1):
            p0, p1 = res[ids[i]].get(a), res[ids[i + 1]].get(a)
            if not p0 or not p1:
                continue
            x0, y0, z0 = view.to2d(p0)
            x1, y1, z1 = view.to2d(p1)
            prims.append((max(z0, z1), "seg", (x0, y0), (x1, y1), color_fn(ids[i]), radius))
    if head_radius:
        for i in ids:
            x, y, z = view.to2d(res[i]["C3'"])
            prims.append((z, "dot", (x, y), None, color_fn(i), head_radius))
    prims.sort(key=lambda p: -p[0])
    for _, kind, a, b, col, r in prims:
        if kind == "dot":
            raster_disc(img, a[0], a[1], r, col)
            continue
        steps = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) * 2))
        for i in range(steps + 1):
            u = i / steps
            raster_disc(img, a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, r, col)


def backbone_xyz(res):
    return np.array([res[i]["C3'"] for i in sorted(res) if "C3'" in res[i]])
