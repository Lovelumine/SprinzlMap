from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.agent.memory.retrieval import PatternRetriever
from backend.agent.memory.store import MemoryIntegrityError, PatternStore


def _record_verified_pattern(path):
    store = PatternStore(str(path), mode="development_write", require_frozen=False)
    retriever = PatternRetriever(store)
    entry = retriever.record_verified_pattern(
        trigger_signature={
            "length_bin": "70-79",
            "difficulty_tier": "hard",
            "route": "canonical",
            "numbering_backend": "cmalign",
            "domain_group": "bacteria",
            "input_type": "mature",
            "intron_status": "absent",
            "cm_evidence_present": True,
            "warning_types": ["boundary"],
            "failure_regions": ["Variable loop"],
            "validation_reason_codes": ["variable_topology_pair_violation"],
        },
        regions=["Variable loop"],
        recommended_actions=[
            {
                "action_type": "refine_boundary",
                "params": {"focus_regions": ["Variable loop"]},
                "allowed_regions": ["Variable loop"],
            }
        ],
        score_gain=1.25,
        dataset_id="development-v2",
        verification_source="gold_and_harness",
    )
    return store, entry


def test_memory_snapshot_is_frozen_and_read_only(tmp_path):
    path = tmp_path / "patterns.jsonl"
    store, entry = _record_verified_pattern(path)

    manifest = store.freeze(dataset_id="development-v2")
    reader = PatternStore(str(path), mode="read_only", require_frozen=True)
    loaded = reader.load()

    assert manifest["source_split"] == "development"
    assert manifest["contains_raw_sequences"] is False
    assert loaded[0].pattern_id == entry.pattern_id
    assert reader.snapshot_audit["sha256"] == manifest["memory_sha256"]
    with pytest.raises(MemoryIntegrityError, match="read_only"):
        reader.append(entry)


def test_frozen_memory_rejects_tampering(tmp_path):
    path = tmp_path / "patterns.jsonl"
    store, _ = _record_verified_pattern(path)
    store.freeze(dataset_id="development-v2")
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(MemoryIntegrityError, match="hash_mismatch"):
        PatternStore(str(path), mode="read_only", require_frozen=True).load()


def test_memory_rejects_case_or_answer_fields(tmp_path):
    path = tmp_path / "patterns.jsonl"
    store, _ = _record_verified_pattern(path)
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    payload["recommended_actions"][0]["params"]["template_name"] = "case-answer"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(MemoryIntegrityError, match="case_or_answer"):
        PatternStore(
            str(path),
            mode="development_write",
            require_frozen=False,
        ).load()


def test_memory_drops_specific_or_answer_carrying_action_parameters(tmp_path):
    path = tmp_path / "patterns.jsonl"
    store = PatternStore(str(path), mode="development_write", require_frozen=False)
    entry = PatternRetriever(store).record_verified_pattern(
        trigger_signature={"route": "canonical"},
        regions=["Variable loop"],
        recommended_actions=[
            {
                "action_type": "repair_region_with_dp_only",
                "params": {
                    "focus_regions": ["Variable loop"],
                    "template_name": "case-specific-template",
                    "target_tokens": ["A", "C", "G"],
                },
            }
        ],
        score_gain=0.5,
        dataset_id="development-v2",
    )

    assert entry.recommended_actions[0]["params"] == {
        "focus_regions": ["Variable loop"]
    }


def test_memory_builder_counts_failed_trials_in_confidence(tmp_path):
    run_dir = tmp_path / "run"
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True)
    base_case = {
        "status": "ok",
        "input_len": 74,
        "benchmark_case": {
            "retrospective_split": "development",
            "metadata": {"broad_source": "bacteria"},
        },
        "agent": {
            "disposition": "accepted",
            "difficulty_tier": "hard",
            "route": "canonical",
        },
        "agent_trace": {
            "context_summary": {
                "numbering_backend": "cmalign",
                "failure_regions": ["Variable loop"],
            },
            "work_state_summary": {
                "action_history": [
                    {
                        "action_type": "repair_region_with_dp_only",
                        "context_signature": {
                            "length_bin": "70-79",
                            "difficulty_tier": "hard",
                            "route": "canonical",
                            "numbering_backend": "cmalign",
                            "domain_group": "archaea",
                            "input_type": "mature",
                            "intron_status": "absent",
                            "cm_evidence_present": True,
                            "warning_types": ["boundary"],
                            "failure_regions": ["Variable loop"],
                            "validation_reason_codes": ["boundary_invalid"],
                        },
                        "target_regions": ["Variable loop"],
                        "accepted": True,
                        "valid_after": True,
                        "score_before": 1.0,
                        "score_after": 2.0,
                    }
                ]
            },
        },
    }
    successful = {**base_case, "metrics": {"label_accuracy": 1.0}}
    failed = {**base_case, "metrics": {"label_accuracy": 0.5}}
    (cases_dir / "success.json").write_text(json.dumps(successful), encoding="utf-8")
    (cases_dir / "failure.json").write_text(json.dumps(failed), encoding="utf-8")
    output = tmp_path / "memory.jsonl"
    project_root = Path(__file__).resolve().parents[2]

    command = [
        sys.executable,
        "benchmark/scripts/build_agent_memory.py",
        "--run-dir",
        str(run_dir),
        "--dataset-id",
        "development-v2",
        "--output",
        str(output),
        "--min-trials",
        "2",
        "--min-success-rate",
        "0.5",
    ]
    subprocess.run(
        command,
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    first_snapshot = output.read_bytes()
    first_manifest = output.with_suffix(".jsonl.manifest.json").read_bytes()
    subprocess.run(
        command,
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert output.read_bytes() == first_snapshot
    assert output.with_suffix(".jsonl.manifest.json").read_bytes() == first_manifest
    assert payload["verified_trials"] == 2
    assert payload["verified_successes"] == 1
    assert payload["confidence"] == 0.5
    assert payload["trigger_signature"]["domain_group"] == "archaea"
    assert payload["trigger_signature"]["numbering_backend"] == "cmalign"
    assert payload["trigger_signature"]["validation_reason_codes"] == [
        "boundary_invalid"
    ]


def test_memory_builder_excludes_validation_fold_and_unreviewed_cases(tmp_path):
    run_dir = tmp_path / "run"
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True)
    case = {
        "status": "ok",
        "input_len": 74,
        "metrics": {"label_accuracy": 1.0},
        "benchmark_case": {
            "benchmark_partition": "retrospective_development",
            "development_fold": 3,
            "gold_evaluation_allowed": True,
            "metadata": {"broad_source": "bacteria"},
        },
        "agent": {"disposition": "accepted", "route": "canonical"},
        "agent_trace": {"work_state_summary": {"action_history": []}},
    }
    unreviewed = json.loads(json.dumps(case))
    unreviewed["benchmark_case"]["development_fold"] = 2
    unreviewed["benchmark_case"]["gold_evaluation_allowed"] = False
    (cases_dir / "held-out.json").write_text(json.dumps(case), encoding="utf-8")
    (cases_dir / "unreviewed.json").write_text(json.dumps(unreviewed), encoding="utf-8")
    output = tmp_path / "memory.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            "benchmark/scripts/build_agent_memory.py",
            "--run-dir",
            str(run_dir),
            "--dataset-id",
            "publication-v3-fold-3",
            "--output",
            str(output),
            "--expected-split",
            "retrospective_development",
            "--exclude-development-fold",
            "3",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)

    assert summary["skipped_validation_fold_cases"] == 1
    assert summary["skipped_unscored_cases"] == 1
    assert summary["pattern_count"] == 0
    manifest = json.loads(
        output.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["source_benchmark_partition"] == "retrospective_development"
    assert manifest["excluded_validation_fold"] == 3
    assert manifest["allow_unscored"] is False
    assert manifest["skipped_validation_fold_cases"] == 1
    assert manifest["skipped_unscored_cases"] == 1
