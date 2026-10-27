from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence

from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.sprinzl_coordinates import labels_are_monotonic
from backend.domain.alignment.variable_boundary import (
    assess_variable_bridge,
    canonical_bridge_labels,
    preferred_standard_variable_labels,
)


_VALID_BASES = {"A", "C", "G", "U", "N"}
_CANONICAL_PAIRS = {"AU", "UA", "GC", "CG", "GU", "UG"}
_INSERTION_RE = re.compile(r"^(.+?)i\d+$")
_VARIABLE_LOOP_LABEL_RE = re.compile(r"^V([1-5])$")
_VARIABLE_ARM_LABEL_RE = re.compile(
    r"^V(?:[1-5]|1[1-7]|2[1-7])(?:i\d+)?$"
)
_STANDARD_VARIABLE_LABELS = frozenset({"44", "45", "46", "47", "48"})


def _norm_base(raw: Any) -> str:
    value = str(raw or "").strip().upper().replace("T", "U")
    return value if value in _VALID_BASES else "-"


def _norm_sequence(raw: Any) -> str:
    return "".join(
        char
        for char in str(raw or "").upper().replace("T", "U")
        if char in _VALID_BASES
    )


def _clean_structure(raw: Any) -> str:
    chars: List[str] = []
    for char in str(raw or ""):
        if char.isspace():
            continue
        if char in "<>":
            chars.append(char)
        elif char in ".,_:":
            chars.append(".")
        else:
            return ""
    return "".join(chars)


def _parse_pairs(structure: str) -> Dict[int, int] | None:
    stack: List[int] = []
    pairs: Dict[int, int] = {}
    for index, char in enumerate(structure):
        if char == ">":
            stack.append(index)
        elif char == "<":
            if not stack:
                return None
            left = stack.pop()
            pairs[left] = index
            pairs[index] = left
    return None if stack else pairs


def _find_unique_subsequence(sequence: str, subsequence: str) -> int | None:
    starts: List[int] = []
    cursor = sequence.find(subsequence)
    while cursor >= 0:
        starts.append(cursor)
        cursor = sequence.find(subsequence, cursor + 1)
    return starts[0] if len(starts) == 1 else None


def _occupied(snapshot: Any) -> tuple[List[str], List[str]]:
    labels: List[str] = []
    bases: List[str] = []
    for label, base in zip(
        list(getattr(snapshot, "numbering_row", []) or []),
        list(getattr(snapshot, "sequence_row", []) or []),
    ):
        normalized = _norm_base(base)
        if normalized == "-":
            continue
        labels.append(str(label or "").strip())
        bases.append(normalized)
    return labels, bases


def _is_variable_label(label: str) -> bool:
    try:
        return number_to_region(str(label or "")) == "Variable loop"
    except ValueError:
        return False


def assess_variable_boundary_reference_agreement(
    *,
    proposed_labels: Sequence[str],
    reference_labels: Sequence[str],
    variable_span_residue_indices: Sequence[int],
) -> Dict[str, Any]:
    """Compare the biological boundary partition, not project V-label offsets."""

    span_indices: List[int] = []
    for raw_index in variable_span_residue_indices:
        try:
            residue_index = int(raw_index)
        except (TypeError, ValueError):
            return {
                "agrees": False,
                "reason": "variable_span_contains_invalid_residue_index",
            }
        if residue_index < 1:
            return {
                "agrees": False,
                "reason": "variable_span_contains_invalid_residue_index",
            }
        span_indices.append(residue_index)

    if not span_indices:
        return {"agrees": False, "reason": "variable_span_is_empty"}
    if len(proposed_labels) != len(reference_labels):
        return {
            "agrees": False,
            "reason": "proposed_and_reference_residue_counts_differ",
        }
    if max(span_indices) > len(proposed_labels):
        return {
            "agrees": False,
            "reason": "variable_span_exceeds_candidate_residue_count",
        }

    disagreements: List[Dict[str, Any]] = []
    standard_coordinate_checks = 0
    variable_arm_partition_checks = 0
    for residue_index in span_indices:
        proposed = str(proposed_labels[residue_index - 1] or "").strip()
        reference = str(reference_labels[residue_index - 1] or "").strip()
        if proposed in _STANDARD_VARIABLE_LABELS:
            standard_coordinate_checks += 1
            agrees = reference == proposed
            comparison = "exact_standard_coordinate"
        elif _VARIABLE_ARM_LABEL_RE.match(proposed):
            variable_arm_partition_checks += 1
            agrees = bool(_VARIABLE_ARM_LABEL_RE.match(reference))
            comparison = "variable_arm_partition"
        else:
            agrees = False
            comparison = "unsupported_proposed_label"
        if not agrees:
            disagreements.append(
                {
                    "residue_index": residue_index,
                    "proposed_label": proposed,
                    "reference_label": reference,
                    "comparison": comparison,
                }
            )

    return {
        "agrees": not disagreements,
        "reason": (
            "reference_supports_variable_boundary_partition"
            if not disagreements
            else "reference_disagrees_with_variable_boundary_partition"
        ),
        "checked_residue_count": len(span_indices),
        "standard_coordinate_checks": standard_coordinate_checks,
        "variable_arm_partition_checks": variable_arm_partition_checks,
        "disagreements": disagreements,
    }


def assess_variable_boundary_ambiguity(snapshot: Any) -> Dict[str, Any] | None:
    """Open an independent structure check without asserting an answer."""

    meta = dict(getattr(snapshot, "raw_meta", {}) or {})
    if str(meta.get("numbering_backend") or "") != "cmalign":
        return None
    labels, bases = _occupied(snapshot)
    variable_labels = [label for label in labels if _is_variable_label(label)]
    if not variable_labels:
        return None

    bridge = assess_variable_bridge(labels, bases, require_t_stem_anchor=True)
    has_variable_arm = any(label.startswith("V") for label in variable_labels)
    has_variable_insertion = any(
        _INSERTION_RE.match(label) for label in variable_labels
    )
    bridge_status = str(bridge.get("status") or "")
    reasons: List[str] = []
    if bridge_status in {"conflict", "requires_review"}:
        reasons.append("sprinzl_bridge_occupancy_requires_review")
    if has_variable_arm:
        reasons.append("variable_arm_boundary_requires_independent_structure")
    if has_variable_insertion:
        reasons.append("variable_region_insertion_state_requires_review")
    if not reasons:
        return None

    provenance = list(meta.get("position_provenance") or [])
    if not provenance:
        provenance = list(dict(meta.get("cm_numbering") or {}).get("residues") or [])
    posterior_codes = [
        str((item or {}).get("posterior_code") or "")
        for item in provenance
        if _is_variable_label(str((item or {}).get("label") or ""))
    ]
    return {
        "code": "cm_variable_boundary_structure_ambiguity",
        "region": "Variable loop",
        "observed_labels": variable_labels,
        "bridge_status": bridge_status,
        "bridge_observed": list(bridge.get("observed_labels") or []),
        "bridge_expected": list(bridge.get("expected_labels") or []),
        "posterior_codes": posterior_codes,
        "trigger_reasons": reasons,
        "effect": "request_independent_trnascan_variable_boundary_projection",
        "coordinate_assertion": "none",
        "answer_free": True,
        "gold_coordinates_used": False,
    }


def _unique_label_index(labels: Sequence[str], label: str) -> int | None:
    indices = [index for index, value in enumerate(labels) if value == label]
    return indices[0] if len(indices) == 1 else None


def _preserved_ordered_subset(
    labels: Sequence[str],
    indices: Sequence[int],
    allowed: Sequence[str],
) -> List[str] | None:
    observed = [str(labels[index] or "").strip() for index in indices]
    order = {label: index for index, label in enumerate(allowed)}
    if any(label not in order for label in observed):
        return None
    ranks = [order[label] for label in observed]
    if len(set(observed)) != len(observed) or ranks != sorted(ranks):
        return None
    return observed


def reconcile_variable_boundary(
    *,
    target_sequence: str,
    current_labels: Sequence[str],
    current_bases: Sequence[str],
    struct_file_sequence: str,
    secondary_structure: str,
    minimum_long_arm_pairs: int = 3,
) -> Dict[str, Any]:
    """Project the variable region from an independent secondary structure."""

    target = _norm_sequence(target_sequence)
    labels = [str(value or "").strip() for value in current_labels]
    bases = [_norm_base(value) for value in current_bases]
    if not target or len(labels) != len(bases) or len(bases) != len(target):
        return {
            "resolved": False,
            "reason": "target_and_occupied_rows_are_not_length_consistent",
        }
    if "".join(bases) != target:
        return {
            "resolved": False,
            "reason": "target_sequence_not_preserved_by_parent_candidate",
        }

    structure_sequence = _norm_sequence(struct_file_sequence)
    structure = _clean_structure(secondary_structure)
    if not structure_sequence or len(structure_sequence) != len(structure):
        return {
            "resolved": False,
            "reason": "independent_structure_sequence_and_shape_length_mismatch",
        }
    hit_start = _find_unique_subsequence(target, structure_sequence)
    if hit_start is None:
        return {
            "resolved": False,
            "reason": "independent_structure_sequence_not_unique_in_input",
        }
    local_pairs = _parse_pairs(structure)
    if local_pairs is None:
        return {"resolved": False, "reason": "independent_structure_not_balanced"}
    pairs = {
        hit_start + left: hit_start + right
        for left, right in local_pairs.items()
    }

    index_43 = _unique_label_index(labels, "43")
    index_49 = _unique_label_index(labels, "49")
    index_65 = _unique_label_index(labels, "65")
    if index_43 is None or index_49 is None or index_65 is None:
        return {
            "resolved": False,
            "reason": "sprinzl_43_49_65_anchors_are_not_unique",
        }
    if pairs.get(index_49) != index_65:
        return {
            "resolved": False,
            "reason": "independent_structure_does_not_confirm_49_65_t_stem_anchor",
            "observed_49_partner_residue_index": (
                pairs.get(index_49) + 1 if index_49 in pairs else None
            ),
        }

    span_start = index_43 + 1
    span_end = index_49
    span = list(range(span_start, span_end))
    if not span or any(not _is_variable_label(labels[index]) for index in span):
        return {
            "resolved": False,
            "reason": "current_43_49_interval_is_not_a_pure_variable_region",
        }
    crossing_pairs = [
        (index, partner)
        for index in span
        for partner in [pairs.get(index)]
        if partner is not None and partner not in span
    ]
    if crossing_pairs:
        return {
            "resolved": False,
            "reason": "independent_structure_pairs_cross_the_variable_boundary",
        }

    variable_pairs = sorted(
        (left, right)
        for left, right in pairs.items()
        if span_start <= left < right < span_end
    )
    expected: List[str]
    pair_records: List[Dict[str, Any]] = []
    canonical_pair_count = 0
    mode = "short_unpaired_variable_region"

    if variable_pairs:
        pair_count = len(variable_pairs)
        if not max(1, int(minimum_long_arm_pairs)) <= pair_count <= 7:
            return {
                "resolved": False,
                "reason": "variable_stem_pair_count_outside_supported_range",
                "pair_count": pair_count,
            }
        left_indices = [left for left, _right in variable_pairs]
        right_indices = [right for _left, right in variable_pairs]
        if left_indices != list(range(left_indices[0], left_indices[0] + pair_count)):
            return {
                "resolved": False,
                "reason": "variable_stem_left_arm_is_not_contiguous",
            }
        if right_indices != sorted(right_indices, reverse=True):
            return {
                "resolved": False,
                "reason": "variable_stem_pairs_are_not_properly_nested",
            }
        right_sequence = sorted(right_indices)
        if right_sequence != list(
            range(right_sequence[0], right_sequence[0] + pair_count)
        ):
            return {
                "resolved": False,
                "reason": "variable_stem_right_arm_is_not_contiguous",
            }

        prefix = list(range(span_start, left_indices[0]))
        loop = list(range(left_indices[-1] + 1, right_sequence[0]))
        suffix = list(range(right_sequence[-1] + 1, span_end))
        if not 1 <= len(prefix) <= 2:
            return {
                "resolved": False,
                "reason": "variable_arm_prefix_length_outside_44_45_capacity",
                "prefix_length": len(prefix),
            }
        if not 1 <= len(loop) <= 5:
            return {
                "resolved": False,
                "reason": "variable_arm_loop_length_outside_v1_v5_capacity",
                "loop_length": len(loop),
            }
        suffix_labels = canonical_bridge_labels(len(suffix))
        if suffix_labels is None or not suffix_labels:
            return {
                "resolved": False,
                "reason": "variable_arm_suffix_length_outside_46_48_capacity",
                "suffix_length": len(suffix),
            }

        for left, right in variable_pairs:
            pair_string = bases[left] + bases[right]
            canonical_pair_count += pair_string in _CANONICAL_PAIRS
            pair_records.append(
                {
                    "left_residue_index": left + 1,
                    "right_residue_index": right + 1,
                    "bases": pair_string,
                }
            )
        if canonical_pair_count < max(1, pair_count - 1):
            return {
                "resolved": False,
                "reason": "variable_stem_has_insufficient_canonical_pair_support",
                "pair_count": pair_count,
                "canonical_pair_count": canonical_pair_count,
            }

        if len(prefix) == 2:
            # A complete two-residue segment between anchor 43 and the extra
            # arm occupies the standard 44/45 positions; the extra arm begins
            # after 45 in this coordinate dialect.
            prefix_labels = ["44", "45"]
        else:
            prefix_labels = _preserved_ordered_subset(
                labels,
                prefix,
                ("44", "45"),
            )
            if prefix_labels is None:
                return {
                    "resolved": False,
                    "reason": "single_variable_prefix_identity_requires_homology_review",
                    "observed_prefix_labels": [labels[index] for index in prefix],
                }
        loop_labels = _preserved_ordered_subset(
            labels,
            loop,
            ("V1", "V2", "V3", "V4", "V5"),
        )
        if loop_labels is None or any(
            not _VARIABLE_LOOP_LABEL_RE.match(label) for label in loop_labels
        ):
            return {
                "resolved": False,
                "reason": "variable_arm_loop_dialect_requires_homology_review",
                "observed_loop_labels": [labels[index] for index in loop],
            }

        expected = list(prefix_labels)
        expected.extend(f"V1{index}" for index in range(1, pair_count + 1))
        # The independent structure identifies the loop, but not its alignment
        # offset within the project/tRNAviz V1-V5 dialect. Keep a valid existing
        # offset instead of manufacturing a new homology assertion.
        expected.extend(loop_labels)
        expected.extend(f"V2{index}" for index in range(pair_count, 0, -1))
        expected.extend(suffix_labels)
        mode = "long_variable_arm_from_independent_pairs"
    else:
        pair_count = 0
        standard = preferred_standard_variable_labels(
            len(span),
            ("44", "45", "46", "47", "48"),
        )
        if standard is None:
            return {
                "resolved": False,
                "reason": "unpaired_variable_region_exceeds_standard_capacity",
                "variable_residue_count": len(span),
            }
        expected = list(standard)

    if len(expected) != len(span):
        return {
            "resolved": False,
            "reason": "projected_labels_do_not_cover_the_variable_span",
        }
    candidate = list(labels)
    for index, label in zip(span, expected):
        candidate[index] = label
    try:
        monotonic = labels_are_monotonic(candidate)
    except ValueError:
        monotonic = False
    if not monotonic:
        return {
            "resolved": False,
            "reason": "projected_labels_are_not_monotonic",
        }

    changed = [
        index + 1
        for index in span
        if candidate[index] != labels[index]
    ]
    for record, (left, right) in zip(pair_records, variable_pairs):
        record["left_label"] = candidate[left]
        record["right_label"] = candidate[right]
    return {
        "resolved": True,
        "reason": "independent_variable_structure_projection_ready",
        "mode": mode,
        "candidate_labels": candidate,
        "changed_residue_indices": changed,
        "variable_span_residue_indices": [index + 1 for index in span],
        "variable_labels_before": labels[span_start:span_end],
        "variable_labels_after": expected,
        "variable_pairs": pair_records,
        "pair_count": pair_count,
        "canonical_pair_count": canonical_pair_count,
        "t_stem_49_65_anchor_confirmed": True,
        "structure_hit_start_residue_index": hit_start + 1,
        "answer_free": True,
        "gold_coordinates_used": False,
        "rule": "sprinzl_variable_arm_structure_projection_v1",
        "variable_loop_dialect_policy": (
            "preserve_existing_ordered_v1_v5_subset_without_new_homology_claim"
            if pair_count
            else "not_applicable"
        ),
    }


__all__ = [
    "assess_variable_boundary_reference_agreement",
    "assess_variable_boundary_ambiguity",
    "reconcile_variable_boundary",
]
