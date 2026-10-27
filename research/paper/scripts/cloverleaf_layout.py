#!/usr/bin/env python3
"""Cloverleaf coordinates for Sprinzl positions.

The authoritative layout already exists in the web application
(``APP/web/src/components/layout/sprinzlLayout.ts``) and Figures 3 and 4 reuse it
through ``make_fig04_coordinate_map.load_sprinzl_positions``. This module reads
the same file so every figure in the manuscript draws the identical molecule
instead of a hand-rolled approximation.

Note on orientation: the web layout stores y downwards (SVG convention). Callers
that plot with a conventional upward y axis must flip, which :func:`place` does
by default.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAYOUT_TS = ROOT / "APP/web/src/components/layout/sprinzlLayout.ts"

_POS_RE = re.compile(
    r'(?:"(?P<quoted>[-\w]+)"|(?P<bare>V\d+)):\s*\{\s*'
    r'x:\s*(?P<x>[-\d.]+),\s*y:\s*(?P<y>[-\d.]+)\s*\}'
)
_ORDER_RE = re.compile(
    r'export const SPRINZL_POSITION_ORDER = Object\.freeze\(\[(?P<body>.*?)\]\);',
    re.S,
)


def _load_positions(flip_y: bool = True) -> dict[str, tuple[float, float]]:
    text = LAYOUT_TS.read_text(encoding="utf-8")
    positions: dict[str, tuple[float, float]] = {}
    for m in _POS_RE.finditer(text):
        key = m.group("quoted") or m.group("bare")
        y = float(m.group("y"))
        positions[key] = (float(m.group("x")), -y if flip_y else y)
    if not positions:
        raise RuntimeError(f"No Sprinzl positions parsed from {LAYOUT_TS}")
    return positions


def _load_order() -> list[str]:
    """Expand SPRINZL_POSITION_ORDER, which uses numericLabels(a, b) helpers."""
    text = LAYOUT_TS.read_text(encoding="utf-8")
    m = _ORDER_RE.search(text)
    if not m:
        return []
    body = m.group("body")
    order: list[str] = []
    for token in re.finditer(
            r'numericLabels\(\s*(\d+)\s*,\s*(\d+)\s*\)|"([^"]+)"|(V\d+)', body):
        if token.group(1):
            lo, hi = int(token.group(1)), int(token.group(2))
            order.extend(str(i) for i in range(lo, hi + 1))
        elif token.group(3):
            order.append(token.group(3))
        elif token.group(4):
            order.append(token.group(4))
    return order


CANONICAL: dict[str, tuple[float, float]] = _load_positions()
BACKBONE: list[str] = _load_order() or list(CANONICAL)


def backbone_order() -> list[str]:
    """5'->3' traversal from the web layout, filtered to known positions."""
    return [l for l in BACKBONE if l in CANONICAL]


# Base pairs used by Figure 4 (PAIR_LINKS in make_fig04_coordinate_map.py).
PAIR_LINKS = [
    ("1", "72"), ("2", "71"), ("3", "70"), ("4", "69"), ("5", "68"),
    ("6", "67"), ("7", "66"),
    ("10", "25"), ("11", "24"), ("12", "23"), ("13", "22"),
    ("27", "43"), ("28", "42"), ("29", "41"), ("30", "40"), ("31", "39"),
    ("49", "65"), ("50", "64"), ("51", "63"), ("52", "62"),
]


def place(labels: list[str]) -> tuple[dict[str, tuple[float, float]], list[str]]:
    placed = {l: CANONICAL[l] for l in labels if l in CANONICAL}
    missing = [l for l in labels if l not in CANONICAL]
    return placed, missing


if __name__ == "__main__":
    print(f"{LAYOUT_TS} -> {len(CANONICAL)} positions, "
          f"{len(BACKBONE)} in backbone order")
