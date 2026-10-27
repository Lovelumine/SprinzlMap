#!/usr/bin/env python3
"""Reflow the four delivered terminal panels into landscape tiles for Figure 4.

The delivered artwork (`figure_cca/individual_compact`) is four portrait
figures, 215 x 336 bp each. Placed unchanged in the 2 x 2 grid of a 180 mm-wide
figure they would either be reduced to about 42 mm per panel or push Figure 4
past one page, so this script reflows them:

* whole-structure inset moved from above the close-up to its left;
* per-panel title removed (letter, identifier are set by the compose step);
* every panel drawn on an identical canvas and cropped to one common bounding
  box, so the four panels keep exactly the same scale.

Structures, projection, colours and data limits are taken unchanged from
`render_cca_v2.py`, which must sit next to this script together with a sibling
`reference_aligned/structures` directory (the AF3 workspace layout). Only the
axes rectangles, the figure size and the crop are new.

    python scripts/render_individual_row.py \
        --source <af3 workspace>/figure_cca \
        --out research/paper/results/af3_cca4_20261019/figures_individual_row
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

IDS = ["DA1110", "DA0940", "DL2150", "DX1480"]
FIG_W, FIG_H = 4.50, 3.05
CROP_PAD = 8  # pixels of white margin kept around the common bounding box


def render_batch(source_dir: Path, json_dir: Path, raw_dir: Path) -> list[Path]:
    source = (source_dir / "render_cca_v2.py").read_text(encoding="utf-8-sig").split("fig=plt.figure(")[0]
    namespace = {"__file__": str((source_dir / "render_cca_v2.py").resolve())}
    exec(compile(source, "structure_helpers", "exec"), namespace)
    globals().update(namespace)

    from matplotlib import font_manager
    font_manager.fontManager.addfont("C:/Windows/Fonts/times.ttf")
    font_manager.fontManager.addfont("C:/Windows/Fonts/timesbd.ttf")
    plt.rcParams.update({"font.family": "Times New Roman", "font.serif": ["Times New Roman"]})

    closepts = p(points)[:, :2]
    left, right = closepts[:, 0].min(), closepts[:, 0].max()
    bottom, top = closepts[:, 1].min(), closepts[:, 1].max()
    newlims = np.array([[left - 5, right + 5], [bottom - 2, top + 2]])
    written = []
    for k, c in zip(IDS, COLORS):
        r = models[k]
        nums = list(r)[-3:]
        fig = plt.figure(figsize=(FIG_W, FIG_H))
        small = fig.add_axes([.005, .605, .245, .361])
        main = fig.add_axes([.245, .015, .620, .914])
        trace(small, ref, GRAY, .8, .65, smooth=True)
        trace(small, r, c, .85, 1, smooth=True)
        trace(small, r, c, 1.6, 1, nums, smooth=True)
        small.add_patch(Rectangle(mn - 2, *(mx - mn + 4), fill=False, edgecolor=GRAY, lw=.6, linestyle=(0, (3, 3))))
        small.set_xlim(wct[0] - wspan / 2, wct[0] + wspan / 2)
        small.set_ylim(wct[1] - wspan / 2, wct[1] + wspan / 2)
        small.set_aspect("equal")
        small.axis("off")
        trace(main, ref, GRAY, .7, .28, [71, 72, 73, 74])
        sticks(main, ref, [74, 75, 76], GRAY, 1, .6)
        sticks(main, r, nums, c, 1.65, 1)
        main.set_xlim(*newlims[0])
        main.set_ylim(*newlims[1])
        main.set_aspect("equal")
        main.axis("off")
        # Model labels on the right, reference labels on the left. Anchors are
        # pushed apart so the leader lines never cross.
        for rr, nn, cc, side in [(r, nums, c, 1), (ref, [74, 75, 76], GRAY, -1)]:
            ordered = sorted(nn, key=lambda n: center(rr, n)[1])
            ys = np.array([center(rr, n)[1] for n in ordered])
            for t in range(1, len(ys)):
                ys[t] = max(ys[t], ys[t - 1] + 4.8)
            ys -= max(0, ys[-1] - (lims[1, 1] - 4))
            grouppts = p([a for n in nn for a in rr[n]["atoms"].values()])[:, :2]
            x = grouppts[:, 0].max() + 2.5 if side == 1 else grouppts[:, 0].min() - 2.5
            for n, y in zip(ordered, ys):
                main.annotate(rr[n]["name"], xy=center(rr, n), xytext=(x, y),
                              ha="left" if side == 1 else "right", va="center",
                              fontsize=11, color=cc,
                              arrowprops={"arrowstyle": "-", "color": cc, "lw": .65,
                                          "shrinkA": 3, "shrinkB": 2})
        require_matplotlib_panel_alignment(
            fig, axes=[main], exclude_axes=[small], panel_ids=["terminal"],
            json_out=json_dir / f"{k}.alignment.json", strict=True)
        target = raw_dir / f"{k}.raw.png"
        fig.savefig(target, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=.02)
        plt.close(fig)
        written.append(target)
    return written


def crop_common(raw: list[Path], out_dir: Path) -> None:
    """Crop every panel to the union of their ink boxes, keeping one scale."""
    boxes = []
    for path in raw:
        mask = (np.array(Image.open(path).convert("RGB")) < 250).any(axis=2)
        ys, xs = np.where(mask)
        boxes.append((xs.min(), ys.min(), xs.max(), ys.max()))
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
           max(b[2] for b in boxes), max(b[3] for b in boxes))
    box = (max(0, box[0] - CROP_PAD), max(0, box[1] - CROP_PAD),
           box[2] + CROP_PAD, box[3] + CROP_PAD)
    for path in raw:
        tile = Image.open(path).convert("RGB").crop(box)
        tile.save(out_dir / path.name.replace(".raw.png", ".png"), dpi=(300, 300))
        path.unlink()
    print(f"cropped tiles {tile.size[0]} x {tile.size[1]} px, aspect {tile.size[0] / tile.size[1]:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="directory holding render_cca_v2.py, with ../reference_aligned/structures")
    parser.add_argument("--out", type=Path, required=True,
                        help="destination for the four cropped tiles")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="af3-row-") as temp:
        raw = render_batch(args.source, args.out, Path(temp))
        crop_common(raw, args.out)
    for sid in IDS:
        print(args.out / f"{sid}.png")


if __name__ == "__main__":
    main()
