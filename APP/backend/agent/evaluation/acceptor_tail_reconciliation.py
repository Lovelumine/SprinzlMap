from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence


_VALID_BASES = {"A", "C", "G", "U", "N"}
_CANONICAL_PAIRS = {"AU", "UA", "GC", "CG", "GU", "UG"}
_ACCEPTOR_REGION = "Aminoacyl arm 3' end"
_INSERTION_RE = re.compile(r"^(?P<parent>-?\d+)i(?P<index>\d+)$")


def merged_annotation_meta(raw_meta: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Merge the latest annotation with the agent's retained context.

    Branch snapshots can retain ``agent_ann_meta`` while a later tool writes
    fresher values to ``annotation_meta``. Empty later values must not erase a
    usable structure observation.
    """
    meta = dict(raw_meta or {})
    merged = dict(meta.get("agent_ann_meta") or {})
    for key, value in dict(meta.get("annotation_meta") or {}).items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        merged[key] = value
    return merged


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
    """Normalize tRNAscan-SE's angle-bracket structure alphabet."""
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


def assess_acceptor_tail_ambiguity(snapshot: Any) -> Dict[str, Any] | None:
    """Return a review signal for a CM path that may shift the 3' acceptor tail.

    This detector deliberately does not assert a coordinate. It only identifies
    a narrow CM risk pattern so the Agent may request an independent structure
    endpoint check.
    """
    meta = dict(getattr(snapshot, "raw_meta", {}) or {})
    if str(meta.get("numbering_backend") or "") != "cmalign":
        return None

    previous_audit = dict(meta.get("acceptor_tail_reconciliation") or {})
    previous_result = dict(previous_audit.get("result") or {})
    previous_reason = str(previous_result.get("reason") or "")
    previous_starts = list(previous_result.get("candidate_right_arm_starts") or [])
    if (
        str(previous_audit.get("status") or "") == "unresolved"
        and previous_reason
        == "acceptor_pairs_do_not_define_one_mirrored_seven_residue_arm"
        and previous_starts
    ):
        labels, _bases = _occupied(snapshot)
        return {
            "code": "cm_acceptor_tail_boundary_ambiguity",
            "region": _ACCEPTOR_REGION,
            "observed_tail_labels": list(labels[-14:]),
            "structural_check_reason": previous_reason,
            "candidate_right_arm_starts": previous_starts,
            "endpoint_is_ambiguous": True,
            "trigger_basis": "retained_independent_structure_observation",
            "effect": "request_distinct_template_endpoint_consensus",
            "coordinate_assertion": "none",
        }

    labels, bases = _occupied(snapshot)
    if not labels:
        return None
    annotation = merged_annotation_meta(meta)
    structural_check = reconcile_acceptor_tail(
        target_sequence="".join(bases),
        current_labels=labels,
        current_bases=bases,
        struct_file_sequence=str(annotation.get("struct_file_sequence") or ""),
        secondary_structure=str(annotation.get("secondary_structure") or ""),
        trna_type=str(annotation.get("trnascan_isotype") or ""),
    )
    if bool(structural_check.get("resolved")):
        return {
            "code": "cm_acceptor_tail_boundary_ambiguity",
            "region": _ACCEPTOR_REGION,
            "observed_tail_labels": list(labels[-14:]),
            "changed_residue_count": len(
                list(structural_check.get("changed_residue_indices") or [])
            ),
            "trigger_basis": "trnascan_acceptor_stem_endpoint_disagreement",
            "effect": "request_acceptor_stem_endpoint_reconstruction",
            "coordinate_assertion": "none",
        }

    # The root annotation may lack a usable structure for an unusual record.
    # In that case, only narrow acceptor/T-boundary patterns open the tool.
    suspicious_insertions = []
    for label in labels[-16:]:
        match = _INSERTION_RE.match(label)
        if not match:
            continue
        try:
            parent = int(match.group("parent"))
        except (TypeError, ValueError):
            continue
        if 61 <= parent <= 72:
            suspicious_insertions.append(label)
    missing_discriminator = "72" in labels and "73" not in labels
    if not suspicious_insertions and not missing_discriminator:
        # A short genomic/precursor terminal prefix is easy to mistake for a
        # residue after 73. Request an independent endpoint check when it has
        # not been assigned to a valid complete CCA tail.
        terminal_prefix = "".join(bases[-2:]) if len(bases) >= 2 else ""
        if terminal_prefix == "CC" and labels[-2:] != ["74", "75"]:
            return {
                "code": "cm_acceptor_tail_boundary_ambiguity",
                "region": _ACCEPTOR_REGION,
                "observed_tail_labels": list(labels[-14:]),
                "terminal_prefix": terminal_prefix,
                "trigger_basis": "terminal_cca_prefix_not_mapped_to_74_75",
                "effect": "request_acceptor_stem_endpoint_reconstruction",
                "coordinate_assertion": "none",
            }
        return None
    return {
        "code": "cm_acceptor_tail_boundary_ambiguity",
        "region": _ACCEPTOR_REGION,
        "observed_tail_labels": list(labels[-14:]),
        "suspicious_insertion_labels": suspicious_insertions,
        "missing_discriminator_73": missing_discriminator,
        "structural_check_reason": str(structural_check.get("reason") or ""),
        "candidate_right_arm_starts": list(
            structural_check.get("candidate_right_arm_starts") or []
        ),
        "endpoint_is_ambiguous": bool(
            structural_check.get("candidate_right_arm_starts")
        ),
        "trigger_basis": "narrow_acceptor_tail_label_pattern",
        "effect": "request_acceptor_stem_endpoint_reconstruction",
        "coordinate_assertion": "none",
    }


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
    if stack:
        return None
    return pairs


def _first_open_run(structure: str) -> List[int]:
    index = 0
    while index < len(structure) and structure[index] != ">":
        index += 1
    start = index
    while index < len(structure) and structure[index] == ">":
        index += 1
    return list(range(start, index))


def _acceptor_left_segment(structure: str) -> List[int]:
    """Return the seven-residue 5' acceptor segment before the first loop gap."""
    separator = structure.find("..")
    if separator < 0:
        return []
    segment = list(range(separator))
    if len(segment) != 7:
        return []
    if any(structure[index] not in {">", "."} for index in segment):
        return []
    return segment


def _find_unique_subsequence(sequence: str, subsequence: str) -> int | None:
    if not sequence or not subsequence:
        return None
    starts: List[int] = []
    cursor = sequence.find(subsequence)
    while cursor >= 0:
        starts.append(cursor)
        cursor = sequence.find(subsequence, cursor + 1)
    return starts[0] if len(starts) == 1 else None


def reconcile_acceptor_tail(
    *,
    target_sequence: str,
    current_labels: Sequence[str],
    current_bases: Sequence[str],
    struct_file_sequence: str,
    secondary_structure: str,
    trna_type: str = "",
    minimum_canonical_pairs: int = 5,
) -> Dict[str, Any]:
    """Build an answer-free candidate from an independent tRNA structure.

    The first seven-residue structural segment in the 5' acceptor arm is
    mapped to 1..7, and a uniquely mirrored seven-residue 3' segment is
    mapped to 66..72. A single structurally excluded 5' residue is mapped
    to -1; a His G-1 is recorded as the corresponding biological subtype.
    The first unpaired residue after that stem is assigned 73. Coordinates
    74-76 are assigned only to an observed complete CCA tail. An incomplete
    or non-CCA extension is retained as an observation for review and is not
    converted into Sprinzl tail coordinates. A downstream alignment candidate
    may use 73i* to preserve such an unresolved extension, but never 74-76.
    """
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
    if not structure_sequence or not structure:
        return {
            "resolved": False,
            "reason": "independent_structure_sequence_and_shape_length_mismatch",
        }
    appended_terminal_sequence = ""
    if len(structure_sequence) != len(structure):
        return {
            "resolved": False,
            "reason": "independent_structure_sequence_and_shape_length_mismatch",
        }
    if len(structure_sequence) < len(target) and target.startswith(structure_sequence):
        omitted_terminal = target[len(structure_sequence) :]
        if (
            1 <= len(omitted_terminal) <= 3
            and structure.endswith(".")
        ):
            # tRNAscan-SE may omit an unpaired genomic/post-transcriptional
            # tail from the .struct record. Preserve it as observed sequence
            # evidence, without asserting mature CCA status.
            structure_sequence += omitted_terminal
            structure += "." * len(omitted_terminal)
            appended_terminal_sequence = omitted_terminal
    if len(structure_sequence) != len(structure):
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

    pairs = _parse_pairs(structure)
    if pairs is None:
        return {"resolved": False, "reason": "independent_structure_not_balanced"}
    left_arm = _acceptor_left_segment(structure)
    if not left_arm:
        return {
            "resolved": False,
            "reason": "acceptor_5prime_arm_is_not_a_seven_residue_segment",
            "observed_left_run_length": len(_first_open_run(structure)),
        }

    paired_left = [
        (offset, index, pairs[index])
        for offset, index in enumerate(left_arm)
        if structure[index] == ">" and index in pairs
    ]
    if len(paired_left) < max(1, int(minimum_canonical_pairs)):
        return {
            "resolved": False,
            "reason": "acceptor_structure_has_insufficient_paired_positions",
            "pair_count": len(paired_left),
        }
    right_starts = {
        right - (6 - offset)
        for offset, _left, right in paired_left
    }
    if len(right_starts) != 1:
        return {
            "resolved": False,
            "reason": "acceptor_pairs_do_not_define_one_mirrored_seven_residue_arm",
            "candidate_right_arm_starts": sorted(right_starts),
        }
    right_start = next(iter(right_starts))
    right_arm = list(range(right_start, right_start + 7))
    if right_start < 0 or right_arm[-1] >= len(structure):
        return {
            "resolved": False,
            "reason": "acceptor_3prime_arm_window_is_out_of_structure_range",
        }
    if any(structure[index] not in {"<", "."} for index in right_arm):
        return {
            "resolved": False,
            "reason": "acceptor_3prime_arm_contains_nonterminal_structure",
        }
    for offset, left_index in enumerate(left_arm):
        expected_right = right_start + (6 - offset)
        if structure[left_index] == ">":
            if pairs.get(left_index) != expected_right:
                return {
                    "resolved": False,
                    "reason": "acceptor_pair_mirror_is_inconsistent",
                }
        elif structure[expected_right] != ".":
            return {
                "resolved": False,
                "reason": "acceptor_unpaired_position_is_not_mirrored",
            }

    right_global = [hit_start + index for index in right_arm]
    canonical_pair_count = sum(
        1
        for _offset, left, right in paired_left
        if structure_sequence[left] + structure_sequence[right] in _CANONICAL_PAIRS
    )
    if canonical_pair_count < max(1, int(minimum_canonical_pairs)):
        return {
            "resolved": False,
            "reason": "independent_acceptor_pairs_have_insufficient_canonical_support",
            "canonical_pair_count": canonical_pair_count,
            "pair_count": len(paired_left),
        }

    last_right_local = right_arm[-1]
    if any(char != "." for char in structure[last_right_local + 1 :]):
        return {
            "resolved": False,
            "reason": "additional_structure_after_acceptor_stem_endpoint",
        }

    tail_global_start = hit_start + last_right_local + 1
    if tail_global_start >= len(target):
        return {
            "resolved": False,
            "reason": "independent_structure_has_no_discriminator_candidate",
        }
    tail_length = len(target) - tail_global_start
    if tail_length > 4:
        return {
            "resolved": False,
            "reason": "discriminator_and_terminal_extension_exceed_four_residues",
            "tail_length": tail_length,
        }

    label_by_residue: Dict[int, str] = {}
    normalized_type = str(trna_type or "").strip().lower()
    left_arm_reconciled = hit_start == 0
    left_arm_status = "mapped_from_complete_structure_hit"
    if hit_start:
        histidine_g_minus_1 = (
            hit_start == 1
            and target[0] == "G"
            and normalized_type in {"his", "histidine"}
        )
        if hit_start == 1:
            label_by_residue[0] = "-1"
            left_arm_reconciled = True
            left_arm_status = (
                "histidine_g_minus_1_and_positions_1_7_mapped"
                if histidine_g_minus_1
                else "single_structural_5prime_extension_mapped_to_minus_1"
            )
        else:
            left_arm_status = "preserved_due_to_unresolved_5prime_extension"

    terminal_extension = target[tail_global_start + 1 :]
    if terminal_extension == "CCA":
        terminal_motif_observation = "complete_cca"
        terminal_coordinate_extension = "CCA"
    elif terminal_extension in {"C", "CC"}:
        return {
            "resolved": False,
            "reason": "incomplete_terminal_cca_not_mappable_to_74_76",
            "discriminator_residue_index": tail_global_start + 1,
            "terminal_extension_residue_indices": [
                index + 1 for index in range(tail_global_start, len(target))
            ],
            "tail_length": tail_length,
            "tail_assignment_basis": "structure_endpoint_observed_incomplete_terminal",
            "terminal_coordinate_labels": ["73"],
            "terminal_extension_sequence": terminal_extension,
            "terminal_motif_observation": "incomplete_terminal",
            "mature_cca_claim": "not_asserted",
            "independent_structure_terminal_suffix_appended": bool(
                appended_terminal_sequence
            ),
            "independent_structure_terminal_suffix": appended_terminal_sequence,
            "left_arm_reconciled": left_arm_reconciled,
            "left_arm_status": left_arm_status,
            "answer_free": True,
            "gold_coordinates_used": False,
        }
    elif terminal_extension:
        # A non-CCA suffix may be biologically meaningful, but it is not a
        # valid occupant of Sprinzl coordinates 74-76.
        return {
            "resolved": False,
            "reason": "non_cca_terminal_extension_not_mappable_to_74_76",
            "discriminator_residue_index": tail_global_start + 1,
            "terminal_extension_residue_indices": [
                index + 1 for index in range(tail_global_start, len(target))
            ],
            "tail_length": tail_length,
            "tail_assignment_basis": "structure_endpoint_observed_extension",
            "terminal_coordinate_labels": ["73"],
            "terminal_extension_sequence": terminal_extension,
            "terminal_motif_observation": "non_cca_extension",
            "mature_cca_claim": "not_asserted",
            "independent_structure_terminal_suffix_appended": bool(
                appended_terminal_sequence
            ),
            "independent_structure_terminal_suffix": appended_terminal_sequence,
            "left_arm_reconciled": left_arm_reconciled,
            "left_arm_status": left_arm_status,
            "answer_free": True,
            "gold_coordinates_used": False,
        }
    else:
        terminal_motif_observation = "no_extension"
        terminal_coordinate_extension = ""

    if left_arm_reconciled:
        for offset, local_index in enumerate(left_arm):
            label_by_residue[hit_start + local_index] = str(1 + offset)
    for offset, residue_index in enumerate(right_global):
        label_by_residue[residue_index] = str(66 + offset)

    label_by_residue[tail_global_start] = "73"
    for offset, residue_index in enumerate(
        range(tail_global_start + 1, len(target))
    ):
        label_by_residue[residue_index] = str(74 + offset)

    candidate_labels = list(labels)
    changed_indices: List[int] = []
    for residue_index, label in sorted(label_by_residue.items()):
        if residue_index >= len(candidate_labels):
            return {
                "resolved": False,
                "reason": "candidate_residue_index_out_of_range",
            }
        if candidate_labels[residue_index] != label:
            changed_indices.append(residue_index + 1)
            candidate_labels[residue_index] = label

    if not changed_indices:
        return {
            "resolved": False,
            "reason": "structure_endpoint_agrees_with_current_candidate",
        }

    occupied_candidate = [label for label in candidate_labels if label]
    if len(set(occupied_candidate)) != len(occupied_candidate):
        return {
            "resolved": False,
            "reason": "candidate_contains_duplicate_occupied_labels",
        }

    structure_pairs = []
    for left, right in sorted(
        ((left, right) for left, right in pairs.items() if left < right),
        key=lambda item: (item[0], item[1]),
    ):
        left_global = hit_start + left
        right_global = hit_start + right
        if left_global >= len(candidate_labels) or right_global >= len(candidate_labels):
            continue
        left_label = candidate_labels[left_global]
        right_label = candidate_labels[right_global]
        if not left_label or not right_label or left_label == right_label:
            continue
        structure_pairs.append(
            {
                "left_id": left_label,
                "right_id": right_label,
                "left_index": left_global + 1,
                "right_index": right_global + 1,
            }
        )

    return {
        "resolved": True,
        "reason": "independent_structure_recovers_acceptor_stem_endpoint",
        "candidate_labels": candidate_labels,
        "changed_residue_indices": changed_indices,
        "acceptor_pairs": [
            {
                "left_structure_index": left,
                "right_structure_index": right,
                "left_residue_index": hit_start + left + 1,
                "right_residue_index": hit_start + right + 1,
                "right_label": str(72 - offset),
            }
            for offset, left, right in paired_left
        ],
        "discriminator_residue_index": tail_global_start + 1,
        "terminal_extension_residue_indices": [
            index + 1 for index in range(tail_global_start, len(target))
        ],
        "hit_start_residue_index": hit_start + 1,
        "hit_end_residue_index": hit_start + len(structure_sequence),
        "canonical_pair_count": canonical_pair_count,
        "pair_count": len(paired_left),
        "tail_length": tail_length,
        "tail_assignment_basis": "structure_endpoint_observed_complete_cca",
        "terminal_coordinate_labels": [
            "73",
            *[
                str(74 + offset)
                for offset in range(len(terminal_coordinate_extension))
            ],
        ],
        "terminal_extension_sequence": terminal_extension,
        "terminal_motif_observation": terminal_motif_observation,
        "mature_cca_claim": "not_asserted",
        "independent_structure_terminal_suffix_appended": bool(
            appended_terminal_sequence
        ),
        "independent_structure_terminal_suffix": appended_terminal_sequence,
        "left_arm_reconciled": left_arm_reconciled,
        "left_arm_status": left_arm_status,
        "structure_pairs": structure_pairs,
        "answer_free": True,
        "gold_coordinates_used": False,
    }


__all__ = [
    "assess_acceptor_tail_ambiguity",
    "merged_annotation_meta",
    "reconcile_acceptor_tail",
]
