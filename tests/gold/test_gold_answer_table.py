"""The single active answer per sequence: how it is derived and how it renders."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from backend.domain.benchmarking import case_index
from backend.domain.gold_curation import service
from backend.infra.storage import sequence_repository


class _Map:
    """Minimal stand-in for a stored answer row."""

    @staticmethod
    def residue_map(labels, bases):
        return service.ResidueMap(labels=list(labels), bases=list(bases))


def test_digest_agrees_between_answer_table_and_case_index():
    labels, bases = ["45", "V11", "57"], ["A", "G", "C"]
    assert sequence_repository.residue_digest(labels, bases) == case_index.gold_fingerprint(
        labels, bases
    )


def test_no_decision_keeps_tRNAdb_numbering_as_the_answer():
    raw = service.ResidueMap(labels=["1", "2"], bases=["A", "C"])
    row = service._gold_row_for("case-1", raw, None)
    assert row["gold_source"] == "raw"
    assert row["gold_labels"] == ["1", "2"]
    assert row["decided_at"] is None
    assert row["evaluation_allowed"] is True


@pytest.mark.parametrize(
    "decision,expected_source",
    [
        ("accepted_gold", "adopted_gold"),
        ("accepted_model", "adopted_model"),
        ("manual_corrected", "manual"),
    ],
)
def test_committed_decision_becomes_the_answer(decision, expected_source):
    raw = service.ResidueMap(labels=["1", "2"], bases=["A", "C"])
    record = {
        "record_hash": "h1",
        "run_id": "run-7",
        "payload": {
            "status": decision,
            "curated_labels": ["1", "2i1"],
            "bases_by_residue": ["A", "C"],
            "curated_at": "2026-09-14T10:00:00+0800",
            "run_id": "run-7",
        },
    }
    row = service._gold_row_for("case-1", raw, record)
    assert row["gold_source"] == expected_source
    assert row["gold_labels"] == ["1", "2i1"]
    assert row["decision_hash"] == "h1"
    # An adopted answer keeps pointing at the run it came from.
    assert row["decision_run_id"] == "run-7"
    assert row["decided_at"] is not None


def test_excluded_sequence_stays_visible_but_unscored():
    raw = service.ResidueMap(labels=["1", "2"], bases=["A", "C"])
    record = {
        "record_hash": "h2",
        "run_id": "run-7",
        "payload": {"status": "excluded", "exclusion_reason": "non_canonical_trna"},
    }
    row = service._gold_row_for("case-1", raw, record)
    assert row["gold_source"] == "excluded"
    assert row["evaluation_allowed"] is False
    assert row["gold_labels"] == ["1", "2"]

def test_manual_exclusion_survives_a_rebuild_of_the_answer():
    """Eligibility is independent: rebuilding cannot quietly re-admit a sequence."""
    raw = service.ResidueMap(labels=["1", "2"], bases=["A", "C"])
    existing = {
        "evaluation_allowed": False,
        "exclusion_reason": "incomplete_sequence",
        "excluded_at": None,
    }
    row = service._gold_row_for("case-1", raw, None, existing)
    assert row["evaluation_allowed"] is False
    assert row["exclusion_reason"] == "incomplete_sequence"
    assert row["gold_source"] == "raw"
    assert row["gold_labels"] == ["1", "2"]
    # Re-including it is the only way back, and the answer was never in doubt.
    assert service._gold_row_for("case-1", raw, None, {**existing, "evaluation_allowed": True})[
        "evaluation_allowed"
    ] is True


def test_unsure_decision_keeps_the_reference_a_reviewer_was_shown():
    raw = service.ResidueMap(labels=["1", "2"], bases=["A", "C"])
    record = {
        "record_hash": "h3",
        "run_id": "run-7",
        "payload": {
            "status": "unsure",
            "review_round": "all-evaluation-mismatch-v1",
            "baseline_labels": ["1", "3"],
            "bases_by_residue": ["A", "C"],
        },
    }
    row = service._gold_row_for("case-1", raw, record)
    assert row["gold_source"] == "unsure"
    assert row["gold_labels"] == ["1", "3"]


def test_answer_matrix_expands_insertion_columns(tmp_path: Path, monkeypatch):
    seed = tmp_path / "schema.tsv"
    with seed.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "1", "2", "3"])
        writer.writerow(["plain", "A", "C", "G"])
    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", seed)
    rows = [
        {
            "seqname": "plain",
            "gold_labels": ["1", "2"],
            "gold_bases": ["A", "C"],
        },
        {
            "seqname": "insertion",
            "gold_labels": ["1", "2i1", "2"],
            "gold_bases": ["A", "G", "C"],
        },
    ]
    fieldnames, matrix = service.table_from_gold_rows(rows)
    columns = list(fieldnames[1:])
    assert columns[:4] == ["1", "2", "2i1", "3"]
    assert matrix["insertion"]["2i1"] == "G"
    assert matrix["plain"]["2i1"] == "-"


def test_rebuild_writes_one_row_per_sequence(tmp_path: Path, monkeypatch):
    gold_tsv = tmp_path / "gold.tsv"
    with gold_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "1", "2", "3"])
        writer.writerows(
            [
                ["plain", "A", "C", "G"],
                ["decided", "A", "C", "-"],
                ["dropped", "A", "-", "-"],
            ]
        )
    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", gold_tsv)
    monkeypatch.setattr(sequence_repository, "enabled", lambda: True)
    monkeypatch.setattr(sequence_repository, "gold_is_populated", lambda: False)
    monkeypatch.setattr(sequence_repository, "baseline_rows", lambda: {})
    # The rebuild keeps a manually set eligibility flag, so it reads the current
    # answer rows first; this queue has none.
    monkeypatch.setattr(sequence_repository, "gold_rows", lambda seqnames=None: {})
    monkeypatch.setattr(
        sequence_repository,
        "latest_formal_records",
        lambda: {
            "decided": {
                "record_hash": "h",
                "run_id": "run-1",
                "review_round": "",
                "status": "accepted_model",
                "payload": {
                    "status": "accepted_model",
                    "curated_labels": ["1", "3"],
                    "bases_by_residue": ["A", "G"],
                    "curated_at": "2026-09-14T10:00:00+0800",
                },
            },
            "dropped": {
                "record_hash": "h2",
                "run_id": "run-1",
                "review_round": "",
                "status": "excluded",
                "payload": {"status": "excluded"},
            },
        },
    )
    written: list[dict] = []

    class _Cursor:
        def execute(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return _Cursor()

        def commit(self):
            return None

    def capture(cursor, rows):
        written.extend(rows)

    monkeypatch.setattr(sequence_repository, "connection", lambda: _Conn())
    monkeypatch.setattr(sequence_repository, "put_gold_many", capture)

    stats = service.rebuild_gold_table()
    assert stats["rows"] == 3
    assert stats["by_source"] == {"adopted_model": 1, "excluded": 1, "raw": 1}
    assert stats["answer_differs_from_tRNAdb"] == 1
    decided = next(row for row in written if row["seqname"] == "decided")
    assert decided["gold_labels"] == ["1", "3"]
    assert decided["raw_labels"] == ["1", "2"]
    assert decided["decision_hash"] == "h"
    dropped = next(row for row in written if row["seqname"] == "dropped")
    assert dropped["evaluation_allowed"] is False
