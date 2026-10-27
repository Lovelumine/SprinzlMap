"""Render the four AF3 3'-end models with PyMOL.

Produces, per model, a full tRNA view (fixed frame) and a 3' end close-up
viewed along the axis that makes the CCA displacement visible in-plane.
"""
import json
from pathlib import Path

import numpy as np
from pymol import cmd

ROOT = Path("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019")
SRC = ROOT / "structures_oriented"
TAIL = ROOT / "structures_tailview"
OUT = ROOT / "figures_pymol"
for d in (TAIL, OUT):
    d.mkdir(exist_ok=True)

MODELS = {
    "DA1110": {"n": 76, "accent": "teal", "canonical": True},
    "DA0940": {"n": 77, "accent": "teal", "canonical": True},
    "DL2150": {"n": 84, "accent": "rust", "canonical": False},
    "DX1480": {"n": 74, "accent": "rust", "canonical": False},
}

FULL_W, FULL_H = 1200, 1500
TAIL_W = TAIL_H = 1200
FULL_CENTER = np.array([-3.0, 8.0])
FULL_BOX = (np.array([-36.0, -31.0]), np.array([30.0, 47.0]))
TAIL_CENTER = np.array([0.5, 33.5])
TAIL_BOX = (np.array([-15.0, 14.0]), np.array([16.0, 51.0]))

FULL_SCALE = FULL_H / np.linalg.norm(FULL_BOX[1] - FULL_BOX[0])
TAIL_SCALE = TAIL_H / np.linalg.norm(TAIL_BOX[1] - TAIL_BOX[0])

# reference (1EHZ) arm centres used for the fixed full-view labels
FULL_ANCHORS = {
    "acceptor arm": np.array([-0.2, 20.5, 2.1]),
    "T arm": np.array([17.9, 3.1, 2.4]),
    "D arm": np.array([-0.4, -5.3, 4.6]),
    "anticodon arm": np.array([-19.7, -19.2, -6.4]),
}

# view along -x: screen x = z, screen y = y, depth = -x
XVIEW = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])


def read_c3(path):
    atoms = {}
    for line in Path(path).read_text().splitlines():
        if line.startswith("ATOM"):
            atoms[(line[22:27].strip(), line[12:16].strip())] = np.array(
                [float(line[30:38]), float(line[38:46]), float(line[46:54])])
    return atoms


def write_transformed(src, dst):
    lines = []
    for line in Path(src).read_text().splitlines():
        if line.startswith(("ATOM", "HETATM")):
            p = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])])
            q = XVIEW @ p
            line = f"{line[:30]}{q[0]:8.3f}{q[1]:8.3f}{q[2]:8.3f}{line[54:]}"
        lines.append(line)
    Path(dst).write_text("\n".join(lines) + "\n")


def project(p, center, scale, w, h):
    return [w / 2 + (p[0] - center[0]) * scale,
            h / 2 - (p[1] - center[1]) * scale]


def setup():
    cmd.reinitialize()
    cmd.bg_color("white")
    cmd.set("ray_trace_mode", 0)
    cmd.set("antialias", 2)
    cmd.set("ambient", 0.47)
    cmd.set("direct", 0.53)
    cmd.set("specular", 0.04)
    cmd.set("shininess", 80)
    cmd.set("ray_shadow", 0)
    cmd.set("ray_trace_fog", 0)
    cmd.set("depth_cue", 0)
    cmd.set("orthoscopic", 1)
    cmd.set("cartoon_smooth_loops", 1)
    cmd.set("cartoon_nucleic_acid_mode", 2)
    cmd.set("cartoon_ladder_mode", 0)
    cmd.set("cartoon_tube_radius", 0.7)
    cmd.set("cartoon_loop_radius", 0.7)
    cmd.set("cartoon_round_helices", 1)
    cmd.set("stick_radius", 0.15)
    for name, rgb in {
        "sage": [0.66, 0.71, 0.66],
        "taupe": [0.80, 0.75, 0.68],
        "elbowgrey": [0.73, 0.72, 0.68],
        "teal": [0.13, 0.42, 0.40],
        "rust": [0.62, 0.29, 0.22],
        "ghost": [0.56, 0.62, 0.64],
        "dash": [0.43, 0.49, 0.50],
        "baseC": [0.33, 0.50, 0.58],
        "baseA": [0.78, 0.62, 0.28],
    }.items():
        cmd.set_color(name, rgb)


def full_view(sid, n, accent):
    cmd.delete("all")
    cmd.load(SRC / f"{sid}.pdb", "m")
    cmd.hide("everything", "m")
    cmd.show("cartoon", "m")
    cmd.color("sage", f"m and ((resi 1-7) or (resi 49-{n-3}))")
    cmd.color("elbowgrey", "m and ((resi 8-9) or (resi 44-48))")
    cmd.color("taupe", "m and resi 10-43")
    cmd.color(accent, f"m and resi {n-2}-{n}")

    cmd.pseudoatom("f1", pos=[FULL_BOX[0][0], FULL_BOX[0][1], 0.0])
    cmd.pseudoatom("f2", pos=[FULL_BOX[1][0], FULL_BOX[1][1], 0.0])
    cmd.hide("everything", "f1 or f2")
    cmd.viewport(FULL_W, FULL_H)
    cmd.reset()
    cmd.zoom("f1 or f2", buffer=0, complete=1)
    cmd.ray(FULL_W, FULL_H)
    cmd.png(str(OUT / f"{sid}_full.png"))
    view = list(cmd.get_view())
    cmd.delete("f1")
    cmd.delete("f2")
    return view


def tail_view(sid, n, accent):
    cmd.delete("all")
    cmd.load(TAIL / "1ehz.pdb", "ref")
    cmd.hide("everything", "ref")
    cmd.show("sticks", "ref and resi 76")
    cmd.set("stick_radius", 0.14, "ref and resi 76")
    cmd.color("ghost", "ref and resi 76")
    cmd.show("spheres", "ref and resi 76 and name C3'")
    cmd.set("sphere_scale", 0.32, "ref and resi 76 and name C3'")

    cmd.load(TAIL / f"{sid}.pdb", "m")
    cmd.hide("everything", "m")
    cmd.show("cartoon", f"m and resi {n-10}-{n-3}")
    cmd.color("sage", f"m and resi {n-10}-{n-3}")
    cmd.create("tail", f"m and resi {n-2}-{n}")
    cmd.hide("everything", "tail")
    cmd.show("sticks", "tail")
    cmd.set("stick_radius", 0.18, "tail")
    cmd.color("baseC", "tail and resn C")
    cmd.color("baseA", "tail and resn A")
    cmd.show("spheres", f"m and resi {n} and name C3'")
    cmd.set("sphere_scale", 0.32, f"m and resi {n} and name C3'")
    cmd.color(accent, f"m and resi {n} and name C3'")

    cmd.distance("d", f"m and resi {n} and name C3'", "ref and resi 76 and name C3'")
    cmd.hide("labels", "d")
    cmd.set("dash_radius", 0.055, "d")
    cmd.set("dash_gap", 0.35, "d")
    cmd.set("dash_length", 0.32, "d")
    cmd.color("dash", "d")

    cmd.pseudoatom("f1", pos=[TAIL_BOX[0][0], TAIL_BOX[0][1], 0.0])
    cmd.pseudoatom("f2", pos=[TAIL_BOX[1][0], TAIL_BOX[1][1], 0.0])
    cmd.hide("everything", "f1 or f2")
    cmd.viewport(TAIL_W, TAIL_H)
    cmd.reset()
    cmd.zoom("f1 or f2", buffer=0, complete=1)
    cmd.ray(TAIL_W, TAIL_H)
    cmd.png(str(OUT / f"{sid}_tail.png"))
    view = list(cmd.get_view())
    cmd.delete("f1")
    cmd.delete("f2")
    return view


def main():
    setup()
    for sid in MODELS:
        for name in ("1ehz", sid):
            if not (TAIL / f"{name}.pdb").exists():
                write_transformed(SRC / f"{name}.pdb", TAIL / f"{name}.pdb")

    meta = {
        "full": {"w": FULL_W, "h": FULL_H, "center": FULL_CENTER.tolist(),
                 "scale": FULL_SCALE, "anchors": {k: v.tolist() for k, v in FULL_ANCHORS.items()}},
        "tail": {"w": TAIL_W, "h": TAIL_H, "center": TAIL_CENTER.tolist(),
                 "scale": TAIL_SCALE},
        "models": {},
    }
    for sid, info in MODELS.items():
        n = info["n"]
        c3 = read_c3(SRC / f"{sid}.pdb")
        term = c3[(str(n), "C3'")]
        ghost = c3_ref = read_c3(SRC / "1ehz.pdb")[("76", "C3'")]
        vfull = full_view(sid, n, info["accent"])
        vtail = tail_view(sid, n, info["accent"])
        meta["models"][sid] = {
            "n": n, "accent": info["accent"], "canonical": info["canonical"],
            "full_view": vfull, "tail_view": vtail,
            "tail_anchor": project(term, FULL_CENTER, FULL_SCALE, FULL_W, FULL_H),
            "tail_screen": project(XVIEW @ term, TAIL_CENTER, TAIL_SCALE, TAIL_W, TAIL_H),
            "ghost_screen": project(XVIEW @ ghost, TAIL_CENTER, TAIL_SCALE, TAIL_W, TAIL_H),
            "distance": float(np.linalg.norm(term - ghost)),
        }
        print(sid, "rendered", meta["models"][sid]["distance"])
    (OUT / "render_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    cmd.quit()


main()
