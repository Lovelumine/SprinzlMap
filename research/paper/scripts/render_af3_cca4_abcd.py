"""Render the four A-D validation panels with the 2026-10-19 AF3 rerun.

Same composition as the original 2x2 cross-check figure (magnified 3' end over
the reference tRNA, whole-fold context circle, 2D labels), but each panel is
rendered and composed as a standalone figure.

The reference is 5UD5 chain D -- the crystal tRNA that carries a genuine mature
3'-CCA -- because that is what the paper measures against; 1EHZ puts A76 C3'
12.16 A away from 5UD5's, so it cannot be used for the distance panels.
"""
import json
import math
import os

from pymol import cmd

ST = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019"
REF = os.path.join(ST, "structures", "5ud5_chainD_superposed.pdb")
MODELS = os.path.join(ST, "structures")
OUT = os.path.join(ST, "figures_abcd_pymol_5ud5")
MK = os.path.join(OUT, "markers")

# order matches the original A-D layout; distances are C3'(last) - C3'(A76)
# measured against the 5UD5 chain-D reference, see metrics.json
PANELS = [
    ("DA1110", "true", 76, 4.75),
    ("DA0940", "true", 77, 5.46),
    ("DX1480", "false", 74, 14.46),
    ("DL2150", "false", 84, 14.56),
]
GREEN, ORANGE, GREY, DARK = "#127a42", "#e28118", "#9ba3ad", "#2c2f36"

SIDE = 3600
MAIN_BUFFER = 20
INSET_BUFFER = 14


def hexrgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


os.makedirs(MK, exist_ok=True)
cmd.reinitialize()
cmd.bg_color("white")
for k, v in {
    "ray_trace_mode": 0,
    "antialias": 3,
    "ambient": 0.50,
    "direct": 0.62,
    "specular": 0.06,
    "shininess": 70,
    "ray_shadow": 0,
    "ray_trace_fog": 0,
    "depth_cue": 0,
    "orthoscopic": 1,
    "cartoon_smooth_loops": 1,
}.items():
    cmd.set(k, v)
for name, h in [("green", GREEN), ("orange", ORANGE), ("refgrey", GREY), ("dark", DARK)]:
    cmd.set_color(name, hexrgb(h))

cmd.load(REF, "ref")
cmd.remove("ref and resname HOH")
cmd.show("cartoon", "ref")
cmd.hide("sticks", "ref")
cmd.hide("lines", "ref")
cmd.set("cartoon_nucleic_acid_mode", 0, "ref")
cmd.set("cartoon_ladder_mode", 0, "ref")
cmd.set("cartoon_loop_radius", 0.40, "ref")
cmd.cartoon("loop", "ref")
cmd.color("refgrey", "ref")
cmd.show("sticks", "ref and resi 76")
cmd.color("dark", "ref and resi 76")
cmd.set("stick_radius", 0.18, "ref and resi 76")
cmd.show("spheres", "ref and resi 76 and name C3'")
cmd.set("sphere_scale", 0.20, "ref and resi 76 and name C3'")

for i, (sid, grp, n, _d) in enumerate(PANELS):
    col = "green" if grp == "true" else "orange"
    tail = max(1, n - 2)
    m = f"m{i}"
    cmd.load(os.path.join(MODELS, f"{sid}.pdb"), m)
    cmd.show("cartoon", m)
    cmd.hide("sticks", m)
    cmd.hide("lines", m)
    cmd.set("cartoon_nucleic_acid_mode", 3, m)
    cmd.set("cartoon_ladder_mode", 2, m)
    cmd.set("cartoon_loop_radius", 0.22, m)
    cmd.cartoon("automatic", m)
    cmd.color(col, m)
    cmd.show("sticks", f"{m} and resi {tail}-{n}")
    cmd.color(col, f"{m} and resi {tail}-{n}")
    cmd.set("stick_radius", 0.26, f"{m} and resi {tail}-{n}")
    cmd.set("stick_transparency", 0, f"{m} and resi {tail}-{n}")


def anchors(i, n):
    return {
        "model_3p": f"m{i} and resi {n} and name C3'",
        "ref_3p": "ref and resi 76 and name C3'",
        "model_tail": f"m{i} and resi {max(1, n - 1)} and name C3'",
        "model_5p": f"m{i} and resi 1 and name P",
    }


cmd.ray(600, 600, 1, 1)
cmd.orient("ref")
cmd.zoom("all", buffer=MAIN_BUFFER, complete=1, animate=0)
MAIN_VIEW = list(cmd.get_view(0))

LOCAL = " or ".join(
    ["ref and resi 73-76"] +
    [f"m{i} and resi {max(1, n - 3)}-{n}" for i, (_s, _g, n, _d) in enumerate(PANELS)])
cmd.zoom(LOCAL, buffer=INSET_BUFFER, complete=1, animate=0)
# roll the camera so the acceptor arm stands vertical with the CCA at the bottom
R = list(cmd.get_view(0))[0:9]


def _proj(sel):
    x, y, z = cmd.get_atom_coords(sel)
    return (R[0] * x + R[1] * y + R[2] * z,
            R[3] * x + R[4] * y + R[5] * z)


_pt = _proj("ref and resi 62 and name C3'")
_pb = _proj("ref and resi 76 and name C3'")
_dx, _dy = _pt[0] - _pb[0], _pt[1] - _pb[1]
cmd.turn("z", math.degrees(math.atan2(_dx, _dy)))
INSET_VIEW = list(cmd.get_view(0))

spec = {"side": SIDE, "panels": []}
jobs = []
bbox_jobs = []

for i, (sid, grp, n, d376) in enumerate(PANELS):
    for j in range(len(PANELS)):
        if j != i:
            cmd.disable(f"m{j}")
    cmd.enable(f"m{i}")
    A = anchors(i, n)
    rec = {"sid": sid, "group": grp, "length": n, "d376": d376,
           "panel": f"panel_{i}_{sid}.png",
           "inset": f"inset_{i}_{sid}.png",
           "main": {}, "zoom": {}}
    cmd.set_view(MAIN_VIEW)
    cmd.ray(SIDE, SIDE, 2, 2)
    cmd.png(os.path.join(OUT, rec["panel"]))
    for key in ("model_3p", "ref_3p", "model_5p"):
        f = os.path.join(MK, f"{i}_main_{key}.png")
        rec["main"][key] = {"png": f, "side": SIDE, "xyz": list(cmd.get_atom_coords(A[key]))}
        jobs.append((MAIN_VIEW, A[key], f, SIDE))

    # magnification: acceptor-arm segment only, against the 5UD5 reference
    cmd.set_view(INSET_VIEW)
    cmd.hide("cartoon", "ref and not resi 62-76")
    cmd.hide("cartoon", f"m{i} and not resi {max(1, n - 12)}-{n}")
    cmd.ray(SIDE, SIDE, 2, 2)
    cmd.png(os.path.join(OUT, rec["inset"]))
    cmd.show("cartoon", "ref")
    cmd.show("cartoon", f"m{i}")
    for key in ("model_3p", "ref_3p", "model_tail"):
        f = os.path.join(MK, f"{i}_zoom_{key}.png")
        rec["zoom"][key] = {"png": f, "side": SIDE, "xyz": list(cmd.get_atom_coords(A[key]))}
        jobs.append((INSET_VIEW, A[key], f, SIDE))
    for key, sel in (
        ("model_tail_bbox", f"m{i} and resi {max(1, n - 2)}-{n} and name N1+C2+N3+C4+C5+C6+N7+C8+N9"),
        ("model_tail_all", f"m{i} and resi {max(1, n - 2)}-{n}"),
        ("ref_bbox", "ref and resi 76 and name N1+C2+N3+C4+C5+C6+N7+C8+N9"),
    ):
        f = os.path.join(MK, f"{i}_zoom_{key}.png")
        rec["zoom"][key] = {"png": f, "side": SIDE}
        bbox_jobs.append((INSET_VIEW, sel, f, SIDE))
    spec["panels"].append(rec)

for view, sel, path, side in jobs:
    cmd.set_view(view)
    cmd.pseudoatom("mk", pos=cmd.get_atom_coords(sel), name="MK")
    cmd.color("red", "mk")
    cmd.set("sphere_scale", 3.0, "mk")
    cmd.show("spheres", "mk")
    for o in cmd.get_names("objects"):
        if o != "mk":
            cmd.disable(o)
    cmd.ray(side, side, 1, 1)
    cmd.png(path)
    cmd.remove("mk")
    for o in cmd.get_names("objects"):
        cmd.enable(o)

for view, sel, path, side in bbox_jobs:
    cmd.set_view(view)
    cmd.create("bboxmk", sel, zoom=0)
    cmd.hide("everything", "bboxmk")
    cmd.show("spheres", "bboxmk")
    cmd.color("red", "bboxmk")
    cmd.set("sphere_scale", 0.5, "bboxmk")
    for o in cmd.get_names("objects"):
        if o != "bboxmk":
            cmd.disable(o)
    cmd.ray(side, side, 1, 1)
    cmd.png(path)
    cmd.delete("bboxmk")
    for o in cmd.get_names("objects"):
        cmd.enable(o)

with open(os.path.join(OUT, "markers.json"), "w") as fh:
    json.dump(spec, fh, indent=1)
print("wrote", len(PANELS), "panels + insets + markers", flush=True)
cmd.quit()
