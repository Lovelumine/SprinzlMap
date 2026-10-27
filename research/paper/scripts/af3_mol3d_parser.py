"""Minimal software 3D renderer for mmRNA/mmCIF backbone plots (stdlib + numpy only)."""
from __future__ import annotations

import math
import struct
import zlib
from collections import OrderedDict

import numpy as np


# ---------------------------------------------------------------- png output
def write_png(path: str, rgb: np.ndarray) -> None:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(png)


# ---------------------------------------------------------------- cif parsing
def read_residues(path: str) -> OrderedDict:
    """label_seq_id -> {atom: (x,y,z), '_c': comp, '_b': mean B-factor}."""
    res: OrderedDict = OrderedDict()
    acc: dict = {}
    for line in open(path):
        if not line.startswith(("ATOM", "HETATM")):
            continue
        p = line.split()
        if p[8] in (".", "?"):
            continue
        sq = int(p[8])
        name = p[3].strip('"')
        xyz = (float(p[10]), float(p[11]), float(p[12]))
        d = res.setdefault(sq, {})
        d[name] = xyz
        d.setdefault("_c", p[5].strip('"'))
        acc.setdefault(sq, []).append(float(p[14]))
    for sq, b in acc.items():
        res[sq]["_b"] = sum(b) / len(b)
    return OrderedDict(sorted(res.items()))


def kabsch(P, Q):
    P = np.asarray(P, float)
    Q = np.asarray(Q, float)
    pc, qc = P.mean(0), Q.mean(0)
    A, B = P - pc, Q - qc
    U, S, Vt = np.linalg.svd(A.T @ B)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    return R, qc - pc @ R.T


# ---------------------------------------------------------------- rasterizer
class Scene:
    def __init__(self, w, h, bg=(255, 255, 255)):
        self.w, self.h = w, h
        self.img = np.zeros((h, w, 3), np.uint8)
        self.img[:] = bg
        self.prims: list[tuple] = []

    def _rot(self, p, R, t):
        return np.asarray(p, float) @ R.T + t

    def project(self, xyz, R, t, cam, scale, cx, cy):
        q = self._rot(xyz, R, t)
        z = q[2] - cam
        f = 900.0
        k = f / max(-z, 1.0)
        return cx + q[0] * scale * k / f, cy - q[1] * scale * k / f, -z

    def tube(self, pts, R, t, cam, scale, cx, cy, color, radius, nseg=1):
        pts = [self.project(p, R, t, cam, scale, cx, cy) for p in pts]
        for i in range(len(pts) - 1):
            self.prims.append(("seg", pts[i], pts[i + 1], color, radius))

    def ball(self, xyz, R, t, cam, scale, cx, cy, color, radius):
        self.prims.append(("sph", self.project(xyz, R, t, cam, scale, cx, cy), None, color, radius))

    def _disc(self, x, y, r, color, shade=1.0):
        x0, x1 = max(0, int(x - r)), min(self.w, int(x + r) + 1)
        y0, y1 = max(0, int(y - r)), min(self.h, int(y + r) + 1)
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d2 = (xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2
        m = d2 <= r * r
        col = np.asarray(color, float)
        if shade < 1.0:
            s = 0.55 + 0.45 * np.sqrt(np.clip(1 - d2 / max(r * r, 1e-6), 0, 1))
            patch = np.stack([col[k] * s for k in range(3)], -1)
        else:
            patch = np.broadcast_to(col, (*d2.shape, 3))
        region = self.img[y0:y1, x0:x1]
        region[m] = patch[m].clip(0, 255).astype(np.uint8)

    def render(self):
        self.prims.sort(key=lambda p: -p[1][2])  # far (large depth) first
        for kind, a, b, color, r in self.prims:
            if kind == "sph":
                self._disc(a[0], a[1], r, color, shade=0.55)
                continue
            ax, ay, _ = a
            bx, by, _ = b
            steps = max(2, int(math.hypot(bx - ax, by - ay) * 2))
            for i in range(steps + 1):
                u = i / steps
                self._disc(ax + (bx - ax) * u, ay + (by - ay) * u, r, color, shade=0.75)
        return self.img


def plddt_color(b):
    if b >= 90:
        return (26, 120, 60)
    if b >= 70:
        return (120, 190, 90)
    if b >= 50:
        return (235, 200, 60)
    return (215, 110, 45)
