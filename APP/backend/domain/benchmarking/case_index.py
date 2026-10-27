"""Canonical fields and scoring for one benchmark case result.

Stored executions keep model predictions while scores are derived from the
currently active answer row, so curation changes do not require model calls.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from backend.domain.alignment.number_to_region import number_to_region

ANTICODON_LABELS = frozenset({"34", "35", "36"})
TERMINAL_LABELS = frozenset({"73", "74", "75", "76"})
HIGH_RISK_REGIONS = frozenset({"Variable loop", "D loop + D stem"})


def gold_fingerprint(labels, bases) -> str:
    """Stable fingerprint of one answer: labels plus bases, residue by residue.

    A stored score is only meaningful next to the answer it was graded against,
    so every case row carries this.  It stays byte-compatible with
    ``sequence_repository.residue_digest``, which writes the answer table.
    """
    payload = json.dumps(
        [[str(v) for v in labels], [str(v) for v in bases]],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


TEXT_FIELDS = (
    "benchmark_protocol",
    "benchmark_split",
    "template_overlap_view",
    "sequence_sha256",
    "template_exact_overlap",
    "agent_planner_policy",
    "broad_source",
    "cohort_source",
    "curation_status",
    "terminal_reference_category",
    "representative_seqname",
    "correction_decision",
    "gold_source",
    "gold_labels_sha256",
    "status",
    "template_name",
    "method_backend",
    "planner_sources",
    "agent_stop_reason",
    "route",
    "disposition",
    "error",
)

BOOL_FIELDS = (
    "gold_evaluation_allowed",
    "agent_llm_postcheck_enabled",
    "agent_deterministic_boundary_refinement_enabled",
    "use_llm",
    "out_of_scope",
    "in_canonical_benchmark",
    "base_preserved",
    "legality_passed",
    "exact_match",
    "anticodon_exact",
    "cca_exact",
    "discriminator_exact",
    "terminal_coordinate_exact",
    "variable_exact",
)

TEXT_FIELDS = TEXT_FIELDS + (
    "diff_regions",
    "started_at",
    "finished_at",
)

INT_FIELDS = (
    "duplicate_group_size",
    "input_len",
    "planner_decision_count",
    "planner_llm_call_count",
    "agent_action_count",
    "cm_tool_call_count",
    "guard_challenge_count",
    "guard_challenge_rejected_count",
    "guard_challenge_escaped_count",
    "label_correct",
    "label_total",
    "region_correct",
    "region_total",
    "llm_api_call_count",
    "llm_calls_with_usage",
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "llm_total_tokens",
    "llm_prompt_cache_hit_tokens",
    "llm_prompt_cache_miss_tokens",
    "llm_reasoning_tokens",
    "label_incorrect",
    "diff_count",
    "prediction_residue_count",
    "priority",
)

FLOAT_FIELDS = (
    "elapsed_sec",
    "label_accuracy",
    "region_accuracy",
    "anticodon_accuracy",
    "cca_accuracy",
    "discriminator_accuracy",
    "terminal_coordinate_accuracy",
    "variable_accuracy",
)

TEXT_FIELDNAMES: List[str] = list(TEXT_FIELDS)
INDEX_FIELDNAMES: List[str] = [
    "seqname",
    "benchmark_protocol",
    "benchmark_split",
    "template_overlap_view",
    "sequence_sha256",
    "duplicate_group_size",
    "template_exact_overlap",
    "broad_source",
    "cohort_source",
    "curation_status",
    "terminal_reference_category",
    "representative_seqname",
    "correction_decision",
    "gold_source",
    "gold_labels_sha256",
    "gold_evaluation_allowed",
    "status",
    "input_len",
    "elapsed_sec",
    "template_name",
    "method_backend",
    "agent_planner_policy",
    "agent_llm_postcheck_enabled",
    "agent_deterministic_boundary_refinement_enabled",
    "planner_decision_count",
    "planner_llm_call_count",
    "planner_sources",
    "agent_action_count",
    "cm_tool_call_count",
    "agent_stop_reason",
    "use_llm",
    "route",
    "disposition",
    "out_of_scope",
    "in_canonical_benchmark",
    "base_preserved",
    "legality_passed",
    "guard_challenge_count",
    "guard_challenge_rejected_count",
    "guard_challenge_escaped_count",
    "label_correct",
    "label_total",
    "label_accuracy",
    "exact_match",
    "region_correct",
    "region_total",
    "region_accuracy",
    "anticodon_correct",
    "anticodon_total",
    "anticodon_accuracy",
    "anticodon_exact",
    "cca_correct",
    "cca_total",
    "cca_accuracy",
    "cca_exact",
    "discriminator_correct",
    "discriminator_total",
    "discriminator_accuracy",
    "discriminator_exact",
    "terminal_coordinate_correct",
    "terminal_coordinate_total",
    "terminal_coordinate_accuracy",
    "terminal_coordinate_exact",
    "variable_correct",
    "variable_total",
    "variable_accuracy",
    "variable_exact",
    "llm_api_call_count",
    "llm_calls_with_usage",
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "llm_total_tokens",
    "llm_prompt_cache_hit_tokens",
    "llm_prompt_cache_miss_tokens",
    "llm_reasoning_tokens",
    "label_incorrect",
    "diff_count",
    "diff_regions",
    "prediction_residue_count",
    "priority",
    "started_at",
    "finished_at",
    "error",
]


def count_cm_tool_calls(events: Sequence[Dict[str, Any]]) -> int:
    direct_calls = sum(
        str(event.get("stage") or "") == "tool_started"
        and str(event.get("tool") or "") == "align_numbering_cm"
        for event in events
        if isinstance(event, dict)
    )
    if direct_calls:
        return direct_calls
    return sum(
        str(event.get("stage") or "") in {"action_executed", "action_failed"}
        and str(event.get("action_type") or "") == "align_numbering_cm"
        for event in events
        if isinstance(event, dict)
    )


def planner_events_of(trace_events: Sequence[Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for event in trace_events:
        if not isinstance(event, dict):
            continue
        if event.get("stage") == "agent_decision":
            events.append(event)
        elif event.get("stage") == "planning" and isinstance(event.get("agent_decision"), dict):
            events.append(dict(event["agent_decision"]))
    return events


def flatten_case_result(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the canonical index row for one stored case JSON."""
    metrics = dict(row.get("metrics") or {})
    agent = dict(row.get("agent") or {})
    benchmark_case = dict(row.get("benchmark_case") or {})
    benchmark_metadata = dict(benchmark_case.get("metadata") or {})
    usage = dict(row.get("llm_usage") or {})
    usage_tokens = dict(usage.get("tokens") or {})
    guard_audit = dict(row.get("guard_challenge_audit") or {})
    agent_trace = dict(row.get("agent_trace") or {})
    trace_events = list(agent_trace.get("trace_events") or [])
    action_events = [
        event
        for event in trace_events
        if isinstance(event, dict)
        and str(event.get("stage") or "") in {"action_executed", "action_failed"}
    ]
    decisions = planner_events_of(trace_events)
    details = dict(usage_tokens)
    reasoning = details.get("completion_tokens_details.reasoning_tokens")

    flat: Dict[str, Any] = {
        "seqname": row.get("seqname"),
        "benchmark_protocol": benchmark_case.get("selected_protocol"),
        "benchmark_split": (
            benchmark_case.get("benchmark_partition")
            or benchmark_case.get("retrospective_split")
            or benchmark_case.get("selected_split")
        ),
        "template_overlap_view": benchmark_case.get("selected_template_overlap_view"),
        "sequence_sha256": benchmark_case.get("sequence_sha256"),
        "duplicate_group_size": benchmark_case.get("duplicate_group_size"),
        "template_exact_overlap": benchmark_case.get("template_exact_overlap"),
        "broad_source": benchmark_metadata.get("broad_source"),
        "cohort_source": benchmark_case.get("cohort_source"),
        "curation_status": benchmark_case.get("curation_status"),
        "terminal_reference_category": benchmark_case.get("terminal_reference_category"),
        "representative_seqname": benchmark_case.get("representative_seqname"),
        "correction_decision": benchmark_case.get("correction_decision"),
        "gold_source": benchmark_case.get("gold_source"),
        "gold_labels_sha256": benchmark_case.get("gold_labels_sha256") or row.get("gold_labels_sha256"),
        "gold_evaluation_allowed": row.get(
            "gold_evaluation_allowed",
            benchmark_case.get("gold_evaluation_allowed", True),
        ),
        "status": row.get("status"),
        "input_len": row.get("input_len"),
        "elapsed_sec": row.get("elapsed_sec"),
        "template_name": row.get("template_name"),
        "method_backend": row.get("method_backend"),
        "agent_planner_policy": row.get("agent_planner_policy"),
        "agent_llm_postcheck_enabled": row.get("agent_llm_postcheck_enabled"),
        "agent_deterministic_boundary_refinement_enabled": row.get(
            "agent_deterministic_boundary_refinement_enabled"
        ),
        "planner_decision_count": len(decisions),
        "planner_llm_call_count": sum(bool(event.get("llm_called")) for event in decisions),
        "planner_sources": ",".join(
            sorted({str(event.get("planner_source") or "unknown") for event in decisions})
        ),
        "agent_action_count": len(action_events),
        "cm_tool_call_count": count_cm_tool_calls(trace_events),
        "agent_stop_reason": dict(agent_trace.get("work_state_summary") or {}).get("stop_reason"),
        "use_llm": row.get("use_llm"),
        "route": agent.get("route"),
        "disposition": agent.get("disposition"),
        "out_of_scope": agent.get("out_of_scope"),
        "in_canonical_benchmark": agent.get("in_canonical_benchmark"),
        "base_preserved": row.get("base_preserved"),
        "legality_passed": metrics.get("legality_passed"),
        "guard_challenge_count": guard_audit.get("challenge_count"),
        "guard_challenge_rejected_count": guard_audit.get("rejected_count"),
        "guard_challenge_escaped_count": guard_audit.get("escaped_count"),
        "label_correct": metrics.get("label_correct"),
        "label_total": metrics.get("label_total"),
        "label_accuracy": metrics.get("label_accuracy"),
        "exact_match": metrics.get("exact_match"),
        "region_correct": metrics.get("region_correct"),
        "region_total": metrics.get("region_total"),
        "region_accuracy": metrics.get("region_accuracy"),
        "anticodon_correct": metrics.get("anticodon_correct"),
        "anticodon_total": metrics.get("anticodon_total"),
        "anticodon_accuracy": metrics.get("anticodon_accuracy"),
        "anticodon_exact": metrics.get("anticodon_exact"),
        "cca_correct": metrics.get("cca_correct"),
        "cca_total": metrics.get("cca_total"),
        "cca_accuracy": metrics.get("cca_accuracy"),
        "cca_exact": metrics.get("cca_exact"),
        "discriminator_correct": metrics.get("discriminator_correct"),
        "discriminator_total": metrics.get("discriminator_total"),
        "discriminator_accuracy": metrics.get("discriminator_accuracy"),
        "discriminator_exact": metrics.get("discriminator_exact"),
        "terminal_coordinate_correct": metrics.get("terminal_coordinate_correct"),
        "terminal_coordinate_total": metrics.get("terminal_coordinate_total"),
        "terminal_coordinate_accuracy": metrics.get("terminal_coordinate_accuracy"),
        "terminal_coordinate_exact": metrics.get("terminal_coordinate_exact"),
        "variable_correct": metrics.get("variable_correct"),
        "variable_total": metrics.get("variable_total"),
        "variable_accuracy": metrics.get("variable_accuracy"),
        "variable_exact": metrics.get("variable_exact"),
        "llm_api_call_count": usage.get("call_count"),
        "llm_calls_with_usage": usage.get("calls_with_usage"),
        "llm_prompt_tokens": usage_tokens.get("prompt_tokens"),
        "llm_completion_tokens": usage_tokens.get("completion_tokens"),
        "llm_total_tokens": usage_tokens.get("total_tokens"),
        "llm_prompt_cache_hit_tokens": usage_tokens.get("prompt_tokens_details.cached_tokens"),
        "llm_prompt_cache_miss_tokens": usage_tokens.get("prompt_tokens_details.created_cache_tokens"),
        "llm_reasoning_tokens": reasoning,
        "diff_count": row.get("diff_count"),
        "diff_regions": ",".join(str(v) for v in (row.get("diff_regions") or [])),
        "prediction_residue_count": row.get("prediction_residue_count"),
        "priority": row.get("priority"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "error": row.get("error", ""),
    }
    correct, total = flat.get("label_correct"), flat.get("label_total")
    if isinstance(correct, int) and isinstance(total, int):
        flat["label_incorrect"] = max(0, total - correct)
    return flat


def technical_issue_count(flat: Mapping[str, Any]) -> int:
    """Rows that must be retried: transport errors, not scientific rejections."""
    error = str(flat.get("error") or "").strip()
    if not error:
        return 0
    if str(flat.get("status") or "") == "ok":
        return 0
    return 1


# ---------------------------------------------------------------- A/B compare
# The gold-standard review page and the benchmark index must count differences
# the same way, so the comparison lives here instead of in either caller.


def safe_base(raw: Any) -> str:
    base = str(raw or "").strip().upper().replace("T", "U")
    return base if base in {"A", "C", "G", "U", "N"} else "-"


def safe_region(label: Any) -> str:
    try:
        return number_to_region(str(label or "").strip())
    except Exception:
        return ""


def parse_alignment_csv(csv_content: Any) -> tuple[list[str], list[str], list[str]]:
    try:
        rows = list(csv.reader(io.StringIO(str(csv_content or ""))))
    except Exception:
        return [], [], []
    if len(rows) < 2:
        return [], [], []
    return list(rows[0]), (list(rows[1]) if len(rows) > 2 else []), list(rows[-1])


def pred_residue_map(csv_content: Any) -> tuple[list[str], list[str]]:
    """Labels and bases of the model candidate, restricted to real residues."""
    labels_row, _template_row, target_row = parse_alignment_csv(csv_content)
    labels: list[str] = []
    bases: list[str] = []
    for label, raw_base in zip(labels_row, target_row):
        if safe_base(raw_base) == "-":
            continue
        labels.append(str(label or "").strip())
        bases.append(safe_base(raw_base))
    return labels, bases


def score_labels(
    gold_labels: Sequence[Any],
    predicted_labels: Sequence[Any],
    *,
    legality_passed: bool,
) -> Dict[str, Any]:
    """Score a frozen prediction against the active answer supplied by caller.

    This is deliberately independent of any run snapshot.  A curation decision
    can therefore change the score of every stored execution without repeating
    the model call.
    """
    gold = [str(value) for value in gold_labels]
    predicted = [str(value) for value in predicted_labels]

    def subset(labels: Sequence[str], predicate) -> tuple[int, int, bool | None]:
        indexes = [index for index, label in enumerate(labels) if predicate(label)]
        total = len(indexes)
        if not total:
            return 0, 0, None
        correct = sum(index < len(predicted) and predicted[index] == labels[index] for index in indexes)
        return total, correct, correct == total

    total = len(gold)
    correct = sum(index < len(predicted) and predicted[index] == label for index, label in enumerate(gold))
    region_correct = sum(
        index < len(predicted) and safe_region(predicted[index]) == safe_region(label)
        for index, label in enumerate(gold)
    )
    groups = {
        "anticodon": lambda label: label in {"34", "35", "36"},
        "cca": lambda label: label in {"73", "74", "75", "76"},
        "discriminator": lambda label: label == "73",
        "terminal_coordinate": lambda label: label in {"74", "75", "76"},
        "variable": lambda label: safe_region(label) == "Variable loop",
    }
    metrics: Dict[str, Any] = {
        "label_correct": correct,
        "label_total": total,
        "label_accuracy": correct / total if total else None,
        "exact_match": predicted == gold,
        "region_correct": region_correct,
        "region_total": total,
        "region_accuracy": region_correct / total if total else None,
        "legality_passed": bool(legality_passed),
    }
    for name, predicate in groups.items():
        group_total, group_correct, group_exact = subset(gold, predicate)
        metrics[f"{name}_correct"] = group_correct
        metrics[f"{name}_total"] = group_total
        metrics[f"{name}_accuracy"] = group_correct / group_total if group_total else None
        metrics[f"{name}_exact"] = group_exact
    return metrics


def unscored_metrics(*, legality_passed: bool) -> Dict[str, Any]:
    """Metric shape for a sequence excluded from the active benchmark score."""
    metrics: Dict[str, Any] = {
        "label_correct": None,
        "label_total": 0,
        "label_accuracy": None,
        "exact_match": None,
        "region_correct": None,
        "region_total": 0,
        "region_accuracy": None,
        "legality_passed": bool(legality_passed),
    }
    for name in ("anticodon", "cca", "discriminator", "terminal_coordinate", "variable"):
        metrics[f"{name}_correct"] = None
        metrics[f"{name}_total"] = 0
        metrics[f"{name}_accuracy"] = None
        metrics[f"{name}_exact"] = None
    return metrics


def build_diffs(
    gold_labels: Sequence[Any],
    gold_bases: Sequence[Any],
    pred_labels: Sequence[Any],
    pred_bases: Sequence[Any],
) -> List[Dict[str, Any]]:
    """Residue-by-residue differences between one answer and one prediction."""
    total = max(len(gold_labels), len(pred_labels))
    diffs: List[Dict[str, Any]] = []
    for idx in range(total):
        gold_label = str(gold_labels[idx]).strip() if idx < len(gold_labels) else ""
        pred_label = str(pred_labels[idx]).strip() if idx < len(pred_labels) else ""
        gold_base = safe_base(gold_bases[idx]) if idx < len(gold_bases) else ""
        pred_base = safe_base(pred_bases[idx]) if idx < len(pred_bases) else ""
        if gold_label == pred_label and gold_base == pred_base:
            continue
        diffs.append(
            {
                "residue_index": idx + 1,
                "gold_base": gold_base,
                "pred_base": pred_base,
                "gold_label": gold_label,
                "pred_label": pred_label,
                "gold_region": safe_region(gold_label),
                "pred_region": safe_region(pred_label),
                "region": safe_region(gold_label or pred_label),
            }
        )
    return diffs


def priority_for_diffs(diffs: Sequence[Mapping[str, Any]], *, legality_passed: Any = None) -> int:
    score = min(50, len(diffs))
    labels = {str(d.get("gold_label") or "") for d in diffs} | {
        str(d.get("pred_label") or "") for d in diffs
    }
    regions = {str(d.get("gold_region") or "") for d in diffs} | {
        str(d.get("pred_region") or "") for d in diffs
    }
    if labels & ANTICODON_LABELS:
        score += 35
    if labels & TERMINAL_LABELS:
        score += 30
    if regions & HIGH_RISK_REGIONS:
        score += 20
    if legality_passed is True:
        score += 10
    return score


def attach_case_comparison(
    result: Dict[str, Any],
    *,
    gold_labels: Sequence[Any] = (),
    gold_bases: Sequence[Any] = (),
) -> Dict[str, Any]:
    """Store the candidate-vs-gold difference summary inside the case record.

    ``gold_labels``/``gold_bases`` come from the benchmark input, which is the
    same reference the review page renders as A, so a stored ``diff_count`` is a
    measured value rather than an estimate derived from the accuracy columns.
    """
    pred_labels, pred_bases = pred_residue_map(result.get("csv_content"))
    diffs = (
        build_diffs(gold_labels, gold_bases, pred_labels, pred_bases)
        if gold_labels or gold_bases
        else []
    )
    metrics = dict(result.get("metrics") or {})
    result["diff_count"] = len(diffs)
    result["diff_regions"] = sorted({str(d.get("region") or "") for d in diffs if d.get("region")})
    result["priority"] = priority_for_diffs(diffs, legality_passed=metrics.get("legality_passed"))
    result["prediction_residue_count"] = len(pred_labels)
    if not result.get("sequence_sha256") and result.get("input_seq"):
        normalized = "".join(str(result["input_seq"]).split()).upper().replace("T", "U")
        result["sequence_sha256"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return result


def case_results_tsv_text(rows: Sequence[Mapping[str, Any]]) -> str:
    """Render already-flat index rows as ``case_results.tsv``.

    A run that finishes writes this file; a run rebuilt from MySQL writes the
    same columns from re-graded rows, so a script reading the TSV cannot tell
    whether the numbers arrived at seal time or from the current answer table.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(INDEX_FIELDNAMES), delimiter="\t")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name) for name in INDEX_FIELDNAMES})
    return buffer.getvalue()


def case_traces_tsv_text(rows: Sequence[Mapping[str, Any]]) -> str:
    """Same index built from case trace records, flattening each one first."""
    return case_results_tsv_text([flatten_case_result(row) for row in rows])


# ---------------------------------------------------------------- summary
# Aggregate flat index rows into the run summary used by reports, figures and
# the ablation ladder.  Every score here comes from rows that were re-graded
# against the active answer table, so a stored run and a freshly graded one go
# through the same arithmetic and a curation decision moves both together.

SUMMARY_ACCURACY_FIELDS = (
    "region",
    "anticodon",
    "cca",
    "discriminator",
    "terminal_coordinate",
    "variable",
)

# Derived fields a stored trace carries.  They are overlaid, never rewritten in
# place, so the model's own record stays byte-identical in object storage while
# the numbers attached to it follow the current answer.
ACTIVE_TRACE_METRIC_KEYS = (
    "label_correct",
    "label_total",
    "label_accuracy",
    "exact_match",
    "region_correct",
    "region_total",
    "region_accuracy",
    "legality_passed",
    "anticodon_correct",
    "anticodon_total",
    "anticodon_accuracy",
    "anticodon_exact",
    "cca_correct",
    "cca_total",
    "cca_accuracy",
    "cca_exact",
    "discriminator_correct",
    "discriminator_total",
    "discriminator_accuracy",
    "discriminator_exact",
    "terminal_coordinate_correct",
    "terminal_coordinate_total",
    "terminal_coordinate_accuracy",
    "terminal_coordinate_exact",
    "variable_correct",
    "variable_total",
    "variable_accuracy",
    "variable_exact",
)


def apply_active_answer_to_trace(
    trace: Mapping[str, Any],
    scored_row: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return ``trace`` with the scores, diffs and answer provenance of ``scored_row``."""
    payload = dict(trace)
    metrics = dict(payload.get("metrics") or {})
    for key in ACTIVE_TRACE_METRIC_KEYS:
        if key in scored_row:
            metrics[key] = scored_row.get(key)
    payload["metrics"] = metrics
    payload["gold_evaluation_allowed"] = scored_row.get(
        "gold_evaluation_allowed", payload.get("gold_evaluation_allowed", True)
    )
    payload["gold_residue_count"] = metrics.get("label_total")
    payload["diff_count"] = scored_row.get("diff_count")
    payload["diff_regions"] = [
        value for value in str(scored_row.get("diff_regions") or "").split(",") if value
    ]
    payload["priority"] = scored_row.get("priority")
    # The answer the numbers were computed from travels with the frozen
    # prediction, so a rebuilt run directory can grade per residue without the
    # reference file the run happened to start from.
    if scored_row.get("active_gold_labels") is not None:
        payload["gold_labels"] = list(scored_row.get("active_gold_labels") or [])
        payload["gold_bases"] = list(scored_row.get("active_gold_bases") or [])
        payload["gold_answer_sha256"] = scored_row.get("gold_labels_sha256")
    meta = dict(payload.get("benchmark_case") or {})
    for key in ("gold_source", "gold_labels_sha256", "correction_decision"):
        if key in scored_row:
            meta[key] = scored_row.get(key)
    meta["gold_evaluation_allowed"] = payload["gold_evaluation_allowed"]
    meta["gold_table"] = "mysql://trna_gold"
    payload["benchmark_case"] = meta
    return payload


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _mean(values: Sequence[Any]) -> Optional[float]:
    clean = [float(value) for value in values if value is not None]
    return (sum(clean) / len(clean)) if clean else None


def summarize_case_rows(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Summarize flat case index rows with the definitions used at seal time.

    Every ratio is pooled over residues rather than averaged over sequences: a
    run that scored 90/100 on one long sequence and 1/1 on a short one reports
    91/101, not 90.5%.  Keeping this function equal to the runner's own summary
    is what lets a re-grade be compared with the number the run was sealed with.
    """
    ok_rows = [row for row in rows if str(row.get("status") or "") == "ok"]
    error_rows = [row for row in rows if str(row.get("status") or "") != "ok"]
    scored_rows = [row for row in rows if bool(row.get("gold_evaluation_allowed"))]
    scored_ok_rows = [row for row in ok_rows if bool(row.get("gold_evaluation_allowed"))]
    accepted_rows = [row for row in ok_rows if str(row.get("disposition") or "") == "accepted"]
    accepted_scored = [row for row in accepted_rows if bool(row.get("gold_evaluation_allowed"))]
    review_rows = [row for row in ok_rows if str(row.get("disposition") or "") != "accepted"]
    review_scored = [row for row in review_rows if bool(row.get("gold_evaluation_allowed"))]
    dispositions = Counter(
        str(row.get("disposition") or "reported") for row in ok_rows
    )

    def residue_ratio(selected: Sequence[Mapping[str, Any]]) -> Optional[float]:
        total = sum(_int(row.get("label_total")) for row in selected)
        if not total:
            return None
        return sum(_int(row.get("label_correct")) for row in selected) / total

    def pooled(name: str, selected: Sequence[Mapping[str, Any]]) -> Optional[float]:
        total = sum(_int(row.get(f"{name}_total")) for row in selected)
        if not total:
            return None
        return sum(_int(row.get(f"{name}_correct")) for row in selected) / total

    def exact_rate(name: str, selected: Sequence[Mapping[str, Any]]) -> Optional[float]:
        values = [
            1 if row.get(name) else 0
            for row in selected
            if row.get(name) is not None
        ]
        return _mean(values)

    elapsed = [float(row.get("elapsed_sec") or 0.0) for row in ok_rows]
    token_keys = (
        "llm_prompt_tokens",
        "llm_completion_tokens",
        "llm_total_tokens",
        "llm_prompt_cache_hit_tokens",
        "llm_prompt_cache_miss_tokens",
        "llm_reasoning_tokens",
    )
    metrics: Dict[str, Any] = {
        "residue_label_accuracy": residue_ratio(scored_ok_rows),
        "exact_match_rate": exact_rate("exact_match", scored_ok_rows),
        "region_accuracy": pooled("region", scored_ok_rows),
        "legality_pass_rate": exact_rate("legality_passed", ok_rows),
        "base_preservation_rate": exact_rate("base_preserved", ok_rows),
        "out_of_scope_rate": exact_rate("out_of_scope", ok_rows),
    }
    for name in SUMMARY_ACCURACY_FIELDS:
        metrics[f"{name}_accuracy"] = pooled(name, scored_ok_rows)
        if name != "region":
            metrics[f"{name}_exact_rate"] = exact_rate(f"{name}_exact", scored_ok_rows)

    return {
        "counts": {
            "total_cases": len(rows),
            "ok_cases": len(ok_rows),
            "error_cases": len(error_rows),
            "accepted_cases": len(accepted_rows),
            "gold_scored_cases": len(scored_rows),
            "gold_scored_ok_cases": len(scored_ok_rows),
            "gold_unscored_cases": len(rows) - len(scored_rows),
            "accepted_gold_scored_cases": len(accepted_scored),
            "dispositions": dict(sorted(dispositions.items())),
        },
        "metrics": metrics,
        "selective_metrics": {
            "accepted_coverage": (len(accepted_rows) / len(rows)) if rows else None,
            "accepted_residue_label_accuracy": residue_ratio(accepted_scored),
            "accepted_exact_match_rate": exact_rate("exact_match", accepted_scored),
            "accepted_region_accuracy": pooled("region", accepted_scored),
            "accepted_variable_accuracy": pooled("variable", accepted_scored),
            "investigate_rate": (len(review_rows) / len(rows)) if rows else None,
            "abstained_rate": (dispositions.get("abstained", 0) / len(rows)) if rows else None,
            "error_rate": (len(error_rows) / len(rows)) if rows else None,
        },
        "flow_groups": {
            "accepted_scored": len(accepted_scored),
            "accepted_exact": sum(1 for row in accepted_scored if row.get("exact_match")),
            "review_scored": len(review_scored),
            "review_exact": sum(1 for row in review_scored if row.get("exact_match")),
        },
        "agent_activity": {
            "planner_llm_call_count": sum(_int(row.get("planner_llm_call_count")) for row in rows),
            "planner_decision_count": sum(_int(row.get("planner_decision_count")) for row in rows),
            "agent_action_count": sum(_int(row.get("agent_action_count")) for row in rows),
            "cm_tool_call_count": sum(_int(row.get("cm_tool_call_count")) for row in rows),
            "technical_issue_cases": sum(1 for row in rows if _int(row.get("technical_issue_count")) > 0),
        },
        "llm_usage": {
            "call_count": sum(_int(row.get("llm_api_call_count")) for row in rows),
            "calls_with_usage": sum(_int(row.get("llm_calls_with_usage")) for row in rows),
            "tokens": {
                key: sum(_int(row.get(key)) for row in rows)
                for key in token_keys
            },
        },
        "timing": {
            "elapsed_total_sec": sum(elapsed),
            "elapsed_mean_sec": (sum(elapsed) / len(elapsed)) if elapsed else None,
            "elapsed_median_sec": (sorted(elapsed)[len(elapsed) // 2] if elapsed else None),
        },
        "guard_challenge_audit": {
            "challenge_count": sum(_int(row.get("guard_challenge_count")) for row in rows),
            "rejected_count": sum(_int(row.get("guard_challenge_rejected_count")) for row in rows),
            "escaped_count": sum(_int(row.get("guard_challenge_escaped_count")) for row in rows),
            "rejection_rate": (
                sum(_int(row.get("guard_challenge_rejected_count")) for row in rows)
                / sum(_int(row.get("guard_challenge_count")) for row in rows)
                if sum(_int(row.get("guard_challenge_count")) for row in rows)
                else None
            ),
        },
    }
