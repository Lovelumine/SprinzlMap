from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.infra.llm.provider_config import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_MINIMAX_MODEL,
)

from ..runtime_helpers import load_runtime_values, runtime_bool, runtime_str


logger = logging.getLogger(__name__)

BOUNDARY_SCORING_POLICY = (
    "anticodon_span,anticodon_motif,structure_cut_pairs,"
    "template_length_error,movement"
)


def postcheck_runtime_overrides(
    llm_reasoning: bool,
    runtime_values_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime_values = load_runtime_values(runtime_values_override)
    provider = runtime_str("LLM_PROVIDER", "ollama", runtime_values=runtime_values).lower()
    if not llm_reasoning:
        if provider == "deepseek":
            requested_model = runtime_str(
                "DEEPSEEK_MODEL",
                DEFAULT_DEEPSEEK_MODEL,
                runtime_values=runtime_values,
            )
            deepseek_thinking = runtime_str(
                "DEEPSEEK_THINKING",
                "",
                runtime_values=runtime_values,
            ).lower()
            if (
                "deepseek-v4" in requested_model.lower()
                and deepseek_thinking in {"disabled", "false", "0", "off", "no"}
            ):
                return {
                    "DEEPSEEK_MODEL": requested_model,
                    "DEEPSEEK_THINKING": "disabled",
                }
            return {"DEEPSEEK_MODEL": "deepseek-chat"}
        return {}
    if provider == "minimax":
        return {
            "LLM_PROVIDER": "minimax",
            "MINIMAX_MODEL": runtime_str(
                "MINIMAX_REASONING_MODEL",
                runtime_str(
                    "MINIMAX_MODEL",
                    DEFAULT_MINIMAX_MODEL,
                    runtime_values=runtime_values,
                ),
                runtime_values=runtime_values,
            ),
            "LLM_MAX_TOKENS": 8192,
            "LLM_READ_TIMEOUT": 180,
            "LLM_HARD_BUDGET": 180,
        }
    if provider == "deepseek":
        return {
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_MODEL": "deepseek-reasoner",
            "LLM_MAX_TOKENS": 8192,
            "LLM_READ_TIMEOUT": 180,
            "LLM_HARD_BUDGET": 180,
        }
    return {}


def seq_chars(raw: str) -> List[str]:
    out: List[str] = []
    for ch in str(raw or "").upper():
        if ch in ("A", "U", "G", "C", "N"):
            out.append(ch)
        elif ch == "T":
            out.append("U")
    return out


def norm_rna_token(raw: str) -> str:
    return "".join(seq_chars(str(raw or "")))


def safe_int_value(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def struct_chars(raw: str) -> List[str]:
    return [ch for ch in str(raw or "") if not ch.isspace()]


def template_non_gap_len(template_seq: str) -> int:
    return sum(
        1
        for ch in str(template_seq or "")
        if (not ch.isspace()) and ch != "-"
    )


def _structure_pairs(structure: Sequence[str]) -> List[Tuple[int, int]]:
    """Parse tRNAscan angle brackets and common dot-bracket variants."""
    opener_to_closer = {">": "<", "(": ")", "[": "]", "{": "}"}
    closer_to_opener = {closer: opener for opener, closer in opener_to_closer.items()}
    stacks: Dict[str, List[int]] = {opener: [] for opener in opener_to_closer}
    pairs: List[Tuple[int, int]] = []
    for index, token in enumerate(structure):
        if token in opener_to_closer:
            stacks[token].append(index)
            continue
        opener = closer_to_opener.get(token)
        if opener is None or not stacks[opener]:
            continue
        pairs.append((stacks[opener].pop(), index))
    return pairs


def _structure_is_balanced(structure: Sequence[str]) -> bool:
    opener_to_closer = {">": "<", "(": ")", "[": "]", "{": "}"}
    closer_to_opener = {closer: opener for opener, closer in opener_to_closer.items()}
    stacks: Dict[str, List[int]] = {opener: [] for opener in opener_to_closer}
    for index, token in enumerate(structure):
        if token in opener_to_closer:
            stacks[token].append(index)
            continue
        opener = closer_to_opener.get(token)
        if opener is None:
            continue
        if not stacks[opener]:
            return False
        stacks[opener].pop()
    return all(not stack for stack in stacks.values())


def _pairs_crossing_cuts(
    pairs: Sequence[Tuple[int, int]],
    cuts: Sequence[int],
) -> int:
    return sum(
        1
        for left, right in pairs
        for cut in cuts
        if left < cut <= right
    )


def _build_candidate_from_deltas(
    bounds: List[int],
    d_left: int,
    d_right: int,
    n_total: int,
    region_order: List[str],
    tpl_lens: List[int],
    idx_d: int,
    idx_ac: int,
    idx_var: int,
) -> Optional[Dict[str, Any]]:
    candidate_bounds = list(bounds)
    candidate_bounds[idx_ac] = bounds[idx_ac] + int(d_left)
    candidate_bounds[idx_ac + 1] = bounds[idx_ac + 1] + int(d_right)

    if any(value < 0 or value > n_total for value in candidate_bounds):
        return None
    if any(
        candidate_bounds[index] > candidate_bounds[index + 1]
        for index in range(len(candidate_bounds) - 1)
    ):
        return None

    lengths = [
        candidate_bounds[index + 1] - candidate_bounds[index]
        for index in range(len(region_order))
    ]
    if any(lengths[index] < 1 for index in (idx_d, idx_ac, idx_var)):
        return None

    template_error = sum(
        abs(lengths[index] - tpl_lens[index])
        for index in (idx_d, idx_ac, idx_var)
    )
    return {
        "bounds": candidate_bounds,
        "lens": lengths,
        "d_left": int(d_left),
        "d_right": int(d_right),
        "obj": int(template_error),
        "move": abs(int(d_left)) + abs(int(d_right)),
    }


def _candidate_evidence(
    candidate: Dict[str, Any],
    *,
    raw_seq: str,
    expected_anticodon: str,
    expected_anticodon_span: Optional[Tuple[int, int]],
    structure_pairs: Sequence[Tuple[int, int]],
    structure_available: bool,
    idx_ac: int,
) -> Dict[str, Any]:
    bounds = list(candidate["bounds"])
    left = int(bounds[idx_ac])
    right = int(bounds[idx_ac + 1])
    ac_sequence = raw_seq[left:right]

    span_available = expected_anticodon_span is not None
    span_contained: Optional[bool] = None
    if expected_anticodon_span is not None:
        span_start, span_end = expected_anticodon_span
        span_contained = left <= (span_start - 1) and span_end <= right

    motif_available = bool(expected_anticodon)
    motif_contained: Optional[bool] = None
    if motif_available:
        motif_contained = expected_anticodon in ac_sequence

    crossing_pairs = (
        _pairs_crossing_cuts(
            structure_pairs,
            (int(bounds[idx_ac]), int(bounds[idx_ac + 1])),
        )
        if structure_available
        else 0
    )
    score_key = (
        int(span_available and not span_contained),
        int(motif_available and not motif_contained),
        int(crossing_pairs),
        int(candidate["obj"]),
        int(candidate["move"]),
    )
    return {
        "anticodon_span_available": span_available,
        "anticodon_span_contained": span_contained,
        "anticodon_motif_available": motif_available,
        "anticodon_motif_contained": motif_contained,
        "structure_available": bool(structure_available),
        "structure_cut_pairs": int(crossing_pairs),
        "template_length_error": int(candidate["obj"]),
        "movement": int(candidate["move"]),
        "score_key": list(score_key),
    }


def _enumerate_boundary_candidates(
    *,
    bounds: List[int],
    n_total: int,
    region_order: List[str],
    tpl_lens: List[int],
    idx_d: int,
    idx_ac: int,
    idx_var: int,
    raw_seq: str,
    expected_anticodon: str,
    expected_anticodon_span: Optional[Tuple[int, int]],
    structure_pairs: Sequence[Tuple[int, int]],
    structure_available: bool,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for d_left in range(-2, 3):
        for d_right in range(-2, 3):
            candidate = _build_candidate_from_deltas(
                bounds,
                d_left,
                d_right,
                n_total,
                region_order,
                tpl_lens,
                idx_d,
                idx_ac,
                idx_var,
            )
            if candidate is None:
                continue
            candidate["evidence"] = _candidate_evidence(
                candidate,
                raw_seq=raw_seq,
                expected_anticodon=expected_anticodon,
                expected_anticodon_span=expected_anticodon_span,
                structure_pairs=structure_pairs,
                structure_available=structure_available,
                idx_ac=idx_ac,
            )
            candidates.append(candidate)
    candidates.sort(
        key=lambda item: tuple(item["evidence"]["score_key"])
        + (abs(int(item["d_left"])), abs(int(item["d_right"])))
    )
    return candidates


def maybe_refine_anticodon_boundaries(
    cmp: Dict[str, Any],
    region_order: List[str],
    runtime_values_override: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Exhaustively score the 5x5 local boundary neighborhood.

    The evidence order is fixed and lexicographic. A candidate is applied only
    when it strictly improves on the unmodified boundary under that same rule.
    No language model participates in candidate generation or acceptance.
    """
    try:
        runtime_values = load_runtime_values(runtime_values_override)
        if not runtime_bool(
            "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT",
            True,
            runtime_values=runtime_values,
        ):
            return

        region_map = cmp.get("regions") or {}
        ann_meta = dict(cmp.get("annotation_meta") or {})
        raw_seq = "".join(seq_chars(cmp.get("raw_input_seq", "")))
        if not raw_seq:
            return
        n_total = len(raw_seq)

        current_lengths: List[int] = []
        template_lengths: List[int] = []
        for region in region_order:
            block = region_map.get(region) or {}
            current_lengths.append(len(seq_chars(block.get("input_seq", ""))))
            template_lengths.append(template_non_gap_len(block.get("template_seq", "")))
        if sum(current_lengths) != n_total or sum(template_lengths) != n_total:
            return

        idx_d = region_order.index("D loop + D stem")
        idx_ac = region_order.index("Anticodon loop + Anticodon stem")
        idx_var = region_order.index("Variable loop")
        if not (idx_d + 1 == idx_ac and idx_ac + 1 == idx_var):
            return

        bounds = [0]
        for length in current_lengths:
            bounds.append(bounds[-1] + length)

        expected_anticodon = norm_rna_token(
            ann_meta.get("trnascan_anticodon", "")
        )
        anti_start = safe_int_value(
            ann_meta.get("trnascan_anticodon_start", 0),
            0,
        )
        anti_end = safe_int_value(
            ann_meta.get("trnascan_anticodon_end", 0),
            0,
        )
        anti_span = (
            (anti_start, anti_end)
            if anti_start > 0 and anti_end >= anti_start
            else None
        )

        structure = struct_chars(ann_meta.get("secondary_structure", ""))
        structure_available = (
            len(structure) == n_total and _structure_is_balanced(structure)
        )
        pairs = _structure_pairs(structure) if structure_available else []
        candidates = _enumerate_boundary_candidates(
            bounds=bounds,
            n_total=n_total,
            region_order=region_order,
            tpl_lens=template_lengths,
            idx_d=idx_d,
            idx_ac=idx_ac,
            idx_var=idx_var,
            raw_seq=raw_seq,
            expected_anticodon=expected_anticodon,
            expected_anticodon_span=anti_span,
            structure_pairs=pairs,
            structure_available=structure_available,
        )
        baseline = next(
            (
                candidate
                for candidate in candidates
                if candidate["d_left"] == 0 and candidate["d_right"] == 0
            ),
            None,
        )
        selected = candidates[0] if candidates else None
        if baseline is None or selected is None:
            return
        if tuple(selected["evidence"]["score_key"]) >= tuple(
            baseline["evidence"]["score_key"]
        ):
            return

        for index, region in enumerate(region_order):
            left = int(selected["bounds"][index])
            right = int(selected["bounds"][index + 1])
            block = region_map.get(region)
            if not isinstance(block, dict):
                continue
            block["input_seq"] = raw_seq[left:right]
            if structure_available:
                block["input_struct"] = "".join(structure[left:right])

        ann_meta["boundary_resolved"] = True
        ann_meta["boundary_resolver_mode"] = "deterministic_multi_evidence_search"
        ann_meta["boundary_llm_enabled"] = False
        ann_meta["boundary_candidate_count"] = len(candidates)
        ann_meta["boundary_scoring_policy"] = BOUNDARY_SCORING_POLICY
        ann_meta["boundary_evidence_sources"] = {
            "anticodon": "tRNAscan-SE" if anti_span or expected_anticodon else "unavailable",
            "secondary_structure": str(
                ann_meta.get("annotation_source") or "annotation_pipeline"
            )
            if structure_available
            else "unavailable",
            "template": str(cmp.get("template_name") or "unknown"),
        }
        ann_meta["boundary_evidence_before"] = dict(baseline["evidence"])
        ann_meta["boundary_evidence_after"] = dict(selected["evidence"])
        ann_meta["boundary_before_lengths"] = current_lengths
        ann_meta["boundary_after_lengths"] = list(selected["lens"])
        ann_meta["boundary_template_lengths"] = template_lengths
        ann_meta["boundary_deltas"] = {
            "d_to_ac": int(selected["d_left"]),
            "ac_to_var": int(selected["d_right"]),
        }
        # Compatibility metrics retain their historical template-error meaning.
        ann_meta["boundary_score_before"] = int(baseline["obj"])
        ann_meta["boundary_score_after"] = int(selected["obj"])
        cmp["annotation_meta"] = ann_meta

        logger.info(
            "Deterministic boundary refinement applied: D/AC/V %s/%s/%s -> "
            "%s/%s/%s, evidence %s -> %s.",
            current_lengths[idx_d],
            current_lengths[idx_ac],
            current_lengths[idx_var],
            selected["lens"][idx_d],
            selected["lens"][idx_ac],
            selected["lens"][idx_var],
            baseline["evidence"]["score_key"],
            selected["evidence"]["score_key"],
        )
    except Exception as exc:
        logger.warning("Deterministic boundary refinement skipped: %r", exc)


def maybe_fix_anticodon_boundaries_in_llm_mode(
    cmp: Dict[str, Any],
    region_order: List[str],
    runtime_values_override: Optional[Dict[str, Any]] = None,
) -> None:
    """Backward-compatible name for the deterministic boundary resolver."""
    maybe_refine_anticodon_boundaries(
        cmp,
        region_order,
        runtime_values_override=runtime_values_override,
    )


__all__ = [
    "BOUNDARY_SCORING_POLICY",
    "maybe_fix_anticodon_boundaries_in_llm_mode",
    "maybe_refine_anticodon_boundaries",
    "norm_rna_token",
    "postcheck_runtime_overrides",
    "safe_int_value",
]
