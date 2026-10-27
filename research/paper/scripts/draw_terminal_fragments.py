"""Compare the modelled and the crystal 3'-terminal trinucleotide base by base.

The two fragments keep their superposed positions, but each base is drawn in its
own row, viewed straight down its ring-plane normal, so the rings cannot hide
one another: a purine reads as a fused double ring, a pyrimidine as a single
ring, in both the model and the 5UD5 D reference. Dotted lines join the three
corresponding positions and carry their true 3D centroid-to-centroid distance.
"""

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors as mcolors

REF_COLOR = "#526d86"
PURINE = ["N9", "C8", "N7", "C5", "C6", "N1", "C2", "N3", "C4"]
GLYCOSIDIC = {"A": "N9", "G": "N9", "C": "N1", "U": "N1"}
ROW = 8.6
PANEL_GAP = 11.0


def ring_groups(residue):
    """Split the base perimeter into its rings: two fused for a purine, one for a pyrimidine."""
    ring = list(residue["ring"])
    names = [residue["atoms"][index]["n"] for index in ring]
    if names == PURINE:
        return [[ring[k] for k in (0, 1, 2, 3, 8)], [ring[k] for k in (3, 4, 5, 6, 7, 8)]], True
    return [ring], False


def signed_area(points):
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def base_frame(residue):
    """In-plane basis: longest ring axis horizontal, glycosidic nitrogen below it."""
    perimeter = np.array([residue["atoms"][i]["p"] for i in residue["ring"]])
    centre = perimeter.mean(axis=0)
    normal = np.array(residue["plane"]["normal"], dtype=float)
    normal /= np.linalg.norm(normal)

    first = perimeter[0] - centre
    e1 = first - normal * np.dot(first, normal)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(normal, e1)
    flat = np.stack([(perimeter - centre) @ e1, (perimeter - centre) @ e2], axis=1)
    if signed_area(flat) < 0:
        e2 = -e2
        flat[:, 1] *= -1

    angles = np.radians(np.arange(0, 180, 0.5))
    spread = [np.ptp(flat @ np.array([np.cos(t), np.sin(t)]), axis=0).max() for t in angles]
    long_axis = angles[int(np.argmax(spread))]
    rotation = np.array([[np.cos(long_axis), np.sin(long_axis)],
                         [-np.sin(long_axis), np.cos(long_axis)]])
    basis = np.stack([e1, e2], axis=1) @ rotation.T

    index = next(i for i, atom in enumerate(residue["atoms"])
                 if atom["n"] == GLYCOSIDIC[residue["name"]])
    if ((np.array(residue["atoms"][index]["p"]) - centre) @ basis)[1] > 0:
        basis = -basis
    return basis, centre


def draw_base(panel, residue, origin, color):
    """One base in its own face-on frame; returns its half width."""
    basis, centre = base_frame(residue)
    points = (np.array([atom["p"] for atom in residue["atoms"]]) - centre) @ basis + origin
    groups, purine = ring_groups(residue)
    rings = [(np.array([residue["atoms"][i]["p"] for i in group]) - centre) @ basis + origin
             for group in groups]
    base_atoms = [i for i, atom in enumerate(residue["atoms"])
                  if atom["cls"] in ("ring", "exocyclic")]

    for start, end in residue["bonds"]:
        if not all(i in base_atoms for i in (start, end)):
            continue
        coords = points[[start, end]]
        panel.plot(coords[:, 0], coords[:, 1], color=color, linewidth=3.4,
                   solid_capstyle="round", zorder=4)
    for poly in rings:
        panel.fill(poly[:, 0], poly[:, 1], color=color, alpha=0.13, zorder=2)

    glycosidic = next(i for i, atom in enumerate(residue["atoms"])
                      if atom["n"] == GLYCOSIDIC[residue["name"]])
    sugar = next(i for i, atom in enumerate(residue["atoms"]) if atom["n"] == "C1'")
    stub = points[[glycosidic, sugar]]
    direction = stub[1] - stub[0]
    panel.plot([stub[0][0], stub[0][0] + direction[0] * 0.45],
               [stub[0][1], stub[0][1] + direction[1] * 0.45],
               color=mcolors.to_rgba(color, 0.45), linewidth=1.8, solid_capstyle="round", zorder=3)

    return float(np.abs(points[base_atoms] - origin).max(axis=0)[0])


def triplet(tail):
    return "".join(residue["name"] for residue in tail)


def main():
    site = Path(__file__).resolve().parents[1] / "site_cca3"
    scene = json.loads((site / "data/scene.json").read_text())
    ref_tail = scene["reference"]["chain"][-3:]
    for sid in (sys.argv[1:] or [model["id"] for model in scene["models"]]):
        model = next(m for m in scene["models"] if m["id"] == sid)
        tails = [model["chain"][-3:], ref_tail]
        colours = [model["color"], REF_COLOR]

        figure, panel = plt.subplots(figsize=(11, 6.9))
        figure.subplots_adjust(left=0.03, right=0.97, top=0.845, bottom=0.115)
        panel.axis("off")

        columns = [-PANEL_GAP / 2 - 6.0, PANEL_GAP / 2 + 6.0]
        widest = 0.0
        for column, (tail, color) in enumerate(zip(tails, colours)):
            centre_x = columns[column]
            for row, residue in enumerate(tail):
                origin = np.array([centre_x, (1 - row) * ROW])
                half = draw_base(panel, residue, origin, color)
                widest = max(widest, half)
                _, purine = ring_groups(residue)
                side = -1 if column == 0 else 1
                panel.text(centre_x + side * (half + 1.6), origin[1] + 2.4,
                           f'{residue["name"]}{residue["resi"]}',
                           ha="right" if side < 0 else "left", va="center", fontsize=14,
                           weight="bold", color=color, zorder=6)
                panel.text(centre_x + side * (half + 1.6), origin[1] + 0.3,
                           "purine · 2 fused rings" if purine else "pyrimidine · 1 ring",
                           ha="right" if side < 0 else "left", va="center",
                           fontsize=9.5, color=color, zorder=6)

        edge = columns[0] + widest + 1.4
        for row, (a, b) in enumerate(zip(tails[0], tails[1])):
            truth = np.linalg.norm(np.array(a["plane"]["centroid"]) - np.array(b["plane"]["centroid"]))
            y = (1 - row) * ROW
            panel.plot([edge, -edge], [y, y], color="#98a2ae", linewidth=1.0,
                       linestyle=(0, (1, 3)), zorder=0)
            panel.text(0.0, y, f"{truth:.1f} \u00c5", ha="center", va="center", fontsize=11,
                       color="#41505f", bbox={"facecolor": "white", "edgecolor": "none"}, zorder=5)

        panel.set(xlim=(-25, 25), ylim=(-13.0, 13.0), aspect="equal")

        figure.text(0.5, 0.975, f"{sid} \u00b7 3\u2032 terminal comparison", ha="center", va="top",
                    fontsize=19, color="#263342", weight="bold")
        figure.text(0.27, 0.905, f"{sid} \u00b7 {triplet(tails[0])}", ha="center",
                    color=model["color"], fontsize=14, weight="bold")
        figure.text(0.73, 0.905, f"5UD5 D \u00b7 {triplet(tails[1])}", ha="center",
                    color=REF_COLOR, fontsize=14, weight="bold")
        figure.text(0.5, 0.026, "Rows are the three 3\u2032 positions, 5\u2032 at the top; every base is drawn "
                    "face-on along its own ring-plane normal\nDotted lines join corresponding positions "
                    "and carry their true 3D centroid-to-centroid distance",
                    ha="center", va="bottom", fontsize=9.5, color="#657080")

        output = site / "figures"
        output.mkdir(exist_ok=True)
        for extension in ("png", "svg", "pdf"):
            path = output / f"{sid}_terminal_fragments_v3.{extension}"
            figure.savefig(path, dpi=300, facecolor="white")
            print(path)
        plt.close(figure)


if __name__ == "__main__":
    main()
