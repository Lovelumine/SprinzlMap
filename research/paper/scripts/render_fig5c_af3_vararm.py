#!/usr/bin/env python3
"""Render Figure 5C AF3 variable-arm structures with one shared fit and camera.

Run inside the PyMOL Python environment on the AF3 host, for example:

  LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6 \
    ~/.venvs/pymol/bin/python \
    research/paper/scripts/render_fig5c_af3_vararm.py

The input root defaults to the dated AF3 workspace used for the manuscript.
Override it with AF3_VARARM_ROOT when reproducing the render elsewhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pymol import cmd


ROOT = Path(
    os.environ.get(
        "AF3_VARARM_ROOT",
        "/mnt/raid/alphafold3",
    )
)
JOB = os.environ.get("AF3_VARARM_JOB", "fig5c_v19_v14_20261027")
INPUTS = ROOT / "inputs" / JOB
MODELS = ROOT / "outputs" / JOB
OUTPUT = Path(os.environ.get("AF3_VARARM_OUTPUT", str(MODELS / "renders")))
OUTPUT.mkdir(parents=True, exist_ok=True)

SPECS = {
    "V19": {
        "object": "v19",
        "cif": MODELS / "varV19" / "varV19_model.cif",
        "classes": INPUTS / "varV19_classes.json",
    },
    "V14": {
        "object": "v14",
        "cif": MODELS / "varV14" / "varV14_model.cif",
        "classes": INPUTS / "varV14_classes.json",
    },
}

COLORS = {
    "v_stem": "0x2E7D32",
    "v_loop": "0xC8924B",
    "t_region": "0x2563A6",
}


def residue_selection(name: str, residues: list[str]) -> str:
    return f"{name} and chain A and resi {'+'.join(residues)}"


cmd.reinitialize()
for spec in SPECS.values():
    name = spec["object"]
    spec["data"] = json.loads(spec["classes"].read_text())
    cmd.load(str(spec["cif"]), name)
    cmd.remove(f"{name} and solvent")
    cmd.hide("everything", name)
    cmd.show("cartoon", name)
    cmd.color("grey78", name)
    for region, color in COLORS.items():
        selection = f"{name}_{region}"
        cmd.select(selection, residue_selection(name, spec["data"][region]))
        cmd.color(color, selection)

# Align V14 to V19 using C3' atoms from shared, non-variable core positions.
v19_map = SPECS["V19"]["data"]["label_to_residue"]
v14_map = SPECS["V14"]["data"]["label_to_residue"]
excluded = {"-1", "46", "47", "48", "49", "50", "51", "52", "53"}
common = [
    label
    for label in v19_map
    if label in v14_map and not label.startswith("V") and label not in excluded
]
v14_core = "+".join(str(v14_map[label]) for label in common)
v19_core = "+".join(str(v19_map[label]) for label in common)
cmd.align(
    f"v14 and chain A and resi {v14_core} and name C3'",
    f"v19 and chain A and resi {v19_core} and name C3'",
    cycles=0,
    transform=1,
)

cmd.set("cartoon_nucleic_acid_mode", 2)
cmd.set("cartoon_tube_radius", 0.46)
cmd.set("cartoon_loop_radius", 0.33)
cmd.set("cartoon_ladder_radius", 0.055)
cmd.set("ray_opaque_background", 0)
cmd.set("ray_trace_fog", 0)
cmd.set("depth_cue", 0)
cmd.set("antialias", 2)
cmd.set("orthoscopic", 1)
cmd.bg_color("white")

cmd.orient("v19 or v14")
cmd.turn("x", -20)
cmd.turn("y", 20)
cmd.turn("z", 10)
cmd.zoom("v19 or v14", 1.20)
shared_view = cmd.get_view()

for label, spec in SPECS.items():
    cmd.disable("all")
    cmd.enable(spec["object"])
    cmd.set_view(shared_view)
    raw = OUTPUT / f"{label}_raw.png"
    cmd.png(str(raw), width=1500, height=1200, dpi=300, ray=1)

(OUTPUT / "render_manifest.json").write_text(
    json.dumps(
        {
            "job": JOB,
            "shared_core_labels": common,
            "raw_size_pixels": [1500, 1200],
            "colors": COLORS,
            "outputs": ["V19_raw.png", "V14_raw.png"],
        },
        indent=2,
    )
    + "\n"
)
print(f"rendered {OUTPUT}")
