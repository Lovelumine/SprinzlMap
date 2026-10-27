"""Enumerate legal variable-arm coordinate rows for a fixed residue set.

This exists to answer one question by measurement rather than guesswork: if the
Harness re-numbered the variable region from scratch, keeping the residues the
covariance model already placed there, could it reach the coordinates tRNAdb
actually uses?  ``benchmark/scripts/census_variable_pattern_ceiling.py`` runs
that test over every case no sealed arm numbers exactly.

Reachability and selectability separate sharply.  Over the 320 floor cases whose
remaining errors sit only in this region, the reference row is inside the
enumerated set for 275 of them, but the median set holds 6,681 rows.  Pruning
the stem to the pair count the covariance model observed keeps 237 reachable and
still leaves a median of 1,571.  So the answer a legal generator could produce
is usually present in the space, while the evidence available to choose from
that space is not: ranking ~10^3 rows is not a task to hand a language model,
and the model's own judgement over a fixed residue set already matches a tuned
policy.  Nothing in this module is wired into the Agent, and the remaining
variable-region error is better read as an upstream segmentation and reference-
convention problem than as a labelling choice.
"""
from __future__ import annotations

import re
from itertools import combinations
from dataclasses import dataclass
from typing import Any, List, Sequence

from backend.domain.alignment.variable_boundary import canonical_bridge_labels


GAP = "-"
MAX_VARIABLE_LOOP_RESIDUES = 5
VARIABLE_COORDINATES = ("44", "45", "46", "47", "48")
_INSERTION_RE = re.compile(r"^(.+?)i(\d+)$")
_VARIABLE_LABEL_RE = re.compile(r"^V(?:[1-5]|1[1-7]|2[1-7])$")


@dataclass(frozen=True)
class VariablePatternCandidate:
    candidate_id: str
    mode: str
    coordinates: tuple[str, ...]
    bases: tuple[str, ...]
    columns: tuple[int, ...]
    stem_pairs: int = 0
    loop_residues: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "mode": self.mode,
            "coordinates": list(self.coordinates),
            "stem_pairs": self.stem_pairs,
            "loop_residues": self.loop_residues,
        }


def is_variable_coordinate(label: Any) -> bool:
    """True for any Sprinzl coordinate inside the variable region, 44-48."""

    value = str(label or "").strip()
    if value in VARIABLE_COORDINATES or _VARIABLE_LABEL_RE.match(value):
        return True
    parent = _INSERTION_RE.match(value)
    if not parent:
        return False
    head = parent.group(1)
    return head in VARIABLE_COORDINATES or bool(_VARIABLE_LABEL_RE.match(head))


def _norm_base(value: Any) -> str:
    text = str(value or "").strip().upper().replace("T", "U")
    return text if text in {"A", "C", "G", "U", "N"} else GAP


def _prefix_options(count: int) -> tuple[str, ...] | None:
    """Acceptor-side bridge occupancy 44-45, filled in Sprinzl order."""

    return {0: (), 1: ("44",), 2: ("44", "45")}.get(int(count))


def _suffix_options(count: int) -> tuple[str, ...] | None:
    """Bridge occupancy 46-48; the documented fill order is 48, 46, 47."""

    documented = canonical_bridge_labels(count)
    if documented is not None:
        return tuple(documented)
    return {3: ("46", "47", "48"), 2: ("47", "48"), 1: ("46",)}.get(int(count))


def _stem(side: str, pairs: int, missing: int | None = None) -> List[str]:
    labels = [f"{side}{index}" for index in range(1, pairs + 1)]
    if missing is not None:
        labels = [label for label in labels if not label.endswith(str(missing))]
    return labels


def _variable_loop(count: int) -> List[str]:
    return [f"V{index}" for index in range(1, count + 1)]


def _variable_loop_options(count: int) -> List[List[str]]:
    """Every way to place ``count`` unpaired residues on positions V1-V5.

    The reference does not always start the loop at V1: a stem of three pairs
    leaves the three loop residues written V2, V3, V4, because V1 and V5 sit
    inside the paired region.  Enumerating only V1..VL would call that illegal.
    """

    if count > MAX_VARIABLE_LOOP_RESIDUES:
        return []
    positions = list(range(1, MAX_VARIABLE_LOOP_RESIDUES + 1))
    return [[f"V{index}" for index in combo] for combo in combinations(positions, count)]


def _stem_sizes(arm: int, observed_pairs: int | None) -> range:
    if observed_pairs is None:
        return range(1, arm + 1)
    return range(max(1, int(observed_pairs) - 1), min(arm, int(observed_pairs) + 1) + 1)


def _count_stem_pairs(coordinates: Sequence[str]) -> int:
    left = sum(1 for item in coordinates if str(item).startswith("V1"))
    right = sum(1 for item in coordinates if str(item).startswith("V2"))
    return min(left, right)


def _count_loops(coordinates: Sequence[str]) -> int:
    return sum(
        1 for item in coordinates if _VARIABLE_LABEL_RE.match(str(item)) and len(str(item)) == 2
    )


def enumerate_variable_patterns(
    labels: Sequence[str],
    target: Sequence[str],
    *,
    observed_pairs: int | None = None,
    limit: int = 4000,
) -> List[VariablePatternCandidate]:
    """Return the legal coordinate rows for the residues this block holds.

    Rows assign coordinates to residues in 5'-to-3' order.  Bases never move,
    so the residue count fixes every candidate length.  The pool is the
    canonical variable interval rather than the columns this alignment kept:
    a covariance model can delete position 45 in one query and 47 in another,
    yet both tRNAs still hold five residues there.  Which column a residue falls
    in is an artefact of the alignment; which coordinate it carries is a
    property of the tRNA.

    ``observed_pairs`` prunes the stem length to the pair count the covariance
    model reports, plus or minus one, which is the range in which an
    unmodelled position can hide.
    """

    if not labels or len(labels) != len(target):
        return []
    columns = [index for index in range(len(labels)) if is_variable_coordinate(labels[index])]
    occupied = [index for index in columns if _norm_base(target[index]) != GAP]
    if not occupied:
        return []
    bases = tuple(_norm_base(target[index]) for index in occupied)
    current = tuple(str(labels[index] or "").strip() for index in occupied)
    count = len(occupied)
    out: List[VariablePatternCandidate] = []
    seen: set[tuple[str, ...]] = set()

    def add(mode: str, coordinates: Sequence[str], pairs: int, loops: int) -> None:
        pattern = tuple(str(item) for item in coordinates)
        if len(pattern) != count or pattern in seen:
            return
        if any(not item or not is_variable_coordinate(item) for item in pattern):
            return
        seen.add(pattern)
        out.append(
            VariablePatternCandidate(
                candidate_id=f"vpat-{len(out) + 1:02d}",
                mode=mode,
                coordinates=pattern,
                bases=bases,
                columns=tuple(occupied),
                stem_pairs=int(pairs),
                loop_residues=int(loops),
            )
        )

    add("as_is", current, _count_stem_pairs(current), _count_loops(current))

    for prefix_count in (1, 2, 0):
        prefix = _prefix_options(prefix_count)
        if prefix is None:
            continue
        for suffix_count in range(0, 4):
            suffix = _suffix_options(suffix_count)
            if suffix is None or prefix_count + suffix_count > count:
                continue
            arm = count - prefix_count - suffix_count
            if arm == 0:
                add("canonical_short_loop", [*prefix, *suffix], 0, 0)
                continue
            # An arm is V11..V1m paired against V2m..V21 with unpaired
            # residues between.  A covariance model can fail to model one
            # position, and tRNAdb then simply omits that coordinate, so each
            # side may be missing one index anywhere along the stem.
            for stem_size in _stem_sizes(arm, observed_pairs):
                for left_missing in (None, *range(1, stem_size + 1)):
                    for right_missing in (None, *range(1, stem_size + 1)):
                        left = _stem("V1", stem_size, left_missing)
                        right = _stem("V2", stem_size, right_missing)
                        loops = arm - len(left) - len(right)
                        if loops < 0 or loops > MAX_VARIABLE_LOOP_RESIDUES:
                            continue
                        for loop_option in _variable_loop_options(loops):
                            add(
                                "canonical_arm"
                                if left_missing is None and right_missing is None
                                else "arm_unmodelled_pair",
                                [*prefix, *left, *loop_option, *reversed(right), *suffix],
                                min(len(left), len(right)),
                                loops,
                            )
            if 0 < arm <= MAX_VARIABLE_LOOP_RESIDUES:
                for loop_option in _variable_loop_options(arm):
                    add("unpaired_variable_loop", [*prefix, *loop_option, *suffix], 0, arm)
            if len(out) >= limit:
                return out[:limit]
    return out[:limit]


__all__ = [
    "VariablePatternCandidate",
    "enumerate_variable_patterns",
    "is_variable_coordinate",
]
