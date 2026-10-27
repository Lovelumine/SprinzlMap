#!/usr/bin/env python3
"""Evaluate answer-free variable-boundary reconciliation on a frozen run."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
for relative in ("APP", "."):
    path = str(PROJECT_ROOT / relative)
    if path not in sys.path:
        sys.path.insert(0, path)

from backend.agent.contracts import (  # noqa: E402
    AlignmentRequest,
    CandidateAction,
    RootSnapshot,
)
from backend.agent.orchestrator.service import AgentAlignmentService  # noqa: E402
from backend.agent.evaluation.variable_boundary_reconciliation import (  # noqa: E402
    assess_variable_boundary_ambiguity,
    reconcile_variable_boundary,
)
from backend.domain.alignment.variable_boundary import (  # noqa: E402
    assess_variable_bridge,
)
from backend.domain.common.trnascan_runtime import (  # noqa: E402
    run_trnascan_directly,
)


VALID_BASES = frozenset({"A", "C", "G", "U", "N"})
CLASSIC_SOURCES = frozenset({"archaea", "bacteria", "cytosolic_eukaryote"})


@dataclass(frozen=True)
class NumberedSequence:
    labels: tuple[str, ...]
    bases: tuple[str, ...]

    @property
    def sequence(self) -> str:
        return "".join(self.bases)


@dataclass(frozen=True)
class EvaluationCase:
    seqname: str
    template_name: str
    broad_source: str
    amino_acid: str
    organism: str
    current: NumberedSequence
    gold: NumberedSequence
    evidence: Mapping[str, Any]
    scientific_provenance: Mapping[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--gold",
        type=Path,
        default=PROJECT_ROOT / ".runtime" / "gold" / "mature_benchmark.curated.tsv",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-tsv", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--policy",
        choices=("structure-only", "multi-evidence"),
        default="structure-only",
    )
    parser.add_argument("--reference-top-k", type=int, default=5)
    parser.add_argument("--minimum-reference-support", type=int, default=2)
    return parser.parse_args()


def normalize_base(raw: Any) -> str:
    value = str(raw or "").strip().upper().replace("T", "U")
    return value if value in VALID_BASES else "-"


def occupied(labels: Sequence[Any], bases: Sequence[Any]) -> NumberedSequence:
    occupied_labels: list[str] = []
    occupied_bases: list[str] = []
    for label, raw_base in zip(labels, bases):
        base = normalize_base(raw_base)
        if base == "-":
            continue
        occupied_labels.append(str(label or "").strip())
        occupied_bases.append(base)
    return NumberedSequence(tuple(occupied_labels), tuple(occupied_bases))


def parse_alignment(raw: Any) -> NumberedSequence:
    rows = list(csv.reader(io.StringIO(str(raw or ""))))
    if len(rows) < 3:
        return NumberedSequence((), ())
    return occupied(rows[0], rows[2])


def load_gold(path: Path) -> dict[str, NumberedSequence]:
    result: dict[str, NumberedSequence] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if not header or header[0] != "seqname":
            raise ValueError(f"Unexpected gold header: {path}")
        labels = header[1:]
        for row in reader:
            if row:
                result[str(row[0])] = occupied(labels, row[1:])
    return result


def accepted_disposition(payload: Mapping[str, Any]) -> bool:
    return str(dict(payload.get("agent") or {}).get("disposition") or "") == "accepted"


def load_cases(run: Path, gold: Mapping[str, NumberedSequence]) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for path in sorted((run / "cases").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        seqname = str(payload.get("seqname") or "")
        provenance = dict(payload.get("scientific_provenance") or {})
        benchmark = dict(payload.get("benchmark_case") or {})
        metadata = dict(benchmark.get("metadata") or {})
        broad_source = str(metadata.get("broad_source") or "").lower()
        amino_acid = str(metadata.get("amino_acid") or "")
        if (
            not accepted_disposition(payload)
            or str(provenance.get("numbering_backend") or "") != "cmalign"
            or broad_source not in CLASSIC_SOURCES
            or amino_acid.lower() in {"sec", "selenocysteine"}
            or seqname not in gold
        ):
            continue
        current = parse_alignment(payload.get("csv_content"))
        snapshot = RootSnapshot(
            template_name=str(payload.get("template_name") or ""),
            csv_content="",
            numbering_row=list(current.labels),
            sequence_row=list(current.bases),
            raw_meta={
                "numbering_backend": "cmalign",
                "cm_numbering": dict(provenance.get("cm_numbering") or {}),
                "position_provenance": list(
                    provenance.get("position_provenance") or []
                ),
            },
        )
        evidence = assess_variable_boundary_ambiguity(snapshot)
        if not evidence:
            continue
        cases.append(
            EvaluationCase(
                seqname=seqname,
                template_name=str(payload.get("template_name") or ""),
                broad_source=broad_source,
                amino_acid=amino_acid,
                organism=str(metadata.get("organism") or ""),
                current=current,
                gold=gold[seqname],
                evidence=evidence,
                scientific_provenance=provenance,
            )
        )
    return cases


def label_at_reference_position(
    labels: Sequence[str],
    gold_labels: Sequence[str],
    position: str,
) -> str:
    try:
        index = list(gold_labels).index(position)
    except ValueError:
        return ""
    return str(labels[index]) if index < len(labels) else "(missing)"


def variable_posterior_codes(case: EvaluationCase) -> str:
    residues = list(
        dict(case.scientific_provenance.get("cm_numbering") or {}).get(
            "residues"
        )
        or []
    )
    values: list[str] = []
    variable_indices = {
        index
        for index, label in enumerate(case.current.labels, start=1)
        if label.startswith("V") or label.split("i", 1)[0] in {"44", "45", "46", "47", "48"}
    }
    for item in residues:
        try:
            residue_index = int((item or {}).get("residue_index") or 0)
        except (TypeError, ValueError):
            continue
        if residue_index in variable_indices:
            values.append(str((item or {}).get("posterior_code") or ""))
    return "".join(values)


def _run_multi_evidence_policy(case: EvaluationCase) -> tuple[
    tuple[str, ...],
    dict[str, Any],
    str,
]:
    parent = RootSnapshot(
        template_name=case.template_name,
        csv_content="",
        numbering_row=list(case.current.labels),
        template_row=["-"] * len(case.current.labels),
        sequence_row=list(case.current.bases),
        raw_meta={
            **dict(case.scientific_provenance or {}),
            "numbering_backend": "cmalign",
        },
    )
    request = AlignmentRequest(
        target_seq=case.current.sequence,
        output_csv_path="/dev/null",
        use_llm=False,
        input_type="mature",
        domain_hint=case.broad_source,
        cca_status="unknown",
        intron_status="unknown",
        trna_class="canonical",
        return_meta=True,
        runtime_values_override={
            "AGENT_VARIABLE_BOUNDARY_REFERENCE_TOP_K": _REFERENCE_TOP_K,
            "AGENT_VARIABLE_BOUNDARY_MIN_REFERENCE_SUPPORT": (
                _MINIMUM_REFERENCE_SUPPORT
            ),
        },
    )
    action = CandidateAction(
        action_id="development-variable-boundary-multi-evidence",
        action_type="reconcile_variable_boundary_with_structure",
        specialist="variable_boundary_specialist",
        scope="region",
        target_region="Variable loop",
        params={},
        reason="answer_free_variable_boundary_multi_evidence_diagnostic",
        allowed_regions=["Variable loop"],
        success_condition="structure_and_distinct_reference_consensus",
        rollback_condition="retain_parent_when_consensus_is_not_established",
    )
    candidate = AgentAlignmentService()._execute_variable_boundary_multi_evidence_search(
        request=request,
        parent_snapshot=parent,
        action=action,
        branch_id="development-variable-boundary-multi-evidence",
        work_state=None,
    )
    audit = dict(
        dict(candidate.raw_meta or {}).get("variable_boundary_reconciliation")
        or {}
    )
    result = dict(audit.get("structure_proposal") or audit.get("result") or {})
    labels, _bases = _occupied_snapshot(candidate)
    return tuple(labels), audit, str(result.get("mode") or "")


def _occupied_snapshot(snapshot: RootSnapshot) -> tuple[list[str], list[str]]:
    labels: list[str] = []
    bases: list[str] = []
    for label, raw_base in zip(snapshot.numbering_row, snapshot.sequence_row):
        base = normalize_base(raw_base)
        if base == "-":
            continue
        labels.append(str(label or "").strip())
        bases.append(base)
    return labels, bases


_REFERENCE_TOP_K = 5
_MINIMUM_REFERENCE_SUPPORT = 2


def evaluate_case(
    case: EvaluationCase,
    *,
    policy: str = "structure-only",
) -> dict[str, Any]:
    try:
        if policy == "multi-evidence":
            candidate_labels, audit, resolution_mode = _run_multi_evidence_policy(
                case
            )
            projection = dict(
                audit.get("structure_proposal") or audit.get("result") or {}
            )
            final_result = dict(audit.get("result") or {})
            result = dict(projection)
            result["resolved"] = str(audit.get("status") or "") == "resolved"
            result["mode"] = resolution_mode
            if not result["resolved"] and final_result.get("reason"):
                result["reason"] = str(final_result["reason"])
            consensus = dict(audit.get("reference_consensus") or {})
            observation = dict(audit.get("tool_observation") or {})
            scan_type = str(observation.get("isotype") or "")
            scan_anticodon = str(observation.get("anticodon") or "")
        else:
            scan = run_trnascan_directly(
                case.current.sequence,
                seq_id="variable_boundary_check",
            )
            result = reconcile_variable_boundary(
                target_sequence=case.current.sequence,
                current_labels=case.current.labels,
                current_bases=case.current.bases,
                struct_file_sequence=scan.struct_file_sequence,
                secondary_structure=scan.secondary_structure,
            )
            candidate_labels = tuple(
                result.get("candidate_labels") or case.current.labels
            )
            consensus = {}
            scan_type = str(scan.type or "")
            scan_anticodon = str(scan.anticodon or "")
        tool_error = ""
    except Exception as exc:
        result = {"resolved": False, "reason": "variable_boundary_tool_failed"}
        tool_error = type(exc).__name__
        scan_type = ""
        scan_anticodon = ""

        candidate_labels = tuple(case.current.labels)
        consensus = {}
    gold_labels = case.gold.labels
    before_correct = sum(
        left == right for left, right in zip(case.current.labels, gold_labels)
    )
    after_correct = sum(
        left == right for left, right in zip(candidate_labels, gold_labels)
    )
    reference_bridge = assess_variable_bridge(
        case.gold.labels,
        case.gold.bases,
        require_t_stem_anchor=True,
    )
    current_bridge = assess_variable_bridge(
        case.current.labels,
        case.current.bases,
        require_t_stem_anchor=True,
    )
    candidate_bridge = assess_variable_bridge(
        candidate_labels,
        case.current.bases,
        require_t_stem_anchor=True,
    )
    cm_score = dict(case.scientific_provenance.get("cm_score") or {})
    return {
        "seqname": case.seqname,
        "broad_source": case.broad_source,
        "amino_acid": case.amino_acid,
        "organism": case.organism,
        "trigger_reasons": ";".join(case.evidence.get("trigger_reasons") or []),
        "current_bridge_status": str(current_bridge.get("status") or ""),
        "current_bridge_labels": ",".join(current_bridge.get("observed_labels") or []),
        "reference_bridge_status": str(reference_bridge.get("status") or ""),
        "reference_bridge_labels": ",".join(reference_bridge.get("observed_labels") or []),
        "resolved": bool(result.get("resolved")),
        "resolution_reason": str(result.get("reason") or ""),
        "resolution_mode": str(result.get("mode") or ""),
        "candidate_bridge_status": str(candidate_bridge.get("status") or ""),
        "candidate_bridge_labels": ",".join(candidate_bridge.get("observed_labels") or []),
        "pair_count": int(result.get("pair_count") or 0),
        "canonical_pair_count": int(result.get("canonical_pair_count") or 0),
        "changed_residue_count": len(result.get("changed_residue_indices") or []),
        "correct_before": before_correct,
        "correct_after": after_correct,
        "correct_delta": after_correct - before_correct,
        "exact_before": bool(tuple(case.current.labels) == tuple(gold_labels)),
        "exact_after": bool(candidate_labels == tuple(gold_labels)),
        "position_47_before": label_at_reference_position(
            case.current.labels, gold_labels, "47"
        ),
        "position_47_after": label_at_reference_position(
            candidate_labels, gold_labels, "47"
        ),
        "position_47_correct_before": bool(
            label_at_reference_position(case.current.labels, gold_labels, "47")
            == "47"
        ),
        "position_47_correct_after": bool(
            label_at_reference_position(candidate_labels, gold_labels, "47")
            == "47"
        ),
        "cm_average_posterior": cm_score.get("average_posterior"),
        "variable_posterior_codes": variable_posterior_codes(case),
        "trnascan_isotype": scan_type,
        "trnascan_anticodon": scan_anticodon,
        "trnascan_tool_error": tool_error,
        "evaluation_policy": policy,
        "reference_consensus_established": bool(consensus.get("established")),
        "reference_consensus_support": int(
            consensus.get("distinct_sequence_support") or 0
        ),
        "reference_candidate_count": int(consensus.get("candidate_count") or 0),
        "answer_free_action": True,
        "gold_used_by_action": False,
    }


def counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    status = Counter()
    reason = Counter()
    by_mode: dict[str, Counter[str]] = defaultdict(Counter)
    by_reference: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        reason[str(row.get("resolution_reason") or "unknown")] += 1
        if not bool(row.get("resolved")):
            status["unresolved"] += 1
            continue
        status["resolved"] += 1
        delta = int(row.get("correct_delta") or 0)
        outcome = "improved" if delta > 0 else "same" if delta == 0 else "worse"
        status[outcome] += 1
        status["net_correct_residue_delta"] += delta
        mode = str(row.get("resolution_mode") or "unknown")
        reference_status = str(row.get("reference_bridge_status") or "unknown")
        for bucket in (by_mode[mode], by_reference[reference_status]):
            bucket["resolved"] += 1
            bucket[outcome] += 1
            bucket["net_correct_residue_delta"] += delta
    return {
        "schema_version": 1,
        "analysis_role": "development_diagnostic_not_final_blind_test",
        "action_contract": {
            "answer_free": True,
            "gold_coordinates_used_by_action": False,
            "gold_coordinates_used_for_post_hoc_scoring_only": True,
        },
        "case_count": len(rows),
        "status": counter_dict(status),
        "reason_counts": counter_dict(reason),
        "by_resolution_mode": {
            key: counter_dict(value) for key, value in sorted(by_mode.items())
        },
        "by_reference_bridge_status": {
            key: counter_dict(value)
            for key, value in sorted(by_reference.items())
        },
    }


def write_outputs(
    *,
    rows: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    output_json: Path,
    output_tsv: Path,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not rows:
        raise ValueError("No eligible variable-boundary cases were found")
    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    gold = load_gold(args.gold)
    cases = load_cases(args.run, gold)
    global _REFERENCE_TOP_K, _MINIMUM_REFERENCE_SUPPORT
    _REFERENCE_TOP_K = max(2, min(int(args.reference_top_k), 10))
    _MINIMUM_REFERENCE_SUPPORT = max(
        2,
        min(int(args.minimum_reference_support), _REFERENCE_TOP_K),
    )
    rows: list[dict[str, Any]] = []
    workers = max(1, min(int(args.workers), 32))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(evaluate_case, case, policy=args.policy)
            for case in cases
        ]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: str(row.get("seqname") or ""))
    summary = summarize(rows)
    summary["inputs"] = {
        "run": str(args.run.resolve()),
        "gold": str(args.gold.resolve()),
        "workers": workers,
        "policy": args.policy,
        "reference_top_k": _REFERENCE_TOP_K,
        "minimum_reference_support": _MINIMUM_REFERENCE_SUPPORT,
    }
    write_outputs(
        rows=rows,
        summary=summary,
        output_json=args.output_json,
        output_tsv=args.output_tsv,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
