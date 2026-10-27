"""PyMOL renderer for the AF3 tRNA 3'-CCA cross-check figure.

Run INSIDE PyMOL:  AF3_SIDE=1400 xvfb-run -a pymol -cg -i render_af3_pymol.py

Emits, for each of the four sequences, a full-length panel and a magnified 3'
inset under locked shared cameras, plus isolated single-sphere "marker" renders
whose blob centroids give the composer the screen position of specific atoms.
Nothing is annotated here -- the composer draws the solid connector and every
label in 2D, where they can never be occluded by the molecule.
"""
import json
import os

from pymol import cmd

ST = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca13"
WEB = os.path.join(ST, "webview", "structures")
OUT = os.path.join(ST, "figures_pymol")
MK = os.path.join(OUT, "markers")

# sid -> (group, model length, C3' 3'-end -> 1EHZ A76 C3' distance, A)
PANELS = [
    ("DA1110", "true", 76, 8.56),
    ("DA0940", "true", 77, 6.92),
    ("DX1480", "false", 74, 18.28),
    ("DL2150", "false", 84, 18.51),
]
GREEN, ORANGE, GREY, DARK = "#127a42", "#e28118", "#9ba3ad", "#2c2f36"

SIDE = int(os.environ.get("AF3_SIDE", 1400))
TAG = os.environ.get("AF3_TAG", "")
MAIN_BUFFER = float(os.environ.get("AF3_MAIN_BUFFER", "20"))
INSET_BUFFER = float(os.environ.get("AF3_INSET_BUFFER", "14"))
REF_MODE = int(os.environ.get("AF3_REF_MODE", 0))
MDL_MODE = int(os.environ.get("AF3_MDL_MODE", 3))
REF_STYLE = os.environ.get("AF3_REF_STYLE", "loop")
MDL_STYLE = os.environ.get("AF3_MDL_STYLE", "automatic")
REF_RADIUS = float(os.environ.get("AF3_REF_RADIUS", 0.52))
MDL_RADIUS = float(os.environ.get("AF3_MDL_RADIUS", 0.3))
MDL_LADDER = int(os.environ.get("AF3_MDL_LADDER", 2))


def hexrgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


os.makedirs(MK, exist_ok=True)
cmd.reinitialize()
cmd.bg_color("white")
for k, v in {
    "ray_trace_mode": 1, "antialias": 3, "ambient": 0.55, "direct": 0.85,
    "specular": 0.25, "cartoon_smooth_loops": 1, "ray_shadow": 0,
    "ray_trace_fog": 0, "depth_cue": 0,
}.items():
    cmd.set(k, v)
for name, h in [("green", GREEN), ("orange", ORANGE), ("refgrey", GREY), ("dark", DARK)]:
    cmd.set_color(name, hexrgb(h))

cmd.load(os.path.join(WEB, "1ehz.pdb"), "ref")
cmd.show("cartoon", "ref")
cmd.hide("sticks", "ref")
cmd.hide("lines", "ref")
cmd.set("cartoon_nucleic_acid_mode", REF_MODE, "ref")
cmd.set("cartoon_ladder_mode", 0, "ref")
cmd.set("cartoon_loop_radius", REF_RADIUS, "ref")
cmd.cartoon(REF_STYLE, "ref")
cmd.color("refgrey", "ref")
# the crystal's own 3' terminus, drawn dark so it reads as the target
cmd.show("sticks", "ref and resi 76")
cmd.color("dark", "ref and resi 76")
cmd.set("stick_radius", 0.24, "ref and resi 76")

for i, (sid, grp, n, _d) in enumerate(PANELS):
    col = "green" if grp == "true" else "orange"
    tail = max(1, n - 2)
    m = f"m{i}"
    cmd.load(os.path.join(WEB, f"{sid}_superposed.pdb"), m)
    cmd.show("cartoon", m)
    cmd.hide("sticks", m)
    cmd.hide("lines", m)
    cmd.set("cartoon_nucleic_acid_mode", MDL_MODE, m)
    cmd.set("cartoon_ladder_mode", MDL_LADDER, m)
    cmd.set("cartoon_loop_radius", MDL_RADIUS, m)
    cmd.cartoon(MDL_STYLE, m)
    cmd.color(col, m)
    # the terminal 3 nt are the tail under test: sticks, spheres on the last one
    cmd.show("sticks", f"{m} and resi {tail}-{n}")
    cmd.color(col, f"{m} and resi {tail}-{n}")
    cmd.set("stick_radius", 0.26, f"{m} and resi {tail}-{n}")
    cmd.show("spheres", f"{m} and resi {n}")
    cmd.set("sphere_scale", 0.72, f"{m} and resi {n}")


def anchors(i, n):
    return {
        "model_3p": f"m{i} and resi {n} and name C3'",
        "ref_3p": "ref and resi 76 and name C3'",
        "model_tail": f"m{i} and resi {max(1, n - 1)} and name C3'",
        "model_5p": f"m{i} and resi 1 and name P",
    }


# Frame with zoom's own buffer (hand-scaling the view matrix corrupts the clip
# planes).  Everything is rendered at one square size so a saved view always
# restores to the same picture.
cmd.ray(SIDE, SIDE, 1, 1)
cmd.orient("ref")
cmd.zoom("all", buffer=MAIN_BUFFER, complete=1, animate=0)
MAIN_VIEW = list(cmd.get_view(0))

# One shared inset camera for all four 3' ends: the models are superposed on the
# same core, so a single framing keeps the magnification comparable.
LOCAL = " or ".join(
    ["ref and resi 73-76"] +
    [f"m{i} and resi {max(1, n - 3)}-{n}" for i, (_s, _g, n, _d) in enumerate(PANELS)])
cmd.zoom(LOCAL, buffer=INSET_BUFFER, complete=1, animate=0)
INSET_VIEW = list(cmd.get_view(0))

spec = {"side": SIDE, "panels": []}
jobs = []

for i, (sid, grp, n, d376) in enumerate(PANELS):
    for j in range(len(PANELS)):
        if j != i:
            cmd.disable(f"m{j}")
    cmd.enable(f"m{i}")
    A = anchors(i, n)
    rec = {"sid": sid, "group": grp, "length": n, "d376": d376,
           "panel": f"panel_{i}_{sid}{TAG}.png",
           "inset": f"inset_{i}_{sid}{TAG}.png",
           "main": {}, "zoom": {}}
    cmd.set_view(MAIN_VIEW)
    cmd.ray(SIDE, SIDE, 3, 3)
    cmd.png(os.path.join(OUT, rec["panel"]))
    for key in ("model_3p", "ref_3p", "model_5p"):
        f = os.path.join(MK, f"{i}_main_{key}{TAG}.png")
        s = SIDE
        rec["main"][key] = {"png": f, "side": s, "xyz": list(cmd.get_atom_coords(A[key]))}
        jobs.append((MAIN_VIEW, A[key], f, s))

    # the inset is a magnification of the acceptor end only -- dropping the rest of
    # the cartoon keeps all four panels at one readable scale
    cmd.set_view(INSET_VIEW)
    cmd.hide("cartoon", "ref and not resi 62-76")
    cmd.hide("cartoon", f"m{i} and not resi {max(1, n - 12)}-{n}")
    cmd.ray(SIDE, SIDE, 3, 3)
    cmd.png(os.path.join(OUT, rec["inset"]))
    cmd.show("cartoon", "ref")
    cmd.show("cartoon", f"m{i}")
    for key in ("model_3p", "ref_3p", "model_tail"):
        f = os.path.join(MK, f"{i}_zoom_{key}{TAG}.png")
        s = SIDE
        rec["zoom"][key] = {"png": f, "side": s, "xyz": list(cmd.get_atom_coords(A[key]))}
        jobs.append((INSET_VIEW, A[key], f, s))
    spec["panels"].append(rec)

# markers after the panels, so a bad marker render never costs the images
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

with open(os.path.join(OUT, f"markers{TAG}.json"), "w") as fh:
    json.dump(spec, fh, indent=1)
print("wrote", len(PANELS), "panels + insets + markers", flush=True)
cmd.quit()
