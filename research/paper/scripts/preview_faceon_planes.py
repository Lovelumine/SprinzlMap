"""Preview: camera facing the modelled terminal base planes, reference shown two ways.

Variant "true": 1EHZ A76 left exactly as the crystal has it.
Variant "par":  A76's base rotated about its own C3'-N9 glycosidic axis to the
                closest achievable orientation parallel to the modelled planes.
                Display-only -- the coordinates are no longer the crystal's.
"""
import math
import os

import numpy as np
from pymol import cmd

ST = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019"
REF = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca13/webview/structures/1ehz.pdb"
MODELS = os.path.join(ST, "structures")
OUT = os.path.join(ST, "figures_abcd_pymol", "camera_previews")
PANELS = [("DA1110", 76), ("DA0940", 77), ("DX1480", 74), ("DL2150", 84)]
PUR = " or ".join(f"name {a}" for a in ("N1", "C2", "N3", "C4", "C5", "C6", "N7", "C8", "N9"))
PYR = " or ".join(f"name {a}" for a in ("N1", "C2", "N3", "C4", "C5", "C6"))
SUGAR = " or ".join(f"name {a}" for a in
                    ("P", "OP1", "OP2", "OP3", "O5'", "C5'", "C4'", "O4'",
                     "C3'", "C2'", "O2'", "C1'"))
SIDE = 1800

os.makedirs(OUT, exist_ok=True)
cmd.reinitialize()
cmd.bg_color("white")
for k, v in {"ray_trace_mode": 0, "antialias": 3, "ambient": 0.50, "direct": 0.62,
             "specular": 0.06, "ray_shadow": 0, "depth_cue": 0, "orthoscopic": 1,
             "cartoon_smooth_loops": 1}.items():
    cmd.set(k, v)
cmd.set_color("green", [0x12 / 255, 0x7a / 255, 0x42 / 255])
cmd.set_color("orange", [0xe2 / 255, 0x81 / 255, 0x18 / 255])
cmd.set_color("refgrey", [0x9b / 255, 0xa3 / 255, 0xad / 255])
cmd.set_color("dark", [0x2c / 255, 0x2f / 255, 0x36 / 255])


def style_ref(obj):
    cmd.show("cartoon", obj)
    cmd.hide("sticks", obj)
    cmd.hide("lines", obj)
    cmd.set("cartoon_nucleic_acid_mode", 0, obj)
    cmd.set("cartoon_ladder_mode", 0, obj)
    cmd.set("cartoon_loop_radius", 0.40, obj)
    cmd.cartoon("loop", obj)
    cmd.color("refgrey", obj)
    cmd.show("sticks", f"{obj} and resi 76")
    cmd.color("dark", f"{obj} and resi 76")
    cmd.set("stick_radius", 0.18, f"{obj} and resi 76")


cmd.load(REF, "ref")
style_ref("ref")
for i, (sid, n) in enumerate(PANELS):
    m = f"m{i}"
    col = "green" if i < 2 else "orange"
    cmd.load(os.path.join(MODELS, f"{sid}.pdb"), m)
    cmd.show("cartoon", m)
    cmd.hide("sticks", m)
    cmd.hide("lines", m)
    cmd.set("cartoon_nucleic_acid_mode", 3, m)
    cmd.set("cartoon_ladder_mode", 2, m)
    cmd.set("cartoon_loop_radius", 0.22, m)
    cmd.cartoon("automatic", m)
    cmd.color(col, m)
    cmd.show("sticks", f"{m} and resi {n - 2}-{n}")
    cmd.color(col, f"{m} and resi {n - 2}-{n}")
    cmd.set("stick_radius", 0.26, f"{m} and resi {n - 2}-{n}")


def ring_sel(resi, name):
    r = cmd.get_model(f"{name} and resi {resi}", 1).atom[0].resn.upper()
    return f"{name} and resi {resi} and ({PUR if r in ('A', 'G') else PYR})"


def plane_normal(sel):
    pts = np.array([a.coord for a in cmd.get_model(sel, 1).atom], float)
    _u, _s, vt = np.linalg.svd(pts - pts.mean(0))
    return vt[2] / np.linalg.norm(vt[2])


def acute(a, b):
    d = abs(float(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return math.degrees(math.acos(min(1.0, d)))


model_ns = []
for i, (sid, n) in enumerate(PANELS):
    first = plane_normal(ring_sel(n, f"m{i}"))
    for r in (n, n - 1, n - 2):
        v = plane_normal(ring_sel(r, f"m{i}"))
        model_ns.append(v if np.dot(v, first) > 0 else -v)
target = np.mean(model_ns, axis=0)
target /= np.linalg.norm(target)

arm = np.array(cmd.get_atom_coords("ref and resi 62 and name C3'")) - \
    np.array(cmd.get_atom_coords("ref and resi 76 and name C3'"))
arm /= np.linalg.norm(arm)

# camera: look down the mean model-plane normal, roll so the arm stands vertical
z = target
y = arm - np.dot(arm, z) * z
y /= np.linalg.norm(y)
x = np.cross(y, z)
M = np.vstack([x, y, z])
cmd.orient("ref")
LOCAL = " or ".join(["ref and resi 73-76"] +
                    [f"m{i} and resi {n - 3}-{n}" for i, (_s, n) in enumerate(PANELS)])
cmd.zoom(LOCAL, buffer=14, complete=1, animate=0)
v = list(cmd.get_view(0))
v[0:9] = list(M.flatten())
cmd.set_view(v)
cmd.zoom(LOCAL, buffer=14, complete=1, animate=0)
VIEW = list(cmd.get_view(0))

print("model planes vs this camera (deg from face-on):",
      " ".join(f"{acute(vv, z):.0f}" for vv in model_ns))

for i, (sid, n) in enumerate(PANELS):
    for j in range(len(PANELS)):
        (cmd.enable if j == i else cmd.disable)(f"m{j}")
    cmd.set_view(VIEW)
    cmd.hide("cartoon", "ref and not resi 62-76")
    cmd.hide("cartoon", f"m{i} and not resi {n - 12}-{n}")
    cmd.ray(SIDE, SIDE, 1, 1)
    cmd.png(os.path.join(OUT, f"faceon_{sid}_true.png"))
    cmd.show("cartoon", "ref")
    cmd.show("cartoon", f"m{i}")

# variant "par": rotate only the A76 base about its own C3'-N9 axis
c3 = np.array(cmd.get_atom_coords("ref and resi 76 and name C3'"))
n9 = np.array(cmd.get_atom_coords("ref and resi 76 and name N9"))
k = n9 - c3
k /= np.linalg.norm(k)

base_ref = f"ref and resi 76 and not ({SUGAR})"
n0 = plane_normal(base_ref)
cmd.copy("refpar_src", "ref")
cmd.save(os.path.join(OUT, "refpar_src.pdb"), "refpar_src")
cmd.delete("refpar_src")


def rot(vv, ang):
    c, s = math.cos(ang), math.sin(ang)
    return vv * c + np.cross(k, vv) * s + k * np.dot(k, vv) * (1 - c)


best = min(((acute(rot(n0, math.radians(t)), target), t) for t in range(360)))
ang = math.radians(best[1])

src = os.path.join(OUT, "refpar_src.pdb")
dst = os.path.join(OUT, "refpar.pdb")
moved = 0
with open(src) as fin, open(dst, "w") as fout:
    for line in fin:
        if (line.startswith("ATOM") and line[22:26].strip() == "76"
                and line[12:16].strip() not in
                {"P", "OP1", "OP2", "OP3", "O5'", "C5'", "C4'", "O4'",
                 "C3'", "C2'", "O2'", "C1'"}):
            p = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])]) - c3
            q = c3 + rot(p, ang)
            line = line[:30] + "".join("%8.3f" % t for t in q) + line[54:]
            moved += 1
        fout.write(line)
cmd.delete("refpar")
cmd.load(dst, "refpar")
style_ref("refpar")
n1 = plane_normal(f"refpar and resi 76 and not ({SUGAR})")
dbg = ["best theta %.1f residual %.1f" % (best[1], best[0]),
       "rotated %d base atoms" % moved,
       "normal before %s" % np.round(n0, 3),
       "normal after  %s" % np.round(n1, 3),
       "angle after vs camera %.1f" % acute(n1, z)]
with open(os.path.join(OUT, "rotate_diag.txt"), "w") as fh:
    fh.write("\n".join(dbg) + "\n")

for i, (sid, n) in enumerate(PANELS):
    for j in range(len(PANELS)):
        (cmd.enable if j == i else cmd.disable)(f"m{j}")
    cmd.disable("ref")
    cmd.enable("refpar")
    cmd.set_view(VIEW)
    cmd.hide("cartoon", "refpar and not resi 62-76")
    cmd.hide("cartoon", f"m{i} and not resi {n - 12}-{n}")
    cmd.ray(SIDE, SIDE, 1, 1)
    cmd.png(os.path.join(OUT, f"faceon_{sid}_par.png"))
    cmd.show("cartoon", "refpar")
    cmd.show("cartoon", f"m{i}")
print("previews in", OUT)
cmd.quit()
