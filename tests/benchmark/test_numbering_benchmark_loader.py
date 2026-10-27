from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


RUNNER_PATH = Path(__file__).resolve().parents[2] / "benchmark" / "scripts" / "run_numbering_benchmark.py"
SPEC = importlib.util.spec_from_file_location("numbering_benchmark_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def test_generated_checkpoints_do_not_invalidate_code_resume_contract(tmp_path, monkeypatch):
    source = tmp_path / "APP/backend/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Benchmark Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
        cwd=tmp_path, check=True,
    )
    monkeypatch.setattr(RUNNER, "PROJECT_ROOT", tmp_path)
    assert RUNNER.git_worktree_dirty() is False
    checkpoint = tmp_path / "benchmark/runs/example/cases/result.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text('{}\n')
    assert RUNNER.git_worktree_dirty() is False
    source.write_text("value = 2\n")
    assert RUNNER.git_worktree_dirty() is True


def test_curated_empty_insertion_columns_are_not_counted_as_residues(tmp_path: Path):
    gold = tmp_path / "curated.tsv"
    with gold.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "1", "1i1", "2", "2i1", "3"])
        writer.writerow(["case-a", "A", "", "C", "-", "U"])

    cases = RUNNER.load_gold_cases(gold)

    assert len(cases) == 1
    assert cases[0].input_seq == "ACU"
    assert cases[0].gold_labels == ("1", "2", "3")
    assert cases[0].gold_bases == ("A", "C", "U")


def test_case_checkpoint_reuses_only_matching_success():
    # Resume works from a MySQL row or a stored case JSON; both are plain mappings.
    assert not RUNNER.should_run_case(
        "done", {"seqname": "done", "status": "ok"}, rerun_all=False, rerun_failed=False
    )
    assert RUNNER.should_run_case(
        "failed", {"seqname": "failed", "status": "error"}, rerun_all=False, rerun_failed=False
    )
    assert RUNNER.should_run_case("missing", None, rerun_all=False, rerun_failed=False)
    assert RUNNER.should_run_case(
        "done", {"seqname": "done", "status": "ok"}, rerun_all=True, rerun_failed=False
    )


def test_checkpoint_retries_hidden_timeout_even_after_http_success():
    result = {
        "seqname": "handled-timeout", "status": "ok",
        "llm_usage": {"calls": [{"status_code": None}, {"status_code": 200}]},
    }
    RUNNER.enforce_llm_failure_policy(result, strict=True)
    assert result["status"] == "error"
    assert result["error_category"] == "llm_technical_failure"
    assert result["llm_technical_issues"]["failed_requests"] == 1
    assert RUNNER.should_run_case(
        result["seqname"], result, rerun_all=False, rerun_failed=True
    )


def test_checkpoint_retries_planner_fallback_without_usage():
    result = {
        "seqname": "fallback", "status": "ok",
        "agent_trace": {"trace_events": [{"agent_decision": {
            "planner_source": "deterministic_fallback",
        }}]},
    }
    # A silent deterministic fallback is a technical issue, so it is retried
    # whether or not --rerun-failed was passed.
    assert RUNNER.should_run_case(result["seqname"], result, rerun_all=False, rerun_failed=True)
    assert RUNNER.should_run_case(result["seqname"], result, rerun_all=False, rerun_failed=False)


def test_successful_api_and_rejected_scientific_tool_are_reusable():
    result = {
        "seqname": "success", "status": "ok",
        "llm_usage": {"calls": [{"status_code": 200}]},
        "agent_trace": {"trace_events": [{
            "stage": "initial_tool_failed", "error_type": "ValueError",
        }]},
    }
    RUNNER.enforce_llm_failure_policy(result, strict=True)
    assert result["status"] == "ok"
    assert not RUNNER.should_run_case(result["seqname"], result, rerun_all=False, rerun_failed=True)


def test_invalid_planner_json_cannot_be_hidden_by_returned_coordinates():
    result = {
        "status": "ok", "csv_content": "retained coordinates",
        "llm_usage": {"calls": [{"status_code": 200}]},
        "llm_failure_events": [{"stage": "llm_decision_failed", "error_type": "ValueError"}],
    }
    RUNNER.enforce_llm_failure_policy(result, strict=True)
    assert result["status"] == "error"
    assert result["csv_content"] == "retained coordinates"
    assert result["llm_technical_issues"]["decision_failures"] == 1


def test_endpoint_provenance_records_actual_output_budget_and_thinking_parameter():
    provenance = RUNNER.build_llm_endpoint_provenance({
        "LLM_PROVIDER": "deepseek", "DEEPSEEK_MODEL": "deepseekv4-flash-guangdong",
        "DEEPSEEK_API_KEY": "private-test-key", "DEEPSEEK_THINKING": "enabled",
        "AGENT_PLANNER_MAX_TOKENS": 256 * 1024, "LLM_MAX_TOKENS": 256 * 1024,
        "LLM_STREAM": True,
    })
    assert provenance["request_parameters"] == {
        "max_tokens": 256 * 1024, "stream": True, "thinking": {"type": "enabled"},
    }
    assert "private-test-key" not in json.dumps(provenance)


def test_resume_contract_allows_only_transport_interval_change():
    base = {
        "inputs": {"gold": {"sha256": "gold-digest"}},
        "method": {
            "backend": "agent",
            "model": "mimo-v2.5-pro",
            "llm_endpoint": {
                "base_url": "https://api.xiaomimimo.com/v1",
                "credential_pool_size": 2,
                "request_policy": {
                    "min_request_interval_s": 3.2,
                    "max_retries": 10,
                },
            },
        },
    }
    changed_interval = {
        "inputs": {"gold": {"sha256": "gold-digest"}},
        "method": {
            "backend": "agent",
            "model": "mimo-v2.5-pro",
            "llm_endpoint": {
                "base_url": "https://api.xiaomimimo.com/v1",
                "credential_pool_size": 1,
                "request_policy": {
                    "min_request_interval_s": 0.0,
                    "max_retries": 10,
                },
            },
        },
    }
    changed_model = json.loads(json.dumps(changed_interval))
    changed_model["method"]["model"] = "other-model"

    assert RUNNER.resume_contract_compatible(base, changed_interval)
    assert not RUNNER.resume_contract_compatible(base, changed_model)


def test_non_cca_reference_tail_remains_in_coordinate_scoring(tmp_path: Path):
    gold = tmp_path / "non-cca.tsv"
    with gold.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "73", "74", "75", "76"])
        writer.writerow(["case-a", "G", "C", "A", "A"])

    cases = RUNNER.load_gold_cases(gold)

    assert cases[0].input_seq == "GCAA"
    assert cases[0].benchmark_meta["terminal_reference_category"] == (
        "non_cca_reference"
    )
    assert cases[0].benchmark_meta.get("gold_evaluation_allowed", True) is True
    assert "terminal_coordinate_evaluation" not in cases[0].benchmark_meta


def test_incomplete_cca_reference_tail_remains_in_coordinate_scoring(
    tmp_path: Path,
):
    gold = tmp_path / "incomplete-cca.tsv"
    with gold.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "73", "74", "75", "76"])
        writer.writerow(["case-a", "G", "C", "C", "-"])

    cases = RUNNER.load_gold_cases(gold)

    assert cases[0].input_seq == "GCC"
    assert cases[0].benchmark_meta["terminal_reference_category"] == (
        "incomplete_terminal"
    )
    assert cases[0].benchmark_meta.get("gold_evaluation_allowed", True) is True
    assert "terminal_coordinate_evaluation" not in cases[0].benchmark_meta


def test_submission_manifest_metadata_is_bound_to_representative_seqname(
    tmp_path: Path,
):
    manifest = tmp_path / "submission.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "representative_seqname": "case-a",
                "retrospective_split": "development",
                "primary_reference_ready": True,
                "similarity_cluster": {"cluster_id": "cluster-a"},
                "metadata": {"broad_source": "archaea"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = RUNNER.load_submission_case_metadata(manifest)

    assert records["case-a"]["retrospective_split"] == "development"
    assert records["case-a"]["similarity_cluster"]["cluster_id"] == "cluster-a"


def test_submission_manifest_rejects_duplicate_representatives(tmp_path: Path):
    manifest = tmp_path / "submission.jsonl"
    row = {
        "schema_version": "2.0",
        "representative_seqname": "case-a",
    }
    manifest.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )

    try:
        RUNNER.load_submission_case_metadata(manifest)
    except ValueError as exc:
        assert "duplicate representative" in str(exc)
    else:
        raise AssertionError("duplicate submission representative was accepted")


def test_submission_manifest_accepts_publication_v31_evidence_tier(tmp_path: Path):
    manifest = tmp_path / "publication.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "3.1",
                "representative_seqname": "case-a",
                "benchmark_partition": "retrospective_development",
                "gold_evaluation_allowed": True,
                "reference_evidence_tier": "trnadb_database_reference",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = RUNNER.load_submission_case_metadata(manifest)

    assert records["case-a"]["gold_evaluation_allowed"] is True
    assert records["case-a"]["reference_evidence_tier"] == "trnadb_database_reference"


def test_cm_model_group_uses_manifest_broad_source():
    def case(source: str):
        return RUNNER.GoldCase(
            seqname=source,
            input_seq="ACU",
            gold_labels=("1", "2", "3"),
            gold_bases=("A", "C", "U"),
            benchmark_meta={"metadata": {"broad_source": source}},
        )

    assert RUNNER.cm_model_group_for_case(case("archaea")) == "arch"
    assert RUNNER.cm_model_group_for_case(case("bacteria")) == "bact"
    assert RUNNER.cm_model_group_for_case(case("cyanelle")) == "bact"
    assert RUNNER.cm_model_group_for_case(case("cytosolic_eukaryote")) == "euk"
    assert RUNNER.cm_model_group_for_case(case("chloroplast")) == "euk"
    assert RUNNER.cm_model_group_for_case(case("mitochondrial")) == "all"
    assert RUNNER.cm_model_group_for_case(case("virus")) == "all"


def test_agent_benchmark_contract_preserves_special_source_without_clade_guessing():
    mitochondrial = RUNNER.GoldCase(
        seqname="mt-case",
        input_seq="ACU",
        gold_labels=("1", "2", "3"),
        gold_bases=("A", "C", "U"),
        benchmark_meta={
            "metadata": {
                "broad_source": "mitochondrial",
                "amino_acid": "Ala",
            }
        },
    )
    sec = RUNNER.GoldCase(
        seqname="sec-case",
        input_seq="ACU",
        gold_labels=("1", "2", "3"),
        gold_bases=("A", "C", "U"),
        benchmark_meta={
            "metadata": {
                "broad_source": "archaea",
                "amino_acid": "SeC",
            }
        },
    )

    mt_contract = RUNNER.biological_contract_for_case(mitochondrial)
    sec_contract = RUNNER.biological_contract_for_case(sec)

    assert mt_contract["input_type"] == "mature"
    assert mt_contract["domain_hint"] == "mitochondrial"
    assert mt_contract["mitochondrial_subtype"] == "unknown"
    assert mt_contract["trna_class"] == "canonical"
    assert sec_contract["domain_hint"] == "archaea"
    assert sec_contract["trna_class"] == "selenocysteine"


def test_cm_backend_uses_shared_case_evaluator(monkeypatch):
    case = RUNNER.GoldCase(
        seqname="cm-case",
        input_seq="ACU",
        gold_labels=("1", "2", "3"),
        gold_bases=("A", "C", "U"),
        benchmark_meta={"metadata": {"broad_source": "archaea"}},
    )

    class FakeCMService:
        def align(self, sequence, *, model_group, sequence_id):
            assert sequence == "ACU"
            assert model_group == "arch"
            assert sequence_id == "cm-case"
            return SimpleNamespace(
                model=SimpleNamespace(name="arch-num"),
                warnings=[],
                csv_content=lambda: "1,2,3\n-,-,-\nA,C,U\n",
                metadata=lambda: {
                    "structure_view": {},
                    "agent_v2": {
                        "route": "cm_direct",
                        "trace_events": [{"stage": "tool_observation"}],
                    },
                },
            )

    monkeypatch.setattr(RUNNER, "get_cm_service", lambda args: FakeCMService())
    monkeypatch.setattr(RUNNER, "reset_usage_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        RUNNER,
        "get_usage_summary",
        lambda *args, **kwargs: {"call_count": 0, "calls_with_usage": 0, "tokens": {}, "calls": []},
    )
    args = SimpleNamespace(
        method_backend="cm",
        no_llm=False,
        llm_reasoning=False,
        run_id="cm-test",
        provider="ignored",
        model="ignored",
    )

    result = RUNNER.run_one_case(case, args, {})

    assert result["status"] == "ok"
    assert result["method_backend"] == "cm"
    assert result["use_llm"] is False
    assert result["template_name"] == "arch-num"
    assert result["metrics"]["label_accuracy"] == 1.0
    assert result["metrics"]["exact_match"] is True
    assert result["base_preserved"] is True
    assert result["agent_trace"]["trace_events"][0] == {"stage": "tool_observation"}
    assert result["agent_trace"]["trace_events"][-1]["disposition"] == "accepted"


def test_backend_error_preserves_benchmark_case_metadata(monkeypatch):
    benchmark_meta = {
        "selected_protocol": "unique_sequence",
        "selected_split": "test",
        "metadata": {"broad_source": "mitochondrial"},
    }
    case = RUNNER.GoldCase(
        seqname="failing-case",
        input_seq="ACU",
        gold_labels=("1", "2", "3"),
        gold_bases=("A", "C", "U"),
        benchmark_meta=benchmark_meta,
    )

    class FailingCMService:
        def align(self, *args, **kwargs):
            raise RuntimeError("expected failure")

    monkeypatch.setattr(RUNNER, "get_cm_service", lambda args: FailingCMService())
    monkeypatch.setattr(RUNNER, "reset_usage_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        RUNNER,
        "get_usage_summary",
        lambda *args, **kwargs: {"call_count": 0, "calls_with_usage": 0, "tokens": {}, "calls": []},
    )
    args = SimpleNamespace(
        method_backend="cm",
        no_llm=False,
        llm_reasoning=False,
        run_id="cm-error-test",
        provider="ignored",
        model="ignored",
    )

    result = RUNNER.run_one_case(case, args, {})

    assert result["status"] == "error"
    assert result["benchmark_case"] == benchmark_meta
    assert result["method_backend"] == "cm"


def test_agent_activity_summary_is_derived_from_trace_events():
    rows = [
        {
            "agent_trace": {
                "trace_events": [
                    {"stage": "planning", "planned_actions": [{}, {}]},
                    {
                        "stage": "action_executed",
                        "action_type": "align_numbering_cm",
                        "disposition": "accepted",
                    },
                ],
                "work_state_summary": {"stop_reason": "legal_and_no_open_issues"},
            }
        },
        {
            "agent_trace": {
                "trace_events": [
                    {
                        "stage": "action_failed",
                        "action_type": "repair_region_with_dp_only",
                        "disposition": "rejected",
                    }
                ],
                "work_state_summary": {"stop_reason": "no_actionable_actions"},
            }
        },
    ]

    activity = RUNNER.aggregate_agent_activity(rows)

    assert activity["planning_event_count"] == 1
    assert activity["planned_action_count"] == 2
    assert activity["executed_action_count"] == 2
    assert activity["failed_action_count"] == 1
    assert activity["cm_tool_call_count"] == 1
    assert activity["cases_with_actions"] == 2
    assert activity["dispositions"] == {"accepted": 1, "rejected": 1}


def test_guard_challenges_reject_integrity_violations():
    request = RUNNER.AlignmentRequest(target_seq="ACU", use_llm=False)
    snapshot = RUNNER.RootSnapshot(
        template_name="synthetic",
        csv_content="1,2,3\nA,C,U\nA,C,U\n",
        numbering_row=["1", "2", "3"],
        template_row=["A", "C", "U"],
        sequence_row=["A", "C", "U"],
    )

    audit = RUNNER.evaluate_guard_challenges(request, snapshot)

    assert audit["baseline_valid"] is True
    assert audit["challenge_count"] == 6
    assert audit["rejected_count"] == 6
    assert audit["escaped_count"] == 0
    assert {item["challenge"] for item in audit["challenges"]} == {
        "row_length_inconsistency",
        "base_substitution",
        "illegal_sprinzl_label",
        "invalid_input_symbol",
        "duplicate_sprinzl_label",
        "sprinzl_order_violation",
    }


def test_guard_challenge_summary_reports_eligible_denominators():
    rows = [
        {
            "status": "ok",
            "guard_challenge_audit": {
                "baseline_valid": True,
                "challenges": [
                    {"challenge": "illegal_sprinzl_label", "rejected": True},
                    {"challenge": "base_substitution", "rejected": False},
                ],
            },
        },
        {
            "status": "ok",
            "guard_challenge_audit": {
                "baseline_valid": False,
                "challenges": [],
            },
        },
    ]

    summary = RUNNER.aggregate_guard_challenges(rows)

    assert summary["baseline_valid_count"] == 1
    assert summary["baseline_invalid_count"] == 1
    assert summary["challenge_count"] == 2
    assert summary["rejected_count"] == 1
    assert summary["escaped_count"] == 1
    assert summary["rejection_rate"] == 0.5
    assert summary["by_challenge"]["illegal_sprinzl_label"]["rejection_rate"] == 1.0


def test_shared_guard_uses_manifest_biological_contract_for_cm_cca():
    case = RUNNER.GoldCase(
        seqname="cm-cca",
        input_seq="AUGCCA",
        gold_labels=("1", "2", "73", "74", "75", "76"),
        gold_bases=("A", "U", "G", "C", "C", "A"),
        benchmark_meta={"metadata": {"broad_source": "bacteria"}},
    )
    result = RUNNER.evaluate_case(
        case,
        template_name="bact-num",
        csv_content="1,2,73,74,75,76\nA,U,G,C,C,A\nA,U,G,C,C,A\n",
        warnings=[],
        meta={
            "numbering_backend": "cmalign",
            "cm_score": {"bit_score": 42.0, "average_posterior": 0.99},
            "cm_portfolio": {"selection_policy": "explicit_domain_hint"},
            "agent_v2": {
                "hard_guard_passed": True,
                "work_state_summary": {"disposition": "accepted"},
            },
        },
        use_llm=False,
        llm_reasoning=False,
        runtime_overrides={},
        elapsed_sec=0.1,
    )

    assert result["metrics"]["legality_passed"] is True
    assert result["agent"]["disposition"] == "accepted"
    assert result["scientific_provenance"]["specialized_route"] == {}
    assert result["scientific_provenance"]["structural_region_evidence"] == {}
    assert result["guard_challenge_audit"]["baseline_valid"] is True
    assert any(
        item["challenge"] == "unsupported_cca_assignment"
        for item in result["guard_challenge_audit"]["challenges"]
    )


def test_shared_guard_has_final_veto_over_internal_acceptance():
    case = RUNNER.GoldCase(
        seqname="illegal",
        input_seq="AC",
        gold_labels=("1", "2"),
        gold_bases=("A", "C"),
        benchmark_meta={"metadata": {"broad_source": "bacteria"}},
    )
    result = RUNNER.evaluate_case(
        case,
        template_name="synthetic",
        csv_content="1,999\nA,C\nA,C\n",
        warnings=[],
        meta={
            "agent_v2": {
                "hard_guard_passed": True,
                "work_state_summary": {
                    "disposition": "accepted",
                    "stop_reason": "internal_acceptance",
                },
            }
        },
        use_llm=False,
        llm_reasoning=False,
        runtime_overrides={},
        elapsed_sec=0.1,
    )

    assert result["metrics"]["legality_passed"] is False
    assert result["agent"]["internal_hard_guard_passed"] is True
    assert result["agent"]["hard_guard_passed"] is False
    assert result["agent"]["disposition"] == "investigate"
    assert result["agent_trace"]["work_state_summary"]["stop_reason"] == (
        "rejected_by_shared_hard_guards"
    )


def test_unreviewed_case_is_run_but_gold_accuracy_is_masked():
    case = RUNNER.GoldCase(
        seqname="unreviewed",
        input_seq="AC",
        gold_labels=("1", "2"),
        gold_bases=("A", "C"),
        benchmark_meta={
            "gold_evaluation_allowed": False,
            "metadata": {"broad_source": "bacteria"},
        },
    )

    result = RUNNER.evaluate_case(
        case,
        template_name="synthetic",
        csv_content="1,2\nA,C\nA,C\n",
        warnings=[],
        meta={"agent_v2": {"work_state_summary": {"disposition": "accepted"}}},
        use_llm=False,
        llm_reasoning=False,
        runtime_overrides={},
        elapsed_sec=0.1,
    )

    assert result["gold_evaluation_allowed"] is False
    assert result["base_preserved"] is True
    assert result["metrics"]["label_total"] == 0
    assert result["metrics"]["label_accuracy"] is None
    assert result["metrics"]["exact_match"] is None


def test_cm_tool_call_count_prefers_current_tool_trace_over_legacy_action_trace():
    events = [
        {"stage": "tool_started", "tool": "align_numbering_cm"},
        {"stage": "tool_started", "tool": "score_numbering_models"},
        {"stage": "tool_started", "tool": "align_numbering_cm"},
        {"stage": "action_executed", "action_type": "align_numbering_cm"},
    ]

    assert RUNNER.count_cm_tool_calls(events) == 2


def test_summary_separates_accepted_accuracy_from_abstention_coverage():
    rows = [
        {
            "status": "ok",
            "base_preserved": True,
            "metrics": {
                "label_correct": 3,
                "label_total": 3,
                "exact_match": True,
                "region_correct": 3,
                "region_total": 3,
                "legality_passed": True,
            },
            "agent": {"disposition": "accepted", "out_of_scope": False},
        },
        {
            "status": "ok",
            "base_preserved": False,
            "metrics": {
                "label_correct": 0,
                "label_total": 3,
                "exact_match": False,
                "region_correct": 0,
                "region_total": 3,
                "legality_passed": False,
            },
            "agent": {"disposition": "abstained", "out_of_scope": True},
        },
    ]

    summary = RUNNER.summarize_results(rows)

    assert summary["counts"]["dispositions"] == {"abstained": 1, "accepted": 1}
    assert summary["metrics"]["residue_label_accuracy"] == 0.5
    assert summary["selective_metrics"]["accepted_coverage"] == 0.5
    assert summary["selective_metrics"]["accepted_residue_label_accuracy"] == 1.0
    assert summary["selective_metrics"]["accepted_exact_match_rate"] == 1.0


def test_summary_excludes_unreviewed_cases_from_accuracy_only():
    rows = [
        {
            "status": "ok",
            "gold_evaluation_allowed": True,
            "base_preserved": True,
            "metrics": {
                "label_correct": 2,
                "label_total": 2,
                "exact_match": True,
                "region_correct": 2,
                "region_total": 2,
                "legality_passed": True,
            },
            "agent": {"disposition": "accepted", "out_of_scope": False},
        },
        {
            "status": "ok",
            "gold_evaluation_allowed": False,
            "base_preserved": True,
            "metrics": {
                "label_correct": None,
                "label_total": 0,
                "exact_match": None,
                "region_correct": None,
                "region_total": 0,
                "legality_passed": True,
            },
            "agent": {"disposition": "accepted", "out_of_scope": False},
        },
    ]

    summary = RUNNER.summarize_results(rows)

    assert summary["counts"]["total_cases"] == 2
    assert summary["counts"]["gold_scored_cases"] == 1
    assert summary["counts"]["gold_unscored_cases"] == 1
    assert summary["metrics"]["residue_label_accuracy"] == 1.0
    assert summary["metrics"]["exact_match_rate"] == 1.0
    assert summary["selective_metrics"]["accepted_coverage"] == 1.0


def test_staged_gold_provenance_labels_the_answer_each_score_used(tmp_path):
    from backend.domain.benchmarking import case_index

    tsv = tmp_path / "reference.tsv"
    with tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "1", "2", "3"])
        writer.writerow(["case-1", "A", "C", "G"])
        writer.writerow(["case-2", "A", "-", "G"])
    staged = {
        "table": "mysql://trna_gold",
        "sequences": {
            "case-1": {
                "gold_sha256": "f" * 64,
                "gold_source": "manual",
                "evaluation_allowed": True,
            }
        },
    }
    (tmp_path / "reference.provenance.json").write_text(
        json.dumps(staged, ensure_ascii=False), encoding="utf-8"
    )

    cases = RUNNER.load_gold_cases(tsv)
    by_name = {case.seqname: case for case in cases}
    assert by_name["case-1"].benchmark_meta["gold_labels_sha256"] == "f" * 64
    assert by_name["case-1"].benchmark_meta["correction_decision"] == "manual"
    assert by_name["case-1"].benchmark_meta["gold_source"] == "trna_gold"
    # A case staged without provenance still gets a fingerprint of its answer.
    assert by_name["case-2"].benchmark_meta["gold_labels_sha256"] == case_index.gold_fingerprint(
        ["1", "3"], ["A", "G"]
    )
    flat = case_index.flatten_case_result(
        {
            "seqname": "case-1",
            "status": "ok",
            "metrics": {},
            "benchmark_case": by_name["case-1"].benchmark_meta,
        }
    )
    assert flat["gold_labels_sha256"] == "f" * 64
