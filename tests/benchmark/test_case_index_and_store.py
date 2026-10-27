from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict

import pytest

from backend.domain.benchmarking.case_index import (
    INDEX_FIELDNAMES,
    attach_case_comparison,
    flatten_case_result,
    summarize_case_rows,
)
from backend.domain.gold_curation import service as curation
from backend.infra.storage import benchmark_run_store as store_module
from backend.infra.storage import sequence_repository as repo


def _sample_case() -> Dict[str, Any]:
    return {
        "seqname": "selftest|case1",
        "status": "ok",
        "input_len": 4,
        "input_seq": "ACGU",
        "elapsed_sec": 1.5,
        "method_backend": "agent",
        "template_name": "arch-num",
        "use_llm": True,
        "base_preserved": True,
        "agent": {"route": "canonical", "disposition": "accepted", "out_of_scope": False,
                  "in_canonical_benchmark": True},
        "agent_trace": {"trace_events": [
            {"stage": "agent_decision", "planner_source": "llm", "llm_called": True},
            {"stage": "tool_started", "tool": "align_numbering_cm"},
        ], "work_state_summary": {"stop_reason": "accepted_by_shared_hard_guards"}},
        "guard_challenge_audit": {"challenge_count": 2, "rejected_count": 1, "escaped_count": 0},
        "llm_usage": {"call_count": 1, "calls_with_usage": 1,
                      "calls": [{"status_code": 200}],
                      "tokens": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                                 "prompt_tokens_details.cached_tokens": 4}},
        "metrics": {"label_correct": 3, "label_total": 4, "label_accuracy": 0.75,
                    "exact_match": False, "legality_passed": True},
        "csv_content": "1,2,3,4\nA,C,G,U\nA,C,G,-\n",
        "benchmark_case": {"cohort_source": "bacteria", "curation_status": "baseline",
                           "terminal_reference_category": "complete_cca",
                           "representative_seqname": "selftest|case1",
                           "metadata": {"broad_source": "unknown"}},
    }


def test_flatten_emits_exactly_the_index_columns():
    flat = flatten_case_result(_sample_case())
    assert set(flat) == set(INDEX_FIELDNAMES)


def test_every_index_column_has_a_sql_type():
    for field in INDEX_FIELDNAMES:
        assert store_module.sql_type_for(field)


def test_scores_are_not_stored_alongside_the_predictions():
    """A score has one source: the answer table, read at grading time.

    The stored case row keeps run-time facts (status, route, tokens, the frozen
    prediction) and the prediction-derived legality flag, but nothing that a
    later curation decision could make disagree with the answer table.
    """
    persisted = {name for _, name in store_module.case_column_pairs()}
    assert persisted.isdisjoint(store_module.ANSWER_DERIVED_CASE_FIELDS)
    for name in ("exact_match", "label_correct", "diff_count", "priority"):
        assert name not in persisted
    assert set(store_module.ANSWER_DERIVED_CASE_FIELDS) < set(INDEX_FIELDNAMES)
    assert "legality_passed" in persisted


def test_measured_diff_count_matches_the_review_page():
    case = _sample_case()
    gold_labels = ["1", "2", "3", "4"]
    gold_bases = ["A", "C", "G", "U"]
    attach_case_comparison(case, gold_labels=gold_labels, gold_bases=gold_bases)

    page_diffs = curation._build_diffs(
        curation.ResidueMap(labels=gold_labels, bases=gold_bases),
        curation._pred_residue_map(str(case["csv_content"])),
    )
    assert case["diff_count"] == len(page_diffs)
    assert case["prediction_residue_count"] == 3
    assert case["diff_regions"]
    assert flatten_case_result(case)["diff_count"] == len(page_diffs)


def test_exact_case_records_zero_differences():
    case = _sample_case()
    case["csv_content"] = "1,2,3,4\nA,C,G,U\nA,C,G,U\n"
    attach_case_comparison(case, gold_labels=["1", "2", "3", "4"], gold_bases=["A", "C", "G", "U"])
    assert case["diff_count"] == 0
    # Priority keeps the review page's ordering rule: a legal candidate still
    # scores 10 even with nothing to correct.
    assert case["priority"] == 10


def _store_or_skip() -> store_module.BenchmarkRunStore:
    try:
        return store_module.connect_store(f"selftest-{uuid.uuid4().hex[:12]}")
    except store_module.BenchmarkStoreError as exc:
        pytest.skip(f"MySQL/MinIO not reachable: {exc}")


def test_store_round_trip_keeps_index_and_trace_together():
    """Run-time facts come back from columns; scores come back from the answer table."""
    store = _store_or_skip()
    case = _sample_case()
    attach_case_comparison(case, gold_labels=["1", "2", "3", "4"], gold_bases=["A", "C", "G", "U"])
    answer = {
        "seqname": case["seqname"],
        "raw_labels": ["1", "2", "3", "4"],
        "raw_bases": ["A", "C", "G", "U"],
        "gold_labels": ["1", "2", "3", "4"],
        "gold_bases": ["A", "C", "G", "U"],
        "gold_source": "raw",
        "evaluation_allowed": True,
    }
    try:
        trace = store.put_case(case)
        row = store.case_row(case["seqname"])
        assert row is not None
        # This sequence has no answer yet, so the case carries no score: the
        # number the run computed for itself is not kept beside the prediction.
        assert "diff_count" not in row and "label_incorrect" not in row
        assert row["cohort_source"] == "bacteria"
        # Legality follows from the prediction alone, so it does stay a column.
        assert row["legality_passed"] == 1
        assert row["trace_sha256"] == trace["sha256"]

        with store.tx() as cursor:
            repo.put_gold(cursor, answer)
        graded = store.case_row(case["seqname"])
        assert graded["diff_count"] == case["diff_count"]
        assert graded["label_incorrect"] == 1

        fetched = store.case_json(case["seqname"])
        assert fetched is not None and fetched["seqname"] == case["seqname"]

        store.put_attempt(case["seqname"], 1, outcome="ok", latency_sec=1.5)
        assert store.record_counts().get("attempt") == 1
    finally:
        for key in store.list_object_keys():
            store._s3.delete_object(Bucket=store.bucket, Key=key)
        with store.tx() as cursor:
            cursor.execute(
                f"DELETE FROM {store_module.EXECUTIONS_TABLE} WHERE run_id=%s",
                (store.run_id,),
            )
            cursor.execute("DELETE FROM trna_gold WHERE seqname=%s", (case["seqname"],))


def test_one_physical_table_serves_every_reader():
    """The retired run/case/attempt views are dropped, never recreated."""
    store = _store_or_skip()
    try:
        store.ensure_schema()
        rows = store.query(
            "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name IN (%s,%s,%s,%s)",
            (store_module.EXECUTIONS_TABLE, *store_module.RETIRED_TABLE_NAMES),
        )
        found = {str(row["TABLE_NAME"]): str(row["TABLE_TYPE"]) for row in rows}
        assert found.get(store_module.EXECUTIONS_TABLE) == "BASE TABLE"
        assert [
            name for name, kind in found.items() if name != store_module.EXECUTIONS_TABLE
        ] == []
    finally:
        with store.tx() as cursor:
            cursor.execute(
                f"DELETE FROM {store_module.EXECUTIONS_TABLE} WHERE run_id=%s",
                (store.run_id,),
            )


def test_answer_edit_moves_a_stored_run_without_calling_the_model():
    """The point of one answer table: history is re-graded, never replayed."""
    store = _store_or_skip()
    seqname = f"selftest|gold-move-{uuid.uuid4().hex[:12]}"
    case = _sample_case()
    case["seqname"] = seqname
    answer = {
        "seqname": seqname,
        "raw_labels": ["1", "2", "3", "4"],
        "raw_bases": ["A", "C", "G", "U"],
        "gold_labels": ["1", "2", "3", "4"],
        "gold_bases": ["A", "C", "G", "U"],
        "gold_source": "raw",
        "evaluation_allowed": True,
    }
    try:
        with store.tx() as cursor:
            repo.put_gold(cursor, answer)
        store.put_case(case)

        first = store.case_row(seqname)
        assert (first["label_correct"], first["label_total"]) == (3, 4)
        assert not first["exact_match"]

        # A reviewer decides the last position is not part of the core numbering.
        answer["gold_labels"] = ["1", "2", "3"]
        answer["gold_bases"] = ["A", "C", "G"]
        answer["gold_source"] = "manual"
        answer["decided_at"] = datetime(2000, 1, 1)
        with store.tx() as cursor:
            repo.put_gold(cursor, answer)

        again = store.case_row(seqname)
        assert (again["label_correct"], again["label_total"]) == (3, 3)
        assert again["exact_match"]
        assert again["gold_source"] == "manual"
        # Nothing was written back beside the prediction, so there is no stale
        # copy to reconcile.  What the review page can still say is how many
        # answers moved after the batch finished: the seal time is the cut-off.
        store.register_run({"state": "sealed", "model": "selftest"})
        with store.tx() as cursor:
            cursor.execute(
                f"UPDATE {store_module.EXECUTIONS_TABLE} "
                "SET run_finished_at=TIMESTAMP('2000-01-02 00:00:00') "
                "WHERE record_type='run' AND run_id=%s",
                (store.run_id,),
            )
        moved = store.revised_answers()
        assert moved["total"] == 1 and moved["missing"] == 0
        assert moved["revised"] == 0
        with store.tx() as cursor:
            cursor.execute(
                f"UPDATE {store_module.EXECUTIONS_TABLE} "
                "SET run_finished_at=TIMESTAMP('1999-12-31 00:00:00') "
                "WHERE record_type='run' AND run_id=%s",
                (store.run_id,),
            )
        assert store.revised_answers()["revised"] == 1

        # The prediction itself is frozen: the stored trace still carries the
        # four-column alignment, while the numbers shown with it have moved.
        trace = store.case_json(seqname)
        assert trace["csv_content"].splitlines()[0] == "1,2,3,4"
        assert trace["metrics"]["label_total"] == 3
        assert summarize_case_rows(store.case_rows())["metrics"]["residue_label_accuracy"] == 1.0
    finally:
        for key in store.list_object_keys():
            store._s3.delete_object(Bucket=store.bucket, Key=key)
        with store.tx() as cursor:
            cursor.execute(
                f"DELETE FROM {store_module.EXECUTIONS_TABLE} WHERE run_id=%s",
                (store.run_id,),
            )
            cursor.execute("DELETE FROM trna_gold WHERE seqname=%s", (seqname,))
