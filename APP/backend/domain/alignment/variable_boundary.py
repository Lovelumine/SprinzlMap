from __future__ import annotations

import re
from typing import Any, Iterable, Sequence


STANDARD_VARIABLE_PREFIX = ("44", "45")
STANDARD_VARIABLE_BRIDGE = ("46", "47", "48")
STANDARD_VARIABLE_ORDER = (*STANDARD_VARIABLE_PREFIX, *STANDARD_VARIABLE_BRIDGE)
SHORT_VARIABLE_OCCUPANCY_PRIORITY = ("44", "48", "46", "47", "45")
_VALID_BASES = {"A", "C", "G", "U", "N"}
_INSERTION_RE = re.compile(r"^(.+?)i(\d+)$")
_VARIABLE_LABEL_RE = re.compile(r"^V(?:[1-5]|1[1-7]|2[1-7])$")


def _norm_base(raw: Any) -> str:
    value = str(raw or "").strip().upper().replace("T", "U")
    return value if value in _VALID_BASES else "-"


def label_parent(label: str) -> str:
    value = str(label or "").strip()
    match = _INSERTION_RE.match(value)
    return match.group(1) if match else value


def canonical_bridge_labels(residue_count: int) -> tuple[str, ...] | None:
    """Return the Sprinzl 46-48 occupancy for a bridge of the given length.

    Sprinzl et al. assign one, two, and three residues in this interval to
    48; 46/48; and 46/47/48, respectively. More than three residues require
    an insertion or a re-evaluation of the variable-arm boundary.
    """

    return {
        0: (),
        1: ("48",),
        2: ("46", "48"),
        3: ("46", "47", "48"),
    }.get(int(residue_count))


def preferred_standard_variable_labels(
    residue_count: int,
    available_labels: Iterable[str],
) -> tuple[str, ...] | None:
    """Select revised Sprinzl labels for an unpaired short variable region.

    The 1993 compilation fills positions 45-48 in the order 48, 46, 47,
    45, after position 44. Returned labels remain in coordinate order.
    """

    available = {str(label or "").strip() for label in available_labels}
    count = max(0, int(residue_count))
    priority = tuple(
        label for label in SHORT_VARIABLE_OCCUPANCY_PRIORITY if label in available
    )
    if count > len(priority):
        return None
    selected = set(priority[:count])
    return tuple(label for label in STANDARD_VARIABLE_ORDER if label in selected)


def _occupied_rows(
    labels: Sequence[str],
    sequence_row: Sequence[str] | None,
) -> list[dict[str, Any]]:
    if sequence_row is not None and len(labels) != len(sequence_row):
        return []
    rows: list[dict[str, Any]] = []
    residue_index = 0
    for column_index, raw_label in enumerate(labels, start=1):
        base = "N" if sequence_row is None else _norm_base(sequence_row[column_index - 1])
        if base == "-":
            continue
        residue_index += 1
        label = str(raw_label or "").strip()
        rows.append(
            {
                "residue_index": residue_index,
                "column_index": column_index,
                "label": label,
                "parent": label_parent(label),
                "base": base,
            }
        )
    return rows


def assess_variable_bridge(
    labels: Sequence[str],
    sequence_row: Sequence[str] | None = None,
    *,
    require_t_stem_anchor: bool = True,
) -> dict[str, Any]:
    """Assess the occupied interval between the variable arm and T-stem 49.

    The assessment is answer-free: it uses only the candidate labels and
    occupied residues. It never reads benchmark reference coordinates.
    """

    rows = _occupied_rows(labels, sequence_row)
    if not rows:
        return {
            "status": "insufficient_evidence",
            "reason": "empty_or_inconsistent_rows",
            "answer_free": True,
            "gold_coordinates_used": False,
        }

    t49_index = next(
        (index for index, item in enumerate(rows) if item["label"] == "49"),
        None,
    )
    has_65 = any(item["label"] == "65" for item in rows)
    if require_t_stem_anchor and (t49_index is None or not has_65):
        return {
            "status": "insufficient_evidence",
            "reason": "t_stem_49_65_anchor_missing",
            "answer_free": True,
            "gold_coordinates_used": False,
        }
    endpoint = len(rows) if t49_index is None else t49_index

    anchor_indices = [
        index
        for index, item in enumerate(rows[:endpoint])
        if item["label"] in STANDARD_VARIABLE_PREFIX
        or _VARIABLE_LABEL_RE.match(item["parent"])
    ]
    if not anchor_indices:
        return {
            "status": "insufficient_evidence",
            "reason": "variable_bridge_left_anchor_missing",
            "answer_free": True,
            "gold_coordinates_used": False,
        }

    start = anchor_indices[-1] + 1
    bridge_rows = rows[start:endpoint]
    allowed_parents = {"45", *STANDARD_VARIABLE_BRIDGE}
    unsupported = [
        item["label"]
        for item in bridge_rows
        if item["parent"] not in allowed_parents
    ]
    if unsupported:
        return {
            "status": "insufficient_evidence",
            "reason": "unsupported_labels_between_variable_arm_and_t_stem",
            "unsupported_labels": unsupported,
            "answer_free": True,
            "gold_coordinates_used": False,
        }

    expected = canonical_bridge_labels(len(bridge_rows))
    observed = tuple(str(item["label"]) for item in bridge_rows)
    evidence = {
        "status": "conforming" if expected == observed else "conflict",
        "reason": (
            "sprinzl_bridge_occupancy_conforms"
            if expected == observed
            else (
                "bridge_has_more_than_three_residues"
                if expected is None
                else "sprinzl_bridge_occupancy_mismatch"
            )
        ),
        "observed_labels": list(observed),
        "expected_labels": list(expected or ()),
        "bridge_residue_count": len(bridge_rows),
        "residue_indices": [int(item["residue_index"]) for item in bridge_rows],
        "bases": [str(item["base"]) for item in bridge_rows],
        "left_anchor_label": str(rows[anchor_indices[-1]]["label"]),
        "t_stem_49_65_anchor_present": bool(t49_index is not None and has_65),
        "contains_insertion_state": any(
            _INSERTION_RE.match(str(item["label"])) for item in bridge_rows
        ),
        "answer_free": True,
        "gold_coordinates_used": False,
        "rule": "sprinzl_1993_variable_bridge_48_46_47",
    }
    if expected is None:
        evidence["status"] = "requires_review"
    return evidence


__all__ = [
    "STANDARD_VARIABLE_BRIDGE",
    "STANDARD_VARIABLE_ORDER",
    "STANDARD_VARIABLE_PREFIX",
    "SHORT_VARIABLE_OCCUPANCY_PRIORITY",
    "assess_variable_bridge",
    "canonical_bridge_labels",
    "label_parent",
    "preferred_standard_variable_labels",
]
