from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path

import pytest

from backend.domain.gold_curation import service


def test_removed_runs_cannot_be_loaded_from_external_archive(tmp_path, monkeypatch):
    current = tmp_path / "current"
    archive = tmp_path / "archive"
    run = archive / "previous"
    cases = run / "cases"
    cases.mkdir(parents=True)
    (run / "case_results.tsv").write_text("seqname\texact_match\nexample\tFalse\n")
    payload = {"seqname": "example", "csv_content": "archived prediction"}
    (cases / f"{service.case_digest('example')}.json").write_text(json.dumps(payload))
    monkeypatch.setattr(service, "DEFAULT_RUNS_DIR", current)
    monkeypatch.setattr(service, "_store_for", lambda _run_id: None)
    service._run_results_by_seqname.cache_clear()
    service._case_index_cached.cache_clear()
    try:
        with pytest.raises(FileNotFoundError):
            service._run_results_by_seqname("previous")
        with pytest.raises(FileNotFoundError):
            service._load_case_json("previous", "example")
        retained = current / "retained"
        (retained / "cases").mkdir(parents=True)
        (retained / "case_results.tsv").write_text("seqname\texact_match\nexample\tTrue\n")
        (retained / "cases" / f"{service.case_digest('example')}.json").write_text(json.dumps(payload))
        assert service._run_results_by_seqname("retained")["example"]["exact_match"] == "True"
        assert service._load_case_json("retained", "example") == payload
    finally:
        service._run_results_by_seqname.cache_clear()


@pytest.mark.parametrize("run_id", ["", ".", "..", "../archive/previous", "/tmp"])
def test_run_resolution_rejects_paths_outside_runs(tmp_path, monkeypatch, run_id):
    monkeypatch.setattr(service, "DEFAULT_RUNS_DIR", tmp_path)
    monkeypatch.setattr(service, "_store_for", lambda _run_id: None)
    with pytest.raises(FileNotFoundError):
        service._local_run_directory(run_id)
    with pytest.raises(FileNotFoundError):
        service.ensure_run_available(run_id)


def test_queue_excludes_gold_records_absent_from_selected_run(tmp_path, monkeypatch):
    run = tmp_path / "retained"
    run.mkdir()
    monkeypatch.setattr(service, "DEFAULT_RUNS_DIR", tmp_path)
    monkeypatch.setattr(service, "_gold_table_for_scope", lambda _scope: (
        ("seqname", "1"),
        {"in-run": {"1": "A"}, "only-in-gold": {"1": "C"}},
    ))
    monkeypatch.setattr(service, "_run_results_by_seqname", lambda _run: {
        "in-run": {"exact_match": "True", "label_accuracy": "1"},
    })
    service._case_index_cached.cache_clear()
    try:
        assert [item["seqname"] for item in service._case_index_cached("retained")] == ["in-run"]
    finally:
        service._case_index_cached.cache_clear()


def test_curation_api_defaults_to_retained_run_and_returns_404_for_removed_runs(tmp_path, monkeypatch):
    from flask import Flask
    from flask_restx import Api
    from backend.api.routes import gold_curation

    (tmp_path / service.DEFAULT_RUN_ID).mkdir()
    monkeypatch.setattr(service, "DEFAULT_RUNS_DIR", tmp_path)
    monkeypatch.setattr(gold_curation, "summary", lambda **kwargs: kwargs)
    app = Flask(__name__)
    gold_curation.register_gold_curation_namespace(Api(app))
    client = app.test_client()

    response = client.get("/gold-curation/summary")
    assert response.status_code == 200
    assert response.get_json()["run_id"] == service.DEFAULT_RUN_ID
    for endpoint in ("summary", "cases", "cases/example"):
        response = client.get(f"/gold-curation/{endpoint}?run_id=removed")
        assert response.status_code == 404
        assert "not retained" in response.get_json()["message"]


def _case(source: str) -> dict:
    return {
        "benchmark_case": {
            "metadata": {
                "broad_source": source,
            }
        }
    }


def test_classic_rereview_scope_requires_classic_mismatch_with_prediction() -> None:
    def item(source: str, *, diff_count: int = 1, available: bool = True) -> dict:
        return {
            "broad_source": source,
            "diff_count": diff_count,
            "prediction_available": available,
        }

    for source in ("archaea", "bacteria", "cytosolic_eukaryote"):
        assert service._item_matches_review_scope(service.CLASSIC_REREVIEW_SCOPE, item(source))

    assert not service._item_matches_review_scope(
        service.CLASSIC_REREVIEW_SCOPE, item("mitochondrial")
    )
    assert not service._item_matches_review_scope(
        service.CLASSIC_REREVIEW_SCOPE, item("bacteria", available=False)
    )
    assert not service._item_matches_review_scope(
        service.CLASSIC_REREVIEW_SCOPE, item("bacteria", diff_count=0)
    )
    assert service._item_matches_review_scope(service.ALL_MISMATCH_REREVIEW_SCOPE, item("mitochondrial"))
    assert service._item_matches_review_scope("", item("mitochondrial"))


def test_all_mismatch_scope_accepts_every_source_with_a_real_difference() -> None:
    # A safety-only terminal record has exact_match=None, so the queue must key
    # off the measured difference count rather than off a benchmark metric.
    assert service._item_matches_review_scope(
        service.ALL_MISMATCH_REREVIEW_SCOPE,
        {"broad_source": "mitochondrial", "diff_count": 3, "prediction_available": True},
    )
    assert service._item_matches_review_scope(
        service.ALL_MISMATCH_REREVIEW_SCOPE,
        {"broad_source": "virus", "diff_count": 1, "prediction_available": True},
    )
    assert not service._item_matches_review_scope(
        service.ALL_MISMATCH_REREVIEW_SCOPE,
        {"broad_source": "mitochondrial", "diff_count": 0, "prediction_available": True},
    )
    assert not service._item_matches_review_scope(
        service.ALL_MISMATCH_REREVIEW_SCOPE,
        {"broad_source": "mitochondrial", "diff_count": 2, "prediction_available": False},
    )


def test_round_corrections_do_not_consume_previous_review_round(tmp_path: Path) -> None:
    path = tmp_path / "corrections.jsonl"
    records = [
        {"seqname": "case-1", "status": "manual_corrected", "review_round": "old-round"},
        {
            "seqname": "case-2",
            "status": "accepted_gold",
            "review_round": service.CLASSIC_REREVIEW_ROUND,
        },
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    service._latest_round_corrections.cache_clear()

    current = service._latest_round_corrections(service.CLASSIC_REREVIEW_ROUND, path)

    assert "case-1" not in current
    assert current["case-2"]["status"] == "accepted_gold"


def test_rereview_keeps_a_committed_c_out_of_the_scratch_panel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_path = tmp_path / "original.tsv"
    curated_path = tmp_path / "curated.tsv"
    for path, row in (
        (original_path, ["case-1", "A", "C", "-"]),
        (curated_path, ["case-1", "A", "-", "C"]),
    ):
        with path.open("w", encoding="utf-8", newline="") as fout:
            writer = csv.writer(fout, delimiter="\t", lineterminator="\n")
            writer.writerow(["seqname", "1", "2", "3"])
            writer.writerow(row)

    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", original_path)
    monkeypatch.setattr(service, "DEFAULT_CURATED_GOLD_TSV", curated_path)
    monkeypatch.setattr(
        service,
        "_load_case_json",
        lambda _run_id, _seqname: {
            "csv_content": "1,2,3\nA,-,C\nA,-,C\n",
            "benchmark_case": {"metadata": {"broad_source": "bacteria"}},
        },
    )
    historical = {
        "case-1": {
            "status": "manual_corrected",
            "curated_labels": ["1", "3"],
        }
    }
    monkeypatch.setattr(service, "_latest_corrections", lambda: historical)
    monkeypatch.setattr(service, "_latest_round_corrections", lambda _round: {})

    bundle = service.load_case_bundle(
        "case-1",
        run_id="test-run",
        scope=service.CLASSIC_REREVIEW_SCOPE,
        include_structure=False,
    )

    assert bundle["gold"]["labels_by_residue"] == ["1", "2"]
    # The committed manual C is already the answer in A, so it does not fill C
    # again on the next visit.
    assert bundle["curated_labels"] is None
    assert bundle["curated_origin"] == ""
    assert bundle["comparison_reference"]["labels_by_residue"] == ["1", "2"]
    assert bundle["diff_count"] == 1
    assert bundle["historical_correction"] == historical["case-1"]


def test_scope_never_uses_derived_curated_tsv_as_hidden_a_b_reference(tmp_path: Path, monkeypatch) -> None:
    original_path = tmp_path / "original.tsv"
    curated_path = tmp_path / "curated.tsv"
    for path, row in (
        (original_path, ["case-1", "A", "C", "G"]),
        (curated_path, ["case-1", "A", "G", "C"]),
    ):
        with path.open("w", encoding="utf-8", newline="") as fout:
            writer = csv.writer(fout, delimiter="\t", lineterminator="\n")
            writer.writerow(["seqname", "1", "2", "3"])
            writer.writerow(row)

    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", original_path)
    monkeypatch.setattr(service, "DEFAULT_CURATED_GOLD_TSV", curated_path)
    monkeypatch.setattr(
        service,
        "_load_case_json",
        lambda _run_id, _seqname: {"csv_content": "1,2,3\nA,C,G\nA,C,G\n"},
    )
    monkeypatch.setattr(service, "_latest_corrections", lambda: {})
    monkeypatch.setattr(service, "_latest_round_corrections", lambda _round: {})

    bundle = service.load_case_bundle(
        "case-1",
        run_id="test-run",
        scope=service.ALL_MISMATCH_REREVIEW_SCOPE,
        include_structure=False,
    )

    assert bundle["gold"]["labels_by_residue"] == ["1", "2", "3"]
    assert bundle["prediction"]["labels_by_residue"] == ["1", "2", "3"]
    assert bundle["diff_count"] == 0


def test_server_draft_is_exposed_as_unsubmitted_curation(tmp_path: Path, monkeypatch) -> None:
    gold_path = tmp_path / "gold.tsv"
    draft_path = tmp_path / "drafts.jsonl"
    with gold_path.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "1", "2", "V12"])
        writer.writerow(["case-1", "A", "C", "G"])
    draft_path.write_text(
        json.dumps(
            {
                "seqname": "case-1",
                "run_id": "test-run",
                "review_scope": service.ALL_MISMATCH_REREVIEW_SCOPE,
                "status": "manual_corrected",
                "notes": "待人工确认的结构方案",
                "curated_labels": ["1", "2", "V12"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", gold_path)
    monkeypatch.setattr(service, "DEFAULT_CURATED_GOLD_TSV", gold_path)
    monkeypatch.setattr(service, "DEFAULT_DRAFTS_JSONL", draft_path)
    monkeypatch.setattr(
        service,
        "_load_case_json",
        lambda _run_id, _seqname: {"csv_content": "1,2,V12\nA,C,G\nA,C,G\n"},
    )
    monkeypatch.setattr(service, "_latest_corrections", lambda: {})
    monkeypatch.setattr(service, "_latest_round_corrections", lambda _round: {})
    service._latest_drafts.cache_clear()

    bundle = service.load_case_bundle(
        "case-1",
        run_id="test-run",
        scope=service.ALL_MISMATCH_REREVIEW_SCOPE,
        include_structure=False,
    )

    assert bundle["correction"] is None
    assert bundle["curated_labels"] == ["1", "2", "V12"]
    assert bundle["curated_origin"] == "server_draft"
    assert bundle["draft"]["notes"] == "待人工确认的结构方案"


def test_export_preserves_scoped_accepted_gold_labels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    gold_path = tmp_path / "gold.tsv"
    output_path = tmp_path / "curated.tsv"
    with gold_path.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(
            fout,
            fieldnames=["seqname", "1", "2"],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow({"seqname": "case-1", "1": "A", "2": "C"})

    correction = {
        "seqname": "case-1",
        "status": "accepted_gold",
        "review_round": service.CLASSIC_REREVIEW_ROUND,
        "curated_labels": ["1", "2"],
        "bases_by_residue": ["A", "C"],
    }
    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", gold_path)
    monkeypatch.setattr(service, "_latest_corrections", lambda: {"case-1": correction})

    result = service.export_curated_tsv(output_path)

    assert result["applied"] == 1
    rows = list(csv.DictReader(output_path.open(encoding="utf-8"), delimiter="\t"))
    assert rows == [{"seqname": "case-1", "1": "A", "2": "C"}]


def test_curation_evidence_flags_unresolved_raw_structure_and_keeps_pair_links() -> None:
    case_json = {
        "scientific_provenance": {
            "cm_score": {"average_posterior": 0.741, "bit_score": 23.33},
            "cm_portfolio": {
                "calibration": {
                    "status": "incompatible",
                    "disposition": "investigate",
                    "reason": "missing calibrated groups",
                }
            },
        },
        "agent_trace": {"context_summary": {"route_confidence": 0.0}},
    }
    structure = {
        "structure_by_residue": [">", "<"],
        "structure_status": "raw_prediction",
        "structure_meta": {
            "predictor": "tRNAscan-SE",
            "trnascan_isotype": "Undet",
            "trnascan_anticodon": "NNN",
        },
    }

    evidence = service._curation_evidence_summary(case_json, structure)

    assert evidence["high_risk"] is True
    assert evidence["structure"]["pair_links_allowed"] is True
    assert evidence["numbering"]["average_posterior"] == 0.741
    assert evidence["numbering"]["route_confidence"] == 0.0
    assert "trnascan_unresolved" in evidence["risk_codes"]
    assert "incompatible_cm_calibration" in evidence["risk_codes"]


def test_curation_evidence_keeps_pair_links_for_resolved_structure() -> None:
    evidence = service._curation_evidence_summary(
        {},
        {
            "structure_by_residue": [">", "<"],
            "structure_status": "raw_prediction",
            "structure_meta": {
                "predictor": "tRNAscan-SE",
                "trnascan_isotype": "Leu",
                "trnascan_anticodon": "GAG",
            },
        },
    )

    assert evidence["structure"]["pair_links_allowed"] is True


def test_exclusion_reason_is_preserved_in_decision_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    gold_path = tmp_path / "gold.tsv"
    corrections_path = tmp_path / "corrections.jsonl"
    with gold_path.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "1", "2"])
        writer.writerow(["case-1", "A", "C"])

    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", gold_path)
    monkeypatch.setattr(service, "DEFAULT_CORRECTIONS_JSONL", corrections_path)
    monkeypatch.setattr(
        service,
        "_load_case_json",
        lambda _run_id, _seqname: {
            "csv_content": "1,2\nA,C\nA,C\n",
            "metrics": {"exact_match": False},
        },
    )
    service._latest_corrections.cache_clear()
    service._latest_round_corrections.cache_clear()
    service._list_cases_cached.cache_clear()

    record = service.append_decision(
        "case-1",
        {"status": "excluded", "exclusion_reason": "non_canonical_trna"},
        run_id="test-run",
    )

    assert record["status"] == "excluded"
    assert record["exclusion_reason"] == "non_canonical_trna"


def test_run_batches_are_labelled_by_role_and_timestamp():
    """The batch selector must name the cohort, the controls and anything else."""
    assert (
        service._run_batch_label("20261020-164533-deepseekv4-flash-guangdong")
        == "2026-10-20 16:45"
    )
    # Control ids may omit seconds; a four-digit stamp still has to parse.
    assert service._run_batch_label("20261023-0515-control-raw-llm") == "2026-10-23 05:15"
    assert service._run_batch_role(service.DEFAULT_RUN_ID) == "主队列"
    assert service._run_batch_role("20261024-090000-scratch-retry") == "完整运行"
    assert service._run_batch_role("20261023-043053-control-harness-cm-root") == "对照 · CM 根候选"
    assert service._run_batch_role("20261023-0515-control-raw-llm") == "对照 · 仅语言模型"


def test_queue_filter_serves_committed_decisions_as_one_group(monkeypatch) -> None:
    """已完成 must be a queue the backend can page, not a client-side sum."""
    indexed = (
        {"seqname": "open", "diff_count": 2, "diff_regions": ["D loop + D stem"], "priority": 10},
        {"seqname": "adopted-a", "diff_count": 1, "diff_regions": [], "priority": 10},
        {"seqname": "hesitant", "diff_count": 1, "diff_regions": [], "priority": 9},
        {"seqname": "agrees", "diff_count": 0, "diff_regions": [], "priority": 8},
    )
    monkeypatch.setattr(service, "_case_index_cached", lambda *_args, **_kwargs: indexed)
    monkeypatch.setattr(
        service,
        "_latest_corrections",
        lambda *_args, **_kwargs: {
            "adopted-a": {"status": "accepted_gold"},
            "hesitant": {"status": "unsure"},
        },
    )
    monkeypatch.setattr(service.sequence_db, "enabled", lambda: False)

    completed = service.list_cases(run_id="grouped", status="completed")
    assert [item["seqname"] for item in completed["items"]] == ["adopted-a"]

    groups = completed["groups"]
    assert (groups["all"], groups["reviewable"], groups["needs_review"]) == (4, 3, 1)
    assert (groups["completed"], groups["unsure"], groups["excluded"]) == (1, 1, 0)

    # 全部差异 stays a mismatch queue, so a decided case remains reviewable.
    reviewable = service.list_cases(run_id="grouped", status="reviewable")
    assert [item["seqname"] for item in reviewable["items"]] == ["open", "adopted-a", "hesitant"]


def test_panel_a_tracks_the_answer_table_and_a_committed_c_clears_the_draft(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Panel A is the live answer; C only ever holds an unsubmitted draft."""
    numbering = ("seqname", "1", "2", "3")
    answer_rows = {"case-1": {"seqname": "case-1", "1": "A", "2": "-", "3": "C"}}
    raw_rows = {"case-1": {"seqname": "case-1", "1": "A", "2": "C", "3": "-"}}

    def fake_store(which: str):
        return numbering, (answer_rows if which == "gold" else raw_rows)

    monkeypatch.setattr(service, "_store_gold_matrix", fake_store)
    monkeypatch.setattr(service.sequence_db, "enabled", lambda: False)
    monkeypatch.setattr(
        service,
        "_load_case_json",
        lambda _run_id, _seqname: {
            "csv_content": "1,2,3\nA,C,-\nA,C,-\n",
            "benchmark_case": {"metadata": {"broad_source": "bacteria"}},
        },
    )

    # Keep the audit log on its file backing while MySQL is off, and point the
    # readers at the same temporary file the writers append to.
    corrections = tmp_path / "corrections.jsonl"
    read_corrections = service._latest_corrections.__wrapped__
    read_round_corrections = service._latest_round_corrections.__wrapped__
    monkeypatch.setattr(
        service,
        "_latest_corrections",
        lru_cache(maxsize=None)(lambda: read_corrections(corrections)),
    )
    monkeypatch.setattr(
        service,
        "_latest_round_corrections",
        lru_cache(maxsize=None)(lambda *args: read_round_corrections(*args, corrections)),
    )
    monkeypatch.setattr(service, "DEFAULT_CORRECTIONS_JSONL", corrections)
    monkeypatch.setattr(service, "DEFAULT_DRAFTS_JSONL", tmp_path / "drafts.jsonl")
    scope = service.ALL_MISMATCH_REREVIEW_SCOPE

    def load() -> dict:
        return service.load_case_bundle(
            "case-1", run_id="test-run", scope=scope, include_structure=False
        )

    bundle = load()
    assert bundle["gold"]["labels_by_residue"] == ["1", "3"]
    assert bundle["raw"]["labels_by_residue"] == ["1", "2"]
    assert bundle["prediction"]["labels_by_residue"] == ["1", "2"]
    # Gaps drop out of both residue maps, so the answer and the model meet at
    # residue 2 with different Sprinzl labels: position 3 against position 2.
    assert bundle["diff_count"] == 1
    assert bundle["curated_labels"] is None

    service.append_draft(
        "case-1",
        {"status": "manual_corrected", "curated_labels": ["1", "2"]},
        run_id="test-run",
        scope=scope,
    )
    with_draft = load()
    assert with_draft["curated_origin"] == "server_draft"
    assert with_draft["curated_labels"] == ["1", "2"]

    record = service.append_decision(
        "case-1",
        {"status": "manual_corrected", "curated_labels": ["1", "2"]},
        run_id="test-run",
        scope=scope,
    )
    # The audit row keeps the untouched tRNAdb numbering, the answer it replaced,
    # and the labels the reviewer committed.
    assert record["curated_labels"] == ["1", "2"]
    assert record["original_gold_labels"] == ["1", "2"]
    assert record["baseline_labels"] == ["1", "3"]

    committed = load()
    # The draft became the answer, so C is blank and that draft no longer looks
    # pending: the decision superseded it.
    assert committed["curated_labels"] is None
    assert committed["curated_origin"] == ""
    assert committed["status"] == "manual_corrected"
