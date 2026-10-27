from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.input_evidence import assess_input_evidence, normalize_sequence
from backend.domain.alignment.postcheck.variable_loop import (
    _check_unpaired_balanced_extension,
    _pair_hint_from_struct,
)
from backend.domain.alignment.safety.validation import validate_alignment
from backend.domain.alignment.sprinzl_coordinates import (
    SPRINZL_PARENT_INDEX,
    SPRINZL_PARENT_ORDER,
)
from backend.domain.alignment.sprinzl_numbering_manual import (
    ACCEPTOR_PAIR_MAP,
    ANTICODON_PAIR_MAP,
    D_OPTIONAL_FAMILIES,
    D_STEM_PAIR_MAP,
    T_STEM_PAIR_MAP,
    VARIABLE_PAIR_MAP,
    VARIABLE_UNPAIRED_LABELS,
)
from backend.domain.alignment.variable_boundary import assess_variable_bridge

from ..contracts import AlignmentRequest, RootSnapshot
from .acceptor_tail_reconciliation import merged_annotation_meta


@dataclass(slots=True)
class HardGuardResult:
    valid: bool
    reasons: List[str] = field(default_factory=list)


_SPRINZL_PARENT_ORDER = list(SPRINZL_PARENT_ORDER)
_SPRINZL_PARENT_INDEX = dict(SPRINZL_PARENT_INDEX)
_INSERTION_RE = re.compile(r"^(.+?)i(\d+)$")


def _parse_label(label: str) -> tuple[str, int]:
    raw = str(label or "").strip()
    m = _INSERTION_RE.match(raw)
    if m:
        return m.group(1), int(m.group(2))
    return raw, 0


def _order_key(label: str) -> tuple[int, int, int]:
    parent, ins_idx = _parse_label(label)
    parent_idx = _SPRINZL_PARENT_INDEX[parent]
    if ins_idx > 0:
        return (parent_idx, 1, ins_idx)
    return (parent_idx, 0, 0)


def _norm_base(raw: str) -> str:
    s = str(raw or "").strip().upper().replace("T", "U")
    return s if s in {"A", "U", "G", "C", "N"} else "-"


def _build_idx(labels: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i, label in enumerate(labels):
        if label and label not in out:
            out[label] = i
    return out


def _validate_d_optional_families(
    labels: List[str],
    template_row: List[str],
    sequence_row: List[str],
) -> List[str]:
    reasons: List[str] = []
    idx = _build_idx(labels)
    families: Tuple[Tuple[str, ...], ...] = D_OPTIONAL_FAMILIES
    for family in families:
        present = [label for label in family if label in idx]
        if len(present) < 2:
            continue
        template_all_gap = all(_norm_base(template_row[idx[label]]) == "-" for label in present)
        if not template_all_gap:
            continue
        for higher, lower in zip(present, present[1:]):
            if _norm_base(sequence_row[idx[higher]]) == "-" and _norm_base(sequence_row[idx[lower]]) != "-":
                reasons.append(f"d_optional_suffix_priority_violation:{higher}<{lower}")
                break
    return reasons


_VAR_STEM5_RE = re.compile(r"^V1([1-7])$")
_VAR_LOOP_RE = re.compile(r"^V([1-5])$")
_VAR_STEM3_RE = re.compile(r"^V2([1-7])$")
_ACCEPTOR_PAIR_MAP = ACCEPTOR_PAIR_MAP
_D_STEM_PAIR_MAP = D_STEM_PAIR_MAP
_ANTICODON_PAIR_MAP = ANTICODON_PAIR_MAP
_T_STEM_PAIR_MAP = T_STEM_PAIR_MAP
_VARIABLE_PAIR_MAP = VARIABLE_PAIR_MAP
_VARIABLE_UNPAIRED_LABELS = VARIABLE_UNPAIRED_LABELS


def _var_class(label: str) -> str | None:
    if label in {"44", "45", "46", "47", "48"}:
        return "core_loop"
    if _VAR_STEM5_RE.match(label):
        return "stem5"
    if _VAR_LOOP_RE.match(label):
        return "loop"
    if _VAR_STEM3_RE.match(label):
        return "stem3"
    return None


def _var_pair_maps(labels: List[str]) -> Tuple[Dict[int, int], Dict[int, int]]:
    left: Dict[int, int] = {}
    right: Dict[int, int] = {}
    for i, label in enumerate(labels):
        m5 = _VAR_STEM5_RE.match(label)
        if m5:
            left[int(m5.group(1))] = i
            continue
        m3 = _VAR_STEM3_RE.match(label)
        if m3:
            right[int(m3.group(1))] = i
    return left, right


def _validate_variable_arm_semantics(labels: List[str], sequence_row: List[str]) -> List[str]:
    reasons: List[str] = []
    left, right = _var_pair_maps(labels)
    for k in sorted(set(left) | set(right)):
        i_left = left.get(k)
        i_right = right.get(k)
        if i_left is None and i_right is not None and _norm_base(sequence_row[i_right]) != "-":
            reasons.append(f"variable_singleton_occupied:V2{k}")
        elif i_right is None and i_left is not None and _norm_base(sequence_row[i_left]) != "-":
            reasons.append(f"variable_singleton_occupied:V1{k}")

    core_indices = [i for i, label in enumerate(labels) if _var_class(label) == "core_loop"]
    if core_indices:
        empty_core = [labels[i] for i in core_indices if _norm_base(sequence_row[i]) == "-"]
        occupied_extra_loops = [label for label, base in zip(labels, sequence_row) if _var_class(label) == "loop" and _norm_base(base) != "-"]
        filled_pairs = sum(
            1
            for k in set(left) & set(right)
            if _norm_base(sequence_row[left[k]]) != "-" and _norm_base(sequence_row[right[k]]) != "-"
        )
        if empty_core and occupied_extra_loops and filled_pairs == 0:
            reasons.append(
                "variable_core_before_extension_violation:"
                f"empty_core={','.join(empty_core)} occupied_loop={','.join(occupied_extra_loops)}"
            )
    return reasons


def _variable_pair_hint_from_snapshot(snapshot: RootSnapshot) -> Optional[int]:
    blocks = list(dict(snapshot.raw_meta or {}).get("agent_blocks") or [])
    for block in blocks:
        if str((block or {}).get("region") or "") != "Variable loop":
            continue
        meta = (block or {}).get("meta")
        if not isinstance(meta, dict):
            continue
        raw_struct = str(meta.get("input_struct") or meta.get("var_struct") or "")
        return _pair_hint_from_struct(raw_struct)
    return None


def _validate_unpaired_variable_priority(snapshot: RootSnapshot) -> List[str]:
    if _variable_pair_hint_from_snapshot(snapshot) != 0:
        return []

    labels = list(snapshot.numbering_row or [])
    sequence_row = list(snapshot.sequence_row or [])
    ok, err = _check_unpaired_balanced_extension(labels, sequence_row, pair_hint=0)
    if ok or not err:
        return []
    return [f"variable_topology_unpaired_priority_violation:{err}"]


def _validate_variable_bridge_convention(
    request: AlignmentRequest,
    snapshot: RootSnapshot,
) -> List[str]:
    domain_hint = str(request.domain_hint or "").strip().lower()
    trna_class = str(request.trna_class or "").strip().lower()
    specialized = dict(snapshot.raw_meta.get("specialized_route") or {})
    route = str(specialized.get("route") or "").strip().lower()
    if (
        "mitochond" in domain_hint
        or route == "mitochondrial"
        or trna_class in {"selenocysteine", "sec"}
    ):
        return []

    evidence = assess_variable_bridge(
        snapshot.numbering_row,
        snapshot.sequence_row,
        require_t_stem_anchor=True,
    )
    status = str(evidence.get("status") or "")
    if status not in {"conflict", "requires_review"}:
        return []
    observed = ",".join(evidence.get("observed_labels") or []) or "(empty)"
    expected = ",".join(evidence.get("expected_labels") or []) or "(empty)"
    reason_code = (
        "variable_bridge_sprinzl_convention_violation"
        if status == "conflict"
        else "variable_bridge_structure_review_required"
    )
    return [f"{reason_code}:observed={observed}:expected={expected}"]


def _append_unique_reason(reasons: List[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def _validate_structure_pair_topology(snapshot: RootSnapshot) -> List[str]:
    reasons: List[str] = []
    id_pairs = list(dict(snapshot.structure_view or {}).get("id_pairs") or [])
    if not id_pairs:
        return reasons

    def _check_fixed_pair(
        *,
        label: str,
        partner: str,
        mapping: Dict[str, str],
        prefix: str,
    ) -> bool:
        expected = mapping.get(label)
        if expected is None:
            return False
        if partner != expected:
            _append_unique_reason(reasons, f"{prefix}:{label}-{partner}")
        return True

    for item in id_pairs:
        left = str((item or {}).get("left_id") or "").strip()
        right = str((item or {}).get("right_id") or "").strip()
        if not left or not right or left == right:
            continue

        if _check_fixed_pair(label=left, partner=right, mapping=_VARIABLE_PAIR_MAP, prefix="variable_topology_pair_violation"):
            continue
        if _check_fixed_pair(label=right, partner=left, mapping=_VARIABLE_PAIR_MAP, prefix="variable_topology_pair_violation"):
            continue
        if left in _VARIABLE_UNPAIRED_LABELS or right in _VARIABLE_UNPAIRED_LABELS:
            _append_unique_reason(reasons, f"variable_topology_loop_paired:{left}-{right}")
            continue
        if _check_fixed_pair(label=left, partner=right, mapping=_ACCEPTOR_PAIR_MAP, prefix="acceptor_topology_pair_violation"):
            continue
        if _check_fixed_pair(label=right, partner=left, mapping=_ACCEPTOR_PAIR_MAP, prefix="acceptor_topology_pair_violation"):
            continue
        if _check_fixed_pair(label=left, partner=right, mapping=_D_STEM_PAIR_MAP, prefix="d_stem_topology_pair_violation"):
            continue
        if _check_fixed_pair(label=right, partner=left, mapping=_D_STEM_PAIR_MAP, prefix="d_stem_topology_pair_violation"):
            continue
        if _check_fixed_pair(label=left, partner=right, mapping=_ANTICODON_PAIR_MAP, prefix="anticodon_topology_pair_violation"):
            continue
        if _check_fixed_pair(label=right, partner=left, mapping=_ANTICODON_PAIR_MAP, prefix="anticodon_topology_pair_violation"):
            continue
        if _check_fixed_pair(label=left, partner=right, mapping=_T_STEM_PAIR_MAP, prefix="t_stem_topology_pair_violation"):
            continue
        _check_fixed_pair(label=right, partner=left, mapping=_T_STEM_PAIR_MAP, prefix="t_stem_topology_pair_violation")
    return reasons


def _validate_acceptor_arm_semantics(
    snapshot: RootSnapshot,
    labels: List[str],
    sequence_row: List[str],
) -> List[str]:
    reasons: List[str] = []
    idx = _build_idx(labels)

    if "-1" in idx and "1" in idx:
        minus1 = _norm_base(sequence_row[idx["-1"]])
        pos1 = _norm_base(sequence_row[idx["1"]])
        if minus1 != "-" and pos1 == "-":
            reasons.append("minus1_without_pos1")

    terminal_insertions = []
    for label, base in zip(labels, sequence_row):
        parent, insertion_index = _parse_label(str(label))
        if (
            insertion_index > 0
            and parent in {"74", "75", "76"}
            and _norm_base(base) != "-"
        ):
            terminal_insertions.append(str(label))
    if terminal_insertions:
        reasons.append(
            "terminal_insertion_not_allowed:"
            + ",".join(terminal_insertions)
        )

    tail_labels = [label for label in ("74", "75", "76") if label in idx]
    if tail_labels:
        tail_indices = [idx[label] for label in tail_labels]
        if tail_indices != list(range(tail_indices[0], tail_indices[0] + len(tail_indices))):
            reasons.append("cca_not_contiguous")

        occupied_tail = [
            label
            for label in tail_labels
            if _norm_base(sequence_row[idx[label]]) != "-"
        ]

        motif = "".join(
            _norm_base(sequence_row[idx[label]]) if label in idx else "-"
            for label in ("74", "75", "76")
        )
        # Coordinates 74-76 are a single reserved group: either a complete
        # CCA occupies all three positions, or all three positions are gaps.
        if occupied_tail and tail_labels != ["74", "75", "76"]:
            reasons.append(
                "cca_coordinates_incomplete:"
                + ",".join(occupied_tail)
            )
        if motif not in {"CCA", "---"}:
            reasons.append(f"cca_motif_invalid:{motif}")

    return reasons


def _validate_cca_evidence(
    request: AlignmentRequest,
    snapshot: RootSnapshot,
    labels: List[str],
    sequence_row: List[str],
) -> List[str]:
    transform = dict(snapshot.raw_meta.get("input_transform") or {})
    evidence_sequence = str(transform.get("mature_sequence") or request.target_seq)
    evidence_input_type = "mature" if transform else request.input_type
    evidence = assess_input_evidence(
        target_seq=evidence_sequence,
        input_type=evidence_input_type,
        domain_hint=request.domain_hint,
        cca_status=request.cca_status,
        intron_status="removed" if transform else request.intron_status,
        trna_class=request.trna_class,
    )
    reasons = [
        f"input_contract_conflict:{item}"
        for item in list(evidence.get("claim_conflicts") or [])
    ]
    invalid_symbols = list(evidence.get("invalid_symbols") or [])
    if invalid_symbols:
        reasons.append(f"input_contract_invalid_symbols:{''.join(invalid_symbols)}")
    idx = _build_idx(labels)
    cca = dict(evidence.get("cca_evidence") or {})
    assignment_allowed = bool(cca.get("coordinate_assignment_allowed"))
    if not all(label in idx for label in ("74", "75", "76")):
        if assignment_allowed:
            reasons.append("cca_coordinates_missing_for_observed_terminal_cca")
        return reasons

    motif = "".join(_norm_base(sequence_row[idx[label]]) for label in ("74", "75", "76"))
    if motif != "CCA":
        if assignment_allowed:
            reasons.append("observed_terminal_cca_not_projected_to_74_76")
        return reasons

    candidate_support = _candidate_cca_support(snapshot)
    claim_status = str(cca.get("status_claim") or "unknown")
    model_supported_assignment = bool(
        evidence_input_type in {"mature", "unknown"}
        and claim_status == "unknown"
        and candidate_support
    )
    if not assignment_allowed and not model_supported_assignment:
        reasons.append("cca_74_76_assignment_not_supported_by_input_evidence")
        return reasons

    if "73" not in idx or _norm_base(sequence_row[idx["73"]]) == "-":
        reasons.append("cca_74_76_require_discriminator_73")

    residue_index = 0
    mapped_indices: Dict[str, int] = {}
    for label, base in zip(labels, sequence_row):
        if _norm_base(base) == "-":
            continue
        residue_index += 1
        if label in {"74", "75", "76"}:
            mapped_indices[label] = residue_index
    sequence_length = len(normalize_sequence(evidence_sequence))
    expected = [sequence_length - 2, sequence_length - 1, sequence_length]
    observed = [mapped_indices.get(label, 0) for label in ("74", "75", "76")]
    if observed != expected:
        reasons.append("cca_74_76_do_not_map_to_terminal_input_residues")
    return reasons


def _candidate_cca_support(snapshot: RootSnapshot) -> str:
    meta = dict(snapshot.raw_meta or {})
    if str(meta.get("numbering_backend") or "") == "cmalign":
        projection = dict(meta.get("cm_portfolio") or {}).get("cca_projection")
        if projection:
            return "confirmed_contract_projection"
        try:
            bit_score = float(dict(meta.get("cm_score") or {}).get("bit_score"))
        except (TypeError, ValueError):
            bit_score = float("-inf")
        if bit_score > 0.0:
            return "cm_structure_homology"
        return ""

    annotation = merged_annotation_meta(meta)
    struct_sequence = normalize_sequence(
        str(annotation.get("struct_file_sequence") or "")
    )
    structure = "".join(str(annotation.get("secondary_structure") or "").split())
    if struct_sequence.endswith("CCA") and structure.endswith("..."):
        return "trnascan_unpaired_terminal_tail"
    return ""


def _validate_unknown_cm_scaffold(
    request: AlignmentRequest,
    snapshot: RootSnapshot,
) -> List[str]:
    meta = dict(snapshot.raw_meta or {})
    if (
        str(request.input_type or "unknown").strip().lower() != "unknown"
        or str(meta.get("numbering_backend") or "") != "cmalign"
        or not dict(meta.get("cm_portfolio") or {})
    ):
        return []

    reasons: List[str] = []
    try:
        bit_score = float(dict(meta.get("cm_score") or {}).get("bit_score"))
    except (TypeError, ValueError):
        bit_score = float("-inf")
    if not math.isfinite(bit_score) or bit_score <= 0.0:
        reasons.append("cm_unknown_input_nonpositive_homology_score")

    observed_pairs = {
        (
            str((item or {}).get("left_id") or "").strip(),
            str((item or {}).get("right_id") or "").strip(),
        )
        for item in list(dict(snapshot.structure_view or {}).get("id_pairs") or [])
    }

    def _family_present(mapping: Dict[str, str]) -> bool:
        return any(
            mapping.get(left) == right or mapping.get(right) == left
            for left, right in observed_pairs
        )

    required_families = {
        "acceptor": _ACCEPTOR_PAIR_MAP,
        "anticodon": _ANTICODON_PAIR_MAP,
        "t_stem": _T_STEM_PAIR_MAP,
    }
    missing = [
        name
        for name, mapping in required_families.items()
        if not _family_present(mapping)
    ]
    if missing:
        reasons.append(
            "cm_unknown_input_core_scaffold_incomplete:" + ",".join(missing)
        )
    return reasons


class HardGuards:
    """
    Shared scientific acceptance guards:
    base preservation, row consistency, legal numbering, and anticodon anchors.
    """

    def validate(self, request: AlignmentRequest, snapshot: RootSnapshot) -> HardGuardResult:
        reasons: List[str] = []
        specialized_route = dict(snapshot.raw_meta.get("specialized_route") or {})
        if specialized_route and not snapshot.numbering_row and not snapshot.sequence_row:
            disposition = str(specialized_route.get("disposition") or "evidence_only")
            return HardGuardResult(
                valid=False,
                reasons=[f"specialized_route_numbering_abstained:{disposition}"],
            )

        row_lengths = [len(snapshot.numbering_row), len(snapshot.template_row), len(snapshot.sequence_row)]
        if len(set(row_lengths)) != 1 or row_lengths[0] == 0:
            return HardGuardResult(valid=False, reasons=["row_length_inconsistent"])

        transform = dict(snapshot.raw_meta.get("input_transform") or {})
        if transform:
            reasons.extend(self._validate_input_transform(request, snapshot, transform))
        elif not validate_alignment(request.target_seq, snapshot.sequence_row):
            reasons.append("base_preservation_failed")

        cm_portfolio = dict(snapshot.raw_meta.get("cm_portfolio") or {})
        if snapshot.raw_meta.get("numbering_backend") == "cmalign" and cm_portfolio:
            selection_policy = str(cm_portfolio.get("selection_policy") or "")
            calibration = dict(cm_portfolio.get("calibration") or {})
            canonical_like_special = bool(
                dict(
                    dict(snapshot.raw_meta.get("specialized_route") or {}).get(
                        "canonical_like_structure"
                    )
                    or {}
                ).get("qualified")
            )
            if (
                selection_policy != "explicit_domain_hint"
                and calibration.get("disposition") != "accepted"
                and not canonical_like_special
            ):
                reasons.append("cm_domain_calibration_abstained")

        labels = [str(label or "").strip() for label in snapshot.numbering_row]
        reasons.extend(
            _validate_cca_evidence(request, snapshot, labels, snapshot.sequence_row)
        )
        seen = set()
        duplicates = set()
        for raw in labels:
            if not raw or raw == "-":
                continue
            if raw in seen:
                duplicates.add(raw)
            seen.add(raw)
        if duplicates:
            reasons.append(f"duplicate_numbering_labels:{','.join(sorted(duplicates))}")

        parent_seen = set()
        next_insertion_idx: dict[str, int] = {}
        prev_key = None
        for label, base in zip(snapshot.numbering_row, snapshot.sequence_row):
            raw = str(label or "").strip()
            if not raw or raw == "-":
                continue
            try:
                number_to_region(raw)
            except ValueError:
                reasons.append(f"illegal_numbering_label:{raw}")
                break
            parent, ins_idx = _parse_label(raw)
            if parent not in _SPRINZL_PARENT_INDEX:
                reasons.append(f"unsupported_sprinzl_parent:{parent}")
                break
            key = _order_key(raw)
            if prev_key is not None and key <= prev_key:
                reasons.append(f"sprinzl_order_violation:{raw}")
                break
            prev_key = key
            if ins_idx > 0:
                if parent not in parent_seen:
                    reasons.append(f"insertion_without_parent:{raw}")
                    break
                expected = next_insertion_idx.get(parent, 1)
                if ins_idx != expected:
                    reasons.append(f"nonconsecutive_insertion_index:{raw}")
                    break
                next_insertion_idx[parent] = expected + 1
            else:
                parent_seen.add(parent)
            if (raw.startswith("34i") or raw.startswith("35i")) and str(base or "").strip() not in {"", "-"}:
                reasons.append(f"illegal_anticodon_insertion_occupancy:{raw}")
                break

        # Keep each independent guard active after an earlier failure. A
        # malformed terminal claim must not hide a second violation such as a
        # forbidden 74i*/75i*/76i* occupancy; 73i* is the permitted
        # representation for an unresolved non-CCA terminal extension.
        reasons.extend(_validate_d_optional_families(labels, snapshot.template_row, snapshot.sequence_row))
        reasons.extend(_validate_variable_arm_semantics(labels, snapshot.sequence_row))
        reasons.extend(_validate_variable_bridge_convention(request, snapshot))
        reasons.extend(_validate_unpaired_variable_priority(snapshot))
        reasons.extend(_validate_structure_pair_topology(snapshot))
        reasons.extend(
            _validate_acceptor_arm_semantics(
                snapshot,
                labels,
                snapshot.sequence_row,
            )
        )
        terminal_coordinate_review = dict(
            dict(snapshot.raw_meta.get("cm_portfolio") or {}).get(
                "terminal_coordinate_review"
            )
            or {}
        )
        if terminal_coordinate_review.get("required"):
            reasons.append(
                "non_cca_terminal_extension_requires_review:"
                + str(terminal_coordinate_review.get("reason") or "unspecified")
            )
        reasons.extend(_validate_unknown_cm_scaffold(request, snapshot))

        ann_meta = dict(snapshot.raw_meta.get("annotation_meta") or {})
        expected_anti = str(ann_meta.get("trnascan_anticodon", "") or "").upper().replace("T", "U")
        if len(expected_anti) == 3:
            idx = {n: i for i, n in enumerate(snapshot.numbering_row)}
            if not all(k in idx for k in ("34", "35", "36")):
                reasons.append("anticodon_anchor_missing")
            else:
                if any(str(snapshot.sequence_row[idx[k]] or "").strip() in {"", "-"} for k in ("34", "35", "36")):
                    reasons.append("anticodon_anchor_gap")
                observed = "".join(
                    str(snapshot.sequence_row[idx[k]] or "").upper().replace("T", "U")
                    for k in ("34", "35", "36")
                )
                if observed != expected_anti:
                    reasons.append("anticodon_anchor_mismatch")

        return HardGuardResult(valid=not reasons, reasons=reasons)

    @staticmethod
    def _validate_input_transform(
        request: AlignmentRequest,
        snapshot: RootSnapshot,
        transform: Dict[str, Any],
    ) -> List[str]:
        reasons: List[str] = []
        raw_sequence = str(transform.get("raw_sequence") or "").upper().replace("T", "U")
        mature_sequence = str(transform.get("mature_sequence") or "").upper().replace("T", "U")
        request_sequence = request.normalized_target_seq().replace("T", "U")
        if raw_sequence != request_sequence:
            reasons.append("input_transform_raw_sequence_mismatch")
        if not validate_alignment(mature_sequence, snapshot.sequence_row):
            reasons.append("input_transform_mature_base_preservation_failed")
        mapping = list(transform.get("raw_to_mature") or [])
        mapped_bases: List[str] = []
        previous_raw_index = 0
        for expected_mature_index, item in enumerate(mapping, start=1):
            try:
                mature_index = int((item or {}).get("mature_residue_index"))
                raw_index = int((item or {}).get("raw_residue_index"))
            except (TypeError, ValueError):
                reasons.append("input_transform_mapping_invalid")
                break
            if mature_index != expected_mature_index or raw_index <= previous_raw_index:
                reasons.append("input_transform_mapping_order_invalid")
                break
            if raw_index < 1 or raw_index > len(raw_sequence):
                reasons.append("input_transform_mapping_index_out_of_range")
                break
            base = raw_sequence[raw_index - 1]
            if str((item or {}).get("base") or "").upper().replace("T", "U") != base:
                reasons.append("input_transform_mapping_base_mismatch")
                break
            mapped_bases.append(base)
            previous_raw_index = raw_index
        if "".join(mapped_bases) != mature_sequence:
            reasons.append("input_transform_mapping_does_not_reconstruct_mature_sequence")
        return reasons


__all__ = ["HardGuards", "HardGuardResult"]
