#!/usr/bin/env python3
"""Recompose retained artwork without rerunning alignment or structure fitting.

Uses pdflatex/TikZ to preserve the original PDF alignment panels as vectors.
PNG structure renders are embedded unchanged, with outer whitespace clipped.
Panel headings are overlaid to match the new grouping and typography.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


PAPER = Path(__file__).resolve().parents[1]
FIGURES = PAPER / "figures"
MSA = FIGURES / "fig05_msa_examples.pdf"
AF3 = PAPER / "results/af3_cca4_20261019/figures_abcd"
# Landscape terminal panels reflowed from figure_cca/individual_compact for the
# 2x2 grid; produced by render_individual_row.py and cropped to a common box so
# every panel keeps the same scale.
AF3_ROW = PAPER / "results/af3_cca4_20261019/figures_individual_row"
AF3_COMPACT_V = PAPER / "results/af3_cca4_20261019/figures_individual_compact"
EXPERIMENTAL_VARARM = PAPER / "results/fig5c_pilot"
ROW_ASPECT = 1.2993865030674847  # width / height of the common cropped tile
SOURCE_WIDTH = 377.236800
SOURCE_HEIGHT = 438.761828
WIDTH = 510.0  # approximately 180 mm before manuscript scaling
REPO_TMP = PAPER.parents[1] / "tmp"


def pdf_panel(
    y0: float, y1: float, top: float, letter: str, *, compact: bool = False,
) -> str:
    """Place vector artwork, optionally removing its empty label/sequence gap."""
    # The 108--157 bp band in source panel A contains no text or highlights.
    # Removing it increases the alignment font size without redrawing any data.
    intervals = [(0.0, 108.0), (157.0, SOURCE_WIDTH)] if compact else [(0.0, SOURCE_WIDTH)]
    scale = WIDTH / sum(x1 - x0 for x0, x1 in intervals)
    parts = []
    dest_x = 0.0
    for x0, x1 in intervals:
        width = (x1 - x0) * scale
        parts.append(rf"""
\node[anchor=north west,inner sep=0] at ({dest_x:.6f},{top:.6f}) {{%
  \includegraphics[width={width:.6f}bp,clip,
    trim={x0:.6f}bp {SOURCE_HEIGHT-y1:.6f}bp {SOURCE_WIDTH-x1:.6f}bp {y0:.6f}bp]{{{MSA.as_posix()}}}}};
""")
        dest_x += width
    parts.append(rf"""
\fill[white] (0,{top-17:.6f}) rectangle (16,{top:.6f});
\node[anchor=north west,inner sep=0,font=\sffamily\bfseries\fontsize{{11}}{{12}}\selectfont]
  at (1.5,{top-1.5:.6f}) {{{letter}}};
""")
    return "".join(parts)


def structure_panel(sid: str, original: str, letter: str, x: float, top: float) -> str:
    # Original PNGs are 3120 x 3160 px at 300 dpi. These fixed clips retain
    # all four panels' content and use an identical scale in every panel.
    width = 220.0
    source = AF3 / f"fig_af3_cca_{original}_{sid}_v4.png"
    return rf"""
\node[anchor=north west,inner sep=0] at ({x},{top:.6f}) {{%
  \includegraphics[width={width}bp,clip,
    trim=33.6bp 177.6bp 33.6bp 7.2bp]{{{source.as_posix()}}}}};
\fill[white] ({x},{top-14:.6f}) rectangle ({x+100},{top:.6f});
\node[anchor=base west,inner sep=0,font=\sffamily\bfseries\fontsize{{11}}{{12}}\selectfont]
  at ({x+1.5},{top-11.5:.6f}) {{{letter}}};
\node[anchor=base west,inner sep=0,font=\sffamily\bfseries\fontsize{{9}}{{10}}\selectfont]
  at ({x+15},{top-11.5:.6f}) {{{sid}}};
"""


def cca_layout() -> tuple[str, float]:
    alignment_height = 188.0 * WIDTH / (SOURCE_WIDTH - 49.0)
    structure_height = 220.0 * 2390.0 / 2840.0
    height = alignment_height + 8.0 + 2.0 * structure_height + 6.0
    top = height - alignment_height - 8.0
    parts = [pdf_panel(0.0, 188.0, height, "A", compact=True)]
    for sid, original, letter, x, y in (
        ("DA1110", "A", "B", 25.0, top),
        ("DA0940", "B", "C", 265.0, top),
        ("DX1480", "C", "D", 25.0, top - structure_height - 6.0),
        ("DL2150", "D", "E", 265.0, top - structure_height - 6.0),
    ):
        parts.append(structure_panel(sid, original, letter, x, y))
    return "".join(parts), height


def structure_panel_row(sid: str, letter: str, x: float, top: float) -> str:
    """Landscape tile: whole-structure inset left, terminal close-up right.

    Distances are deliberately not printed inside the panels; the four
    C3'--C3' values are reported in the text and the caption only.
    """
    width = 220.0
    height = width / ROW_ASPECT
    source = AF3_ROW / f"{sid}.png"
    return rf"""
\node[anchor=north west,inner sep=0] at ({x},{top - 13:.6f}) {{%
  \includegraphics[width={width}bp,height={height:.6f}bp]{{{source.as_posix()}}}}};
\node[anchor=north west,inner sep=0,font=\sffamily\bfseries\fontsize{{11}}{{12}}\selectfont]
  at ({x},{top:.6f}) {{{letter}}};
\node[anchor=north west,inner sep=0,font=\sffamily\bfseries\fontsize{{9}}{{10}}\selectfont]
  at ({x + 13},{top - 1:.6f}) {{{sid}}};
"""


def structure_panel_compact_v(sid: str, letter: str, x: float, top: float) -> str:
    """Vertical compact tile: the delivered individual_compact PDF (small whole-structure
    inset above, large terminal panel below), used unchanged as vector art.
    """
    width = 235.0
    height = width * 336.473863 / 215.240065
    source = AF3_COMPACT_V / f"{sid}.pdf"
    return rf"""
\node[anchor=north west,inner sep=0] at ({x},{top:.6f}) {{%
  \includegraphics[width={width}bp,height={height:.6f}bp]{{{source.as_posix()}}}}};
\node[anchor=north west,inner sep=0,font=\sffamily\bfseries\fontsize{{11}}{{12}}\selectfont]
  at ({x},{top:.6f}) {{{letter}}};
\node[anchor=north west,inner sep=0,font=\sffamily\bfseries\fontsize{{9}}{{10}}\selectfont]
  at ({x + 13},{top - 1:.6f}) {{{sid}}};
"""


def cca_compact_v_layout() -> tuple[str, float]:
    alignment_height = 188.0 * WIDTH / (SOURCE_WIDTH - 49.0)
    tile_width = 235.0
    tile_height = tile_width * 336.473863 / 215.240065
    row_height = 14.0 + tile_height
    height = alignment_height + 8.0 + 2.0 * row_height + 6.0
    top = height - alignment_height - 8.0
    parts = [pdf_panel(0.0, 188.0, height, "A", compact=True)]
    for sid, letter, x, row_top in (
        ("DA1110", "B", 25.0, top),
        ("DA0940", "C", 275.0, top),
        ("DX1480", "D", 25.0, top - row_height - 6.0),
        ("DL2150", "E", 275.0, top - row_height - 6.0),
    ):
        parts.append(structure_panel_compact_v(sid, letter, x, row_top))
    return "".join(parts), height


def cca_row_layout() -> tuple[str, float]:
    alignment_height = 188.0 * WIDTH / (SOURCE_WIDTH - 49.0)
    tile_height = 220.0 / ROW_ASPECT
    row_height = 13.0 + tile_height
    height = alignment_height + 8.0 + 2.0 * row_height + 6.0
    top = height - alignment_height - 8.0
    parts = [pdf_panel(0.0, 188.0, height, "A", compact=True)]
    for sid, letter, x, row_top in (
        ("DA1110", "B", 25.0, top),
        ("DA0940", "C", 265.0, top),
        ("DX1480", "D", 25.0, top - row_height - 6.0),
        ("DL2150", "E", 265.0, top - row_height - 6.0),
    ):
        parts.append(structure_panel_row(sid, letter, x, row_top))
    return "".join(parts), height


def position_layout() -> tuple[str, float]:
    variable_height = (314.0 - 188.0) * WIDTH / SOURCE_WIDTH
    anticodon_height = (SOURCE_HEIGHT - 315.0) * WIDTH / SOURCE_WIDTH
    panel_width = 225.0
    panel_height = panel_width * 700.0 / (1820.0 - 936.0)
    c_long = (EXPERIMENTAL_VARARM / "C_long.png").as_posix()
    c_short = (EXPERIMENTAL_VARARM / "C_short.png").as_posix()
    c_gap = 18.0
    legend_height = 16.0
    c_height = 14.0 + c_gap + panel_height + legend_height
    height = variable_height + 8.0 + anticodon_height + 8.0 + c_height
    c_top = height - variable_height - 8.0 - anticodon_height - 8.0
    c_body = rf"""
\definecolor{{cvstem}}{{RGB}}{{46,125,50}}
\definecolor{{cvloop}}{{RGB}}{{200,146,75}}
\definecolor{{cvtreg}}{{RGB}}{{37,99,166}}
\definecolor{{cvother}}{{RGB}}{{180,180,180}}
\node[anchor=north west,inner sep=0,font=\sffamily\bfseries\fontsize{{11}}{{12}}\selectfont]
  at (1.5,{c_top - 1.5:.6f}) {{C}};
\node[anchor=north west,inner sep=0,font=\sffamily\bfseries\fontsize{{7.5}}{{9}}\selectfont]
  at (22,{c_top - 1.0:.6f}) {{19 V positions}};
\node[anchor=north west,inner sep=0,font=\sffamily\bfseries\fontsize{{7.5}}{{9}}\selectfont]
  at ({WIDTH / 2.0 + 15.0},{c_top - 1.0:.6f}) {{14 V positions}};
\node[anchor=north west,inner sep=0] at (15,{c_top - c_gap:.6f}) {{%
  \includegraphics[width={panel_width:.6f}bp,height={panel_height:.6f}bp,clip,
    trim=936bp 0bp 0bp 0bp]{{{c_long}}}}};
\node[anchor=north west,inner sep=0] at ({WIDTH / 2.0 + 15.0},{c_top - c_gap:.6f}) {{%
  \includegraphics[width={panel_width:.6f}bp,height={panel_height:.6f}bp,clip,
    trim=936bp 0bp 0bp 0bp]{{{c_short}}}}};
"""
    legend_y = 2.0
    legend = rf"""
\fill[cvstem] (12,{legend_y:.6f}) rectangle +(12,8);
\node[anchor=west,inner sep=0,font=\sffamily\fontsize{{7.5}}{{9}}\selectfont] at (27,{legend_y + 4:.6f}) {{V stem}};
\fill[cvloop] (92,{legend_y:.6f}) rectangle +(12,8);
\node[anchor=west,inner sep=0,font=\sffamily\fontsize{{7.5}}{{9}}\selectfont] at (107,{legend_y + 4:.6f}) {{V loop}};
\fill[cvtreg] (168,{legend_y:.6f}) rectangle +(12,8);
\node[anchor=west,inner sep=0,font=\sffamily\fontsize{{7.5}}{{9}}\selectfont] at (183,{legend_y + 4:.6f}) {{T-adjacent}};
\fill[cvother] (263,{legend_y:.6f}) rectangle +(12,8);
\node[anchor=west,inner sep=0,font=\sffamily\fontsize{{7.5}}{{9}}\selectfont] at (278,{legend_y + 4:.6f}) {{Other residues}};
"""
    return (
        pdf_panel(315.0, SOURCE_HEIGHT, height, "A")
        + pdf_panel(188.0, 314.0, height - anticodon_height - 8.0, "B")
        + c_body
        + legend,
        height,
    )


def compose(figure: str, style: str = "compact") -> None:
    if figure == "positions":
        body, height = position_layout()
    elif style == "compact_v":
        body, height = cca_compact_v_layout()
    elif style == "compact":
        body, height = cca_row_layout()
    else:
        body, height = cca_layout()
    name = "fig04_cca_terminal" if figure == "cca" else "fig05_variable_anticodon"
    tex = rf"""\documentclass[tikz,border=1bp]{{standalone}}
\usepackage{{graphicx}}
\usepackage{{helvet}}
\begin{{document}}
\begin{{tikzpicture}}[x=1bp,y=1bp]
\path[use as bounding box] (0,0) rectangle ({WIDTH},{height:.6f});
{body}
\end{{tikzpicture}}
\end{{document}}
"""
    # Stage every included asset under an ASCII-only temporary path. Windows
    # pdfTeX cannot reliably open the repository's Chinese absolute path.
    REPO_TMP.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sprinzlmap-figure-", dir=REPO_TMP) as temp:
        work = Path(temp)
        assets = [MSA]
        if figure == "positions":
            assets.extend([EXPERIMENTAL_VARARM / "C_long.png", EXPERIMENTAL_VARARM / "C_short.png"])
        elif style == "compact_v":
            assets.extend(AF3_COMPACT_V / f"{sid}.pdf" for sid in ("DA1110", "DA0940", "DX1480", "DL2150"))
        elif style == "compact":
            assets.extend(AF3_ROW / f"{sid}.png" for sid in ("DA1110", "DA0940", "DX1480", "DL2150"))
        else:
            assets.extend(
                AF3 / f"fig_af3_cca_{letter}_{sid}_v4.png"
                for sid, letter in (("DA1110", "A"), ("DA0940", "B"), ("DX1480", "C"), ("DL2150", "D"))
            )
        for index, source in enumerate(assets):
            staged = work / f"asset_{index}{source.suffix}"
            shutil.copyfile(source, staged)
            tex = tex.replace(source.as_posix(), staged.name)
        (work / "figure.tex").write_text(tex)
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "figure.tex"],
            cwd=work, capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise RuntimeError("\n".join(result.stdout.splitlines()[-35:]))
        shutil.copyfile(work / "figure.pdf", FIGURES / f"{name}.pdf")
        subprocess.run(
            ["pdftoppm", "-singlefile", "-r", "220", "-png", "figure.pdf", name],
            cwd=work, check=True,
        )
        shutil.copyfile(work / f"{name}.png", FIGURES / f"{name}.png")
    print(f"wrote {FIGURES / name}.pdf and .png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figure", choices=("cca", "positions", "all"), default="all")
    parser.add_argument("--style", choices=("compact", "compact_v", "v4"), default="compact",
                        help="terminal-panel artwork for figure 4: compact = reflowed individual_row "
                             "tiles, compact_v = the delivered individual_compact vector PDFs, "
                             "v4 = the earlier figures_abcd renders")
    args = parser.parse_args()
    for figure in ("cca", "positions") if args.figure == "all" else (args.figure,):
        compose(figure, args.style)


if __name__ == "__main__":
    main()
