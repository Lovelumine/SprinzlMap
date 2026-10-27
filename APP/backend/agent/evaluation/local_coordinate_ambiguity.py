from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from backend.domain.alignment.variable_boundary import assess_variable_bridge

from ..contracts import RootSnapshot


_VALID_BASES = {"A", "C", "G", "U", "N"}


def _norm_base(raw: Any) -> str:
    value = str(raw or "").strip().upper().replace("T", "U")
    return value if value in _VALID_BASES else "-"


def occupied_residues(snapshot: RootSnapshot) -> List[Dict[str, Any]]:
    residues: List[Dict[str, Any]] = []
    for column_index, (label, base) in enumerate(
        zip(snapshot.numbering_row, snapshot.sequence_row),
        start=1,
    ):
        normalized = _norm_base(base)
        if normalized == "-":
            continue
        residues.append(
            {
                "residue_index": len(residues) + 1,
                "column_index": column_index,
                "label": str(label or "").strip(),
                "base": normalized,
            }
        )
    return residues


def residue_label_signature(
    snapshot: RootSnapshot,
    residue_indices: Iterable[int],
) -> tuple[str, ...]:
    by_index = {
        int(item["residue_index"]): str(item["label"])
        for item in occupied_residues(snapshot)
    }
    return tuple(by_index.get(int(index), "") for index in residue_indices)


def assess_local_coordinate_ambiguity(
    snapshot: RootSnapshot,
) -> Dict[str, Any] | None:
    """Detect a nonconforming 46-48 bridge using candidate evidence only."""

    evidence = assess_variable_bridge(
        snapshot.numbering_row,
        snapshot.sequence_row,
        require_t_stem_anchor=True,
    )
    if str(evidence.get("status") or "") != "conflict":
        return None

    residue_indices = [int(value) for value in evidence.get("residue_indices") or []]
    expected_labels = [str(value) for value in evidence.get("expected_labels") or []]
    if not residue_indices or len(residue_indices) != len(expected_labels):
        return None

    provenance_by_index: Dict[int, Mapping[str, Any]] = {}
    meta = dict(snapshot.raw_meta or {})
    provenance_rows = [
        *list(meta.get("position_provenance") or []),
        *list(dict(meta.get("cm_numbering") or {}).get("residues") or []),
    ]
    for raw in provenance_rows:
        if not isinstance(raw, Mapping):
            continue
        try:
            residue_index = int(raw.get("residue_index"))
        except (TypeError, ValueError):
            continue
        provenance_by_index[residue_index] = raw

    return {
        "code": "cm_variable_bridge_coordinate_ambiguity",
        "region": "Variable loop",
        "resolution_mode": "sprinzl_46_48_bridge_occupancy",
        "observed_labels": list(evidence.get("observed_labels") or []),
        "reference_candidate_labels": expected_labels,
        "residue_indices": residue_indices,
        "bases": list(evidence.get("bases") or []),
        "posterior_codes": [
            str(provenance_by_index.get(index, {}).get("posterior_code") or "")
            for index in residue_indices
        ],
        "left_anchor_label": str(evidence.get("left_anchor_label") or ""),
        "t_stem_49_65_anchor_present": bool(
            evidence.get("t_stem_49_65_anchor_present")
        ),
        "contains_insertion_state": bool(evidence.get("contains_insertion_state")),
        "interpretation": (
            "the occupied bridge does not follow the Sprinzl 48; 46/48; "
            "46/47/48 convention for one, two, or three residues"
        ),
        "effect": (
            "request_distinct_template_support_for_the_same_canonical_bridge_"
            "assignment_without_reading_gold_coordinates"
        ),
        "answer_free": True,
        "gold_coordinates_used": False,
        "rule": str(evidence.get("rule") or ""),
    }


def candidate_local_resolution(
    parent_snapshot: RootSnapshot,
    candidate_snapshot: RootSnapshot,
    evidence: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    observed = dict(evidence or assess_local_coordinate_ambiguity(parent_snapshot) or {})
    residue_indices = [
        int(value)
        for value in list(observed.get("residue_indices") or [])
        if str(value).strip()
    ]
    expected_signature = tuple(
        str(value) for value in list(observed.get("reference_candidate_labels") or [])
    )
    if not residue_indices or len(residue_indices) != len(expected_signature):
        return {
            "eligible": False,
            "resolved": False,
            "reason": "parent_bridge_conflict_not_detected",
        }

    parent_residues = occupied_residues(parent_snapshot)
    candidate_residues = occupied_residues(candidate_snapshot)
    parent_bases = tuple(str(item["base"]) for item in parent_residues)
    candidate_bases = tuple(str(item["base"]) for item in candidate_residues)
    base_preserved = parent_bases == candidate_bases
    candidate_signature = residue_label_signature(candidate_snapshot, residue_indices)
    resolved = bool(base_preserved and candidate_signature == expected_signature)
    resolution_key = (
        "sprinzl_bridge_at_residues:"
        + ",".join(map(str, residue_indices))
        + "="
        + ",".join(expected_signature)
        if resolved
        else ""
    )
    position_47_residue_index = None
    if "47" in expected_signature:
        position_47_residue_index = residue_indices[expected_signature.index("47")]
    return {
        "eligible": True,
        "resolved": resolved,
        "reason": (
            "reference_candidate_matches_canonical_sprinzl_bridge"
            if resolved
            else "reference_candidate_does_not_match_canonical_sprinzl_bridge"
        ),
        "residue_indices": residue_indices,
        "parent_signature": list(
            residue_label_signature(parent_snapshot, residue_indices)
        ),
        "candidate_signature": list(candidate_signature),
        "expected_signature": list(expected_signature),
        "resolution_mode": "sprinzl_46_48_bridge_occupancy",
        "resolution_key": resolution_key,
        "position_47_residue_index": position_47_residue_index,
        "base_preserved": base_preserved,
        "answer_free": True,
        "gold_coordinates_used": False,
    }


__all__ = [
    "assess_local_coordinate_ambiguity",
    "candidate_local_resolution",
    "occupied_residues",
    "residue_label_signature",
]
