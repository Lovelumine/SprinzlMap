"""Write the plane-orientation diagnostics to a text file (stdout is unreliable
under pymol -cq with cmd.quit)."""
import math
import os

import numpy as np
from pymol import cmd

ST = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019"
REF = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca13/webview/structures/1ehz.pdb"
MODELS = os.path.join(ST, "structures")
PANELS = [("DA1110", 76), ("DA0940", 77), ("DX1480", 74), ("DL2150", 84)]
PUR = " or ".join(f"name {a}" for a in ("N1", "C2", "N3", "C4", "C5", "C6", "N7", "C8", "N9"))
PYR = " or ".join(f"name {a}" for a in ("N1", "C2", "N3", "C4", "C5", "C6"))
SUGAR = " or ".join(f"name {a}" for a in
                    ("P", "OP1", "OP2", "OP3", "O5'", "C5'", "C4'", "O4'",
                     "C3'", "C2'", "O2'", "C1'"))
lines = []

cmd.reinitialize()
cmd.load(REF, "ref")
for i, (sid, _n) in enumerate(PANELS):
    cmd.load(os.path.join(MODELS, f"{sid}.pdb"), f"m{i}")


def ring_sel(resi, name):
    m = cmd.get_model(f"{name} and resi {resi}", 1)
    r = m.atom[0].resn.upper()
    sel = f"{name} and resi {resi} and ({PUR if r in ('A', 'G') else PYR})"
    return sel, r, len(cmd.get_model(sel, 1).atom)


def plane_normal(sel):
    pts = np.array([a.coord for a in cmd.get_model(sel, 1).atom], float)
    _u, _s, vt = np.linalg.svd(pts - pts.mean(0))
    return vt[2] / np.linalg.norm(vt[2])


def acute(a, b):
    d = abs(float(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return math.degrees(math.acos(min(1.0, d)))


sel, resn, nat = ring_sel(76, "ref")
lines.append(f"ref resi 76 resname={resn} ring atoms={nat}")
rn = plane_normal(sel)

model_ns, anchor = [], None
for i, (sid, n) in enumerate(PANELS):
    for r in (n, n - 1, n - 2):
        s, rn_, nat_ = ring_sel(r, f"m{i}")
        v = plane_normal(s)
        if anchor is None:
            anchor = v
        model_ns.append((f"{sid} {r} {rn_} atoms={nat_}", v if np.dot(v, anchor) > 0 else -v))
target = np.mean([v for _k, v in model_ns], axis=0)
target /= np.linalg.norm(target)

arm = np.array(cmd.get_atom_coords("ref and resi 62 and name C3'")) - \
    np.array(cmd.get_atom_coords("ref and resi 76 and name C3'"))
arm /= np.linalg.norm(arm)

cmd.orient("ref")
LOCAL = " or ".join(["ref and resi 73-76"] +
                    [f"m{i} and resi {n - 3}-{n}" for i, (_s, n) in enumerate(PANELS)])
cmd.zoom(LOCAL, buffer=14, complete=1, animate=0)
R = np.array(cmd.get_view(0)[0:9]).reshape(3, 3)
cur = R[2]

z = target
y = arm - np.dot(arm, z) * z
y /= np.linalg.norm(y)
x = np.cross(y, z)
M = np.vstack([x, y, z])
v = list(cmd.get_view(0))
v[0:9] = list(M.flatten())
cmd.set_view(v)
cmd.zoom(LOCAL, buffer=14, complete=1, animate=0)
R2 = np.array(cmd.get_view(0)[0:9]).reshape(3, 3)
z2 = R2[2]

lines.append("")
lines.append("plane normal vs current camera | vs face-on camera | vs ref A76 | vs arm axis")
lines.append(f"{'ref A76':16s} {acute(rn, cur):5.1f} {acute(rn, z2):5.1f} {0.0:5.1f} {acute(rn, arm):5.1f}")
for k, vv in model_ns:
    lines.append(f"{k:24s} {acute(vv, cur):5.1f} {acute(vv, z2):5.1f} {acute(vv, rn):5.1f} {acute(vv, arm):5.1f}")

c3 = np.array(cmd.get_atom_coords("ref and resi 76 and name C3'"))
n9 = np.array(cmd.get_atom_coords("ref and resi 76 and name N9"))
k = n9 - c3
k /= np.linalg.norm(k)
lines.append("")
lines.append("glycosidic axis C3'->N9: angle to ref A76 normal %.1f deg (cone half-angle)"
             % acute(k, rn))


def rot(vv, ang):
    c, s = math.cos(ang), math.sin(ang)
    return vv * c + np.cross(k, vv) * s + k * np.dot(k, vv) * (1 - c)


scan = sorted(((acute(rot(rn, math.radians(t)), target), t) for t in range(360)))[:4]
lines.append("best chi rotations of the A76 base toward the model planes: "
             + ", ".join("%.1f deg at theta %d" % (a, t) for a, t in scan))

with open(os.path.join(ST, "figures_abcd_pymol", "camera_previews", "diag.txt"), "w") as fh:
    fh.write("\n".join(lines) + "\n")
