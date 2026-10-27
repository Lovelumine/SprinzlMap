from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List, Tuple

from ..contracts import DiagnosticsReport, RootSnapshot, RouteReport
from .hard_guards import HardGuardResult


@dataclass(slots=True)
class RankedSnapshot:
    snapshot: RootSnapshot
    score: float
    guard: HardGuardResult
    diagnostics: DiagnosticsReport
    route: RouteReport


_CANONICAL_PAIRS = {"AU", "UA", "GC", "CG", "GU", "UG"}
_AA_PAIR_LABELS: List[Tuple[str, str]] = [
    ("1", "72"),
    ("2", "71"),
    ("3", "70"),
    ("4", "69"),
    ("5", "68"),
    ("6", "67"),
    ("7", "66"),
]
_D_PAIR_LABELS: List[Tuple[str, str]] = [
    ("10", "25"),
    ("11", "24"),
    ("12", "23"),
    ("13", "22"),
]
_AC_PAIR_LABELS: List[Tuple[str, str]] = [
    ("27", "43"),
    ("28", "42"),
    ("29", "41"),
    ("30", "40"),
    ("31", "39"),
]
_T_PAIR_LABELS: List[Tuple[str, str]] = [
    ("49", "65"),
    ("50", "64"),
    ("51", "63"),
    ("52", "62"),
    ("53", "61"),
]
_VAR_STEM5_RE = re.compile(r"^V1([1-7])$")
_VAR_LOOP_RE = re.compile(r"^V([1-5])$")
_VAR_STEM3_RE = re.compile(r"^V2([1-7])$")


def _norm_base(raw: str) -> str:
    s = str(raw or "").strip().upper().replace("T", "U")
    return s if s in {"A", "U", "G", "C", "N"} else "-"


def _build_idx(labels: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i, label in enumerate(labels):
        key = str(label or "").strip()
        if key and key not in out:
            out[key] = i
    return out


def _pair_stats(labels: List[str], sequence_row: List[str], pair_labels: List[Tuple[str, str]]) -> Dict[str, int]:
    idx = _build_idx(labels)
    total = 0
    canonical = 0
    gap_pairs = 0
    noncanonical = 0
    for left, right in pair_labels:
        if left not in idx or right not in idx:
            continue
        total += 1
        a = _norm_base(sequence_row[idx[left]])
        b = _norm_base(sequence_row[idx[right]])
        if a == "-" or b == "-":
            gap_pairs += 1
            continue
        if a + b in _CANONICAL_PAIRS:
            canonical += 1
        else:
            noncanonical += 1
    return {
        "total_pairs": total,
        "canonical_pairs": canonical,
        "gap_pairs": gap_pairs,
        "noncanonical_pairs": noncanonical,
        "unmatched_pairs": gap_pairs + noncanonical,
    }


def _acceptor_metrics(labels: List[str], sequence_row: List[str]) -> Dict[str, object]:
    idx = _build_idx(labels)
    pair_states: List[str] = []
    total = canonical = gap_pairs = noncanonical = 0
    for left, right in _AA_PAIR_LABELS:
        if left not in idx or right not in idx:
            pair_states.append("missing")
            continue
        total += 1
        a = _norm_base(sequence_row[idx[left]])
        b = _norm_base(sequence_row[idx[right]])
        if a == "-" or b == "-":
            gap_pairs += 1
            pair_states.append("gap")
            continue
        if a + b in _CANONICAL_PAIRS:
            canonical += 1
            pair_states.append("canonical")
        else:
            noncanonical += 1
            pair_states.append("noncanonical")

    run_max = 0
    run = 0
    for st in pair_states:
        if st == "canonical":
            run += 1
            run_max = max(run_max, run)
        else:
            run = 0
    all7 = bool(len(pair_states) == len(_AA_PAIR_LABELS) and all(st == "canonical" for st in pair_states))

    minus1_without_pos1 = False
    if "-1" in idx and "1" in idx:
        minus1_without_pos1 = _norm_base(sequence_row[idx["-1"]]) != "-" and _norm_base(sequence_row[idx["1"]]) == "-"

    cca_valid = False
    cca_contiguous = False
    cca_motif = ""
    if all(k in idx for k in ("74", "75", "76")):
        i74, i75, i76 = idx["74"], idx["75"], idx["76"]
        cca_contiguous = i74 + 1 == i75 and i75 + 1 == i76
        cca_motif = "".join(_norm_base(sequence_row[idx[k]]) for k in ("74", "75", "76"))
        cca_valid = cca_contiguous and cca_motif in {"CCA", "---"}

    return {
        "total_pairs": total,
        "canonical_pairs": canonical,
        "gap_pairs": gap_pairs,
        "noncanonical_pairs": noncanonical,
        "unmatched_pairs": gap_pairs + noncanonical,
        "canonical_run_max": run_max,
        "all7_contiguous_canonical": all7,
        "minus1_without_pos1": minus1_without_pos1,
        "cca_valid": cca_valid,
        "cca_contiguous": cca_contiguous,
        "cca_motif": cca_motif,
    }


def _d_optional_violations(labels: List[str], template_row: List[str], sequence_row: List[str]) -> int:
    idx = _build_idx(labels)
    count = 0
    families = (("17", "17a"), ("20", "20a", "20b"))
    for family in families:
        present = [label for label in family if label in idx]
        if len(present) < 2:
            continue
        template_all_gap = all(_norm_base(template_row[idx[label]]) == "-" for label in present)
        if not template_all_gap:
            continue
        for higher, lower in zip(present, present[1:]):
            if _norm_base(sequence_row[idx[higher]]) == "-" and _norm_base(sequence_row[idx[lower]]) != "-":
                count += 1
                break
    return count


def _variable_arm_metrics(labels: List[str], sequence_row: List[str]) -> Dict[str, int]:
    left: Dict[int, int] = {}
    right: Dict[int, int] = {}
    core_indices: List[int] = []
    loop_indices: List[int] = []
    for i, label in enumerate(labels):
        key = str(label or "").strip()
        m5 = _VAR_STEM5_RE.match(key)
        if m5:
            left[int(m5.group(1))] = i
            continue
        m3 = _VAR_STEM3_RE.match(key)
        if m3:
            right[int(m3.group(1))] = i
            continue
        if key in {"44", "45", "46", "47", "48"}:
            core_indices.append(i)
            continue
        if _VAR_LOOP_RE.match(key):
            loop_indices.append(i)

    singleton_occupied = 0
    paired_filled = 0
    for k in sorted(set(left) | set(right)):
        i_left = left.get(k)
        i_right = right.get(k)
        if i_left is not None and i_right is not None:
            a = _norm_base(sequence_row[i_left])
            b = _norm_base(sequence_row[i_right])
            if a != "-" and b != "-":
                paired_filled += 1
        elif i_left is not None and _norm_base(sequence_row[i_left]) != "-":
            singleton_occupied += 1
        elif i_right is not None and _norm_base(sequence_row[i_right]) != "-":
            singleton_occupied += 1

    empty_core = [i for i in core_indices if _norm_base(sequence_row[i]) == "-"]
    occupied_extra_loops = [i for i in loop_indices if _norm_base(sequence_row[i]) != "-"]
    core_before_extension = int(bool(empty_core and occupied_extra_loops and paired_filled == 0))
    return {
        "singleton_occupied": singleton_occupied,
        "paired_stem_occupancy": paired_filled,
        "core_before_extension_violation": core_before_extension,
    }


def _anticodon_anchor_status(labels: List[str], sequence_row: List[str], expected_anticodon: str) -> str:
    if len(expected_anticodon) != 3:
        return "unknown"
    idx = _build_idx(labels)
    if not all(k in idx for k in ("34", "35", "36")):
        return "missing"
    observed = "".join(_norm_base(sequence_row[idx[k]]) for k in ("34", "35", "36"))
    if "-" in observed:
        return "gap"
    return "exact" if observed == expected_anticodon else "mismatch"


def _structural_metrics(snapshot: RootSnapshot, diagnostics: DiagnosticsReport) -> Dict[str, object]:
    labels = [str(label or "").strip() for label in list(snapshot.numbering_row or [])]
    sequence_row = list(snapshot.sequence_row or [])
    template_row = list(snapshot.template_row or [])
    expected_anti = str(diagnostics.raw_signals.get("preferred_anticodon") or "")
    return {
        "acceptor": _acceptor_metrics(labels, sequence_row),
        "d_stem": {
            **_pair_stats(labels, sequence_row, _D_PAIR_LABELS),
            "optional_suffix_violations": _d_optional_violations(labels, template_row, sequence_row),
        },
        "anticodon_stem": {
            **_pair_stats(labels, sequence_row, _AC_PAIR_LABELS),
            "anchor_status": _anticodon_anchor_status(labels, sequence_row, expected_anti),
        },
        "t_stem": _pair_stats(labels, sequence_row, _T_PAIR_LABELS),
        "variable_arm": _variable_arm_metrics(labels, sequence_row),
    }


class OnlineRanker:
    """
    Heuristic online ranker for valid snapshots.
    """

    def score(
        self,
        snapshot: RootSnapshot,
        diagnostics: DiagnosticsReport,
        route: RouteReport,
        guard: HardGuardResult,
    ) -> float:
        if not guard.valid:
            return float("-inf")

        metrics = _structural_metrics(snapshot, diagnostics)
        acceptor = dict(metrics.get("acceptor") or {})
        d_stem = dict(metrics.get("d_stem") or {})
        anticodon = dict(metrics.get("anticodon_stem") or {})
        t_stem = dict(metrics.get("t_stem") or {})
        variable = dict(metrics.get("variable_arm") or {})

        structural_score = 0.0
        structural_score += float(acceptor.get("canonical_pairs", 0)) * 5.5
        structural_score -= float(acceptor.get("unmatched_pairs", 0)) * 8.5
        structural_score += float(acceptor.get("canonical_run_max", 0)) * 1.25
        if bool(acceptor.get("all7_contiguous_canonical")):
            structural_score += 8.0
        if bool(acceptor.get("cca_valid")):
            structural_score += 2.5
        else:
            structural_score -= 9.0
        if bool(acceptor.get("minus1_without_pos1")):
            structural_score -= 5.0

        structural_score += float(d_stem.get("canonical_pairs", 0)) * 3.5
        structural_score -= float(d_stem.get("unmatched_pairs", 0)) * 5.0
        structural_score -= float(d_stem.get("optional_suffix_violations", 0)) * 3.0

        structural_score += float(anticodon.get("canonical_pairs", 0)) * 4.5
        structural_score -= float(anticodon.get("unmatched_pairs", 0)) * 6.0
        anchor_status = str(anticodon.get("anchor_status") or "unknown")
        structural_score += {
            "exact": 10.0,
            "unknown": 0.0,
            "missing": -10.0,
            "gap": -12.0,
            "mismatch": -15.0,
        }.get(anchor_status, -6.0)

        structural_score += float(t_stem.get("canonical_pairs", 0)) * 3.5
        structural_score -= float(t_stem.get("unmatched_pairs", 0)) * 5.0

        structural_score -= float(variable.get("singleton_occupied", 0)) * 4.0
        structural_score -= float(variable.get("core_before_extension_violation", 0)) * 3.0

        score = structural_score
        score -= diagnostics.warning_count * 1.5
        for warning in diagnostics.warning_types:
            score -= {
                "anticodon": 2.5,
                "boundary": 1.5,
                "rollback": 2.0,
                "insertion": 2.0,
                "llm": 0.75,
                "generic": 0.75,
            }.get(warning, 0.75)
        score -= len(diagnostics.empty_regions) * 3.0
        if diagnostics.boundary_resolved:
            score -= 0.75
        if diagnostics.trnascan_hit:
            score += 1.0
        if diagnostics.template_score is not None:
            score += max(-1.5, min(3.0, float(diagnostics.template_score) / 40.0))
        if diagnostics.template_margin is not None:
            score += max(-0.75, min(1.5, diagnostics.template_margin / 2.0))
        family_rank = int(diagnostics.raw_signals.get("selected_family_match_rank") or 0)
        top_family_rank = int(diagnostics.raw_signals.get("top_family_match_rank") or family_rank)
        strong_family_candidates = int(diagnostics.raw_signals.get("strong_family_candidates") or 0)
        family_rank_gap = int(diagnostics.raw_signals.get("family_rank_gap") or 0)
        isotype_match = int(diagnostics.raw_signals.get("selected_isotype_match") or 0)
        anticodon_match = int(diagnostics.raw_signals.get("selected_anticodon_match") or 0)
        preferred_anticodon = str(diagnostics.raw_signals.get("preferred_anticodon") or "")
        if diagnostics.trnascan_hit:
            score += {
                3: 3.0,
                2: 2.0,
                1: 0.5,
                0: -3.0,
            }.get(family_rank, -1.0)
            score += 1.5 if isotype_match else -3.0
            if preferred_anticodon:
                score += 2.5 if anticodon_match else -5.0
            elif family_rank <= 1:
                score -= 1.0
            if structural_score >= 105.0:
                if not isotype_match:
                    score -= 2.0
                if preferred_anticodon and not anticodon_match:
                    score -= 3.0
            if strong_family_candidates == 0:
                score -= 1.5
            if family_rank_gap > 0:
                score -= min(4.0, 2.0 * family_rank_gap)
            if bool(diagnostics.raw_signals.get("selected_family_rescue_selected")):
                score += 0.75
        if route.route != "canonical":
            score -= 4.0
        if route.out_of_scope:
            score -= 18.0
        if snapshot.source_action == "root_snapshot":
            score += 0.25
            if (
                structural_score >= 105.0
                and diagnostics.trnascan_hit
                and isotype_match
                and (not preferred_anticodon or anticodon_match)
            ):
                score += 2.25
        elif snapshot.source_action in {"compact_insertions", "reanchor_anticodon"}:
            score += 0.5
        branch_meta = dict((snapshot.raw_meta or {}).get("agent_branch") or {})
        compare_meta = dict((snapshot.raw_meta or {}).get("agent_branch_compare") or {})
        special_meta = dict((snapshot.raw_meta or {}).get("agent_specialized_status") or {})
        notes = list(branch_meta.get("notes") or [])
        if any("rollback" in str(note) for note in notes):
            score -= 2.0
        if any("applied" in str(note) for note in notes):
            score += 0.5
        parent_structural_score = float(compare_meta.get("parent_structural_score") or 0.0)
        family_rank_delta = int(compare_meta.get("family_rank_delta") or 0)
        template_changed = bool(compare_meta.get("template_changed"))
        if snapshot.source_action in {
            "switch_template_topk",
            "switch_template_family_constrained",
            "switch_template_domain_or_clade_specific",
        } and template_changed:
            if family_rank_delta <= 0 and parent_structural_score >= 100.0:
                score -= 8.0
            elif family_rank_delta <= 0 and parent_structural_score >= 95.0:
                score -= 4.0
        if bool(special_meta.get("abstain_like")):
            score -= 12.0
        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        snapshot.raw_meta["agent_structural_rank"] = {
            "structural_score": round(structural_score, 4),
            "isotype_match": isotype_match,
            "anticodon_match": anticodon_match,
            "family_match_rank": family_rank,
            "acceptor": acceptor,
            "d_stem": d_stem,
            "anticodon_stem": anticodon,
            "t_stem": t_stem,
            "variable_arm": variable,
        }
        return round(score, 4)


__all__ = ["OnlineRanker", "RankedSnapshot"]
