"""Compare candidate inset cameras for the terminal base planes.

Sign-aligns the modelled terminal-base normals, then evaluates three camera
choices: the current one, one perpendicular to the acceptor-arm axis, and one
looking straight down the mean model-plane normal.  For each it prints how much
of the arm survives in projection and how face-on every base plane is.
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
SIDE = 1100

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

cmd.load(REF, "ref")
cmd.show("cartoon", "ref")
cmd.hide("sticks", "ref")
cmd.hide("lines", "ref")
cmd.set("cartoon_nucleic_acid_mode", 0, "ref")
cmd.set("cartoon_loop_radius", 0.40, "ref")
cmd.cartoon("loop", "ref")
cmd.color("refgrey", "ref")
cmd.show("sticks", "ref and resi 76")
cmd.color("dark", "ref and resi 76")
cmd.set("stick_radius", 0.18, "ref and resi 76")

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


def normal(resi, name):
    pts = np.array([a.coord for a in cmd.get_model(ring_sel(resi, name), 1).atom], float)
    _u, _s, vt = np.linalg.svd(pts - pts.mean(0))
    return vt[2] / np.linalg.norm(vt[2])


def acute(a, b):
    d = abs(float(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return math.degrees(math.acos(min(1.0, d)))


rn = normal(76, "ref")
planes = {"ref A76": rn}
for i, (sid, n) in enumerate(PANELS):
    for r in (n, n - 1, n - 2):
        planes[f"{sid} {r}"] = normal(r, f"m{i}")

# sign-align every model normal to the first model's last base
anchor = planes["DA1110 76"]
for k in list(planes):
    if k != "ref A76" and np.dot(planes[k], anchor) < 0:
        planes[k] = -planes[k]

arm = np.array(cmd.get_atom_coords("ref and resi 62 and name C3'")) - \
    np.array(cmd.get_atom_coords("ref and resi 76 and name C3'"))
arm /= np.linalg.norm(arm)

cmd.orient("ref")
LOCAL = " or ".join(["ref and resi 73-76"] +
                    [f"m{i} and resi {n - 3}-{n}" for i, (_s, n) in enumerate(PANELS)])
cmd.zoom(LOCAL, buffer=14, complete=1, animate=0)
R = np.array(cmd.get_view(0)[0:9]).reshape(3, 3)
cur = R[2]

model_ns = np.array([v for k, v in planes.items() if k != "ref A76"])
mean_models = model_ns.mean(0)
# camera perpendicular to the arm, rolled to the azimuth closest to the mean normal
perp = mean_models - np.dot(mean_models, arm) * arm
perp /= np.linalg.norm(perp)

cands = {"current": cur, "arm_perp": perp, "models_faceon": mean_models,
         "halfway": cur + perp}


def camera(z):
    z = z / np.linalg.norm(z)
    y = arm - np.dot(arm, z) * z
    if np.linalg.norm(y) < 1e-3:
        return None, None
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    M = np.vstack([x, y, z])
    a = M @ arm
    return z, math.hypot(a[0], a[1])


print("=== face-on angle per plane (0 = ring faces the viewer) ===")
for label, z0 in cands.items():
    z, on = camera(z0)
    if z is None:
        print(f"{label}: arm parallel to view")
        continue
    worst = max(acute(v, z) for v in planes.values())
    mods = ", ".join(f"{acute(v, z):.0f}" for k, v in planes.items() if k != "ref A76")
    print(f"{label:14s} arm on screen {on:.2f} | ref A76 {acute(rn, z):.0f} | models [{mods}] | worst {worst:.0f}")

for i, (sid, n) in enumerate(PANELS):
    if i not in (0, 2):
        continue
    for label, z0 in cands.items():
        if label == "arm_perp":
            continue
        z, _on = camera(z0)
        if z is None:
            continue
        y = arm - np.dot(arm, z) * z
        y /= np.linalg.norm(y)
        x = np.cross(y, z)
        M = np.vstack([x, y, z])
        v = list(cmd.get_view(0))
        v[0:9] = list(M.flatten())
        cmd.set_view(v)
        cmd.zoom(LOCAL, buffer=14, complete=1, animate=0)
        for j in range(len(PANELS)):
            if j != i:
                cmd.disable(f"m{j}")
        cmd.hide("cartoon", "ref and not resi 62-76")
        cmd.hide("cartoon", f"m{i} and not resi {n - 12}-{n}")
        cmd.ray(SIDE, SIDE, 1, 1)
        cmd.png(os.path.join(OUT, f"prev_{sid}_{label}.png"))
        cmd.show("cartoon", "ref")
        cmd.show("cartoon", f"m{i}")
        cmd.enable(f"m{i}")
print("previews in", OUT)
