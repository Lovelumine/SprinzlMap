from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.domain.benchmarking.protocol import digest_text
from backend.domain.benchmarking.publication_protocol import (
    DEVELOPMENT_PARTITION,
    build_blind_review_tasks,
    build_development_records,
    normalize_external_candidates,
    summarize_development_records,
)


def source_record(
    seqname: str,
    sequence: str,
    *,
    curation_state: str,
    primary_reference_ready: bool,
    retrospective_split: str = "development",
) -> dict:
    return {
        "schema_version": "2.0",
        "representative_seqname": seqname,
        "sequence_sha256": digest_text(sequence),
        "template_exact_overlap": False,
        "curation_state": curation_state,
        "primary_reference_ready": primary_reference_ready,
        "retrospective_split": retrospective_split,
        "similarity_cluster": {
            "threshold": 0.95,
            "cluster_id": digest_text(sequence),
            "cluster_size": 1,
        },
        "metadata": {
            "broad_source": "bacteria",
            "amino_acid": "Ala",
            "anticodon": "UGC",
            "organism": "synthetic",
        },
    }


def test_development_records_score_database_references_and_preserve_evidence_tiers():
    records = build_development_records(
        [
            source_record(
                "reviewed",
                "A" * 40,
                curation_state="reviewed_reference",
                primary_reference_ready=True,
                retrospective_split="internal_test",
            ),
            source_record(
                "pending",
                "C" * 40,
                curation_state="review_required",
                primary_reference_ready=False,
                retrospective_split="calibration",
            ),
        ]
    )

    assert {row["benchmark_partition"] for row in records} == {
        DEVELOPMENT_PARTITION
    }
    by_name = {row["representative_seqname"]: row for row in records}
    assert by_name["reviewed"]["gold_evaluation_allowed"] is True
    assert by_name["reviewed"]["reference_evidence_tier"] == "project_expert_audited"
    assert by_name["reviewed"]["legacy_retrospective_split"] == "internal_test"
    assert by_name["pending"]["gold_evaluation_allowed"] is True
    assert by_name["pending"]["reference_evidence_tier"] == "trnadb_database_reference"
    assert by_name["pending"]["project_expert_audit_available"] is False
    assert by_name["pending"]["review_required"] is True
    assert 1 <= by_name["pending"]["development_fold"] <= 5
    summary = summarize_development_records(records)
    assert summary["scored_case_count"] == 2
    assert summary["expert_audited_case_count"] == 1
    assert summary["database_reference_only_case_count"] == 1


def test_blind_review_tasks_are_deterministic_and_hide_existing_labels():
    sequence = "ACGU" * 10
    records = build_development_records(
        [
            source_record(
                "pending",
                sequence,
                curation_state="blind_rereview_required",
                primary_reference_ready=False,
            )
        ]
    )

    tasks_a, keys_a = build_blind_review_tasks(
        records,
        {"pending": sequence},
        reviewer="A",
    )
    tasks_a_repeat, _ = build_blind_review_tasks(
        records,
        {"pending": sequence},
        reviewer="A",
    )
    tasks_b, _ = build_blind_review_tasks(
        records,
        {"pending": sequence},
        reviewer="B",
    )

    assert tasks_a == tasks_a_repeat
    assert tasks_a[0]["reviewer_task_id"] != tasks_b[0]["reviewer_task_id"]
    assert keys_a[0]["representative_seqname"] == "pending"
    forbidden = {
        "gold_labels",
        "sprinzl_labels",
        "model_output",
        "system_output",
        "review_reason",
    }
    assert forbidden.isdisjoint(tasks_a[0])


def test_external_candidates_require_provenance_quota_and_identity_audit():
    sequences = ["A" * 39 + "C", "G" * 39 + "U"]
    rows = [
        {
            "external_id": "canonical-1",
            "sequence": sequences[0],
            "test_stratum": "canonical_numbering",
            "source_database": "example",
            "source_release": "1",
            "source_accession": "A1",
        },
        {
            "external_id": "safety-1",
            "sequence": sequences[1],
            "test_stratum": "specialized_safety",
            "source_database": "example",
            "source_release": "1",
            "source_accession": "B1",
        },
    ]
    audits = {
        digest_text(sequence): {
            "nearest_template_identity": 0.9,
            "identity_metric": "one_minus_global_levenshtein_over_max_length",
        }
        for sequence in sequences
    }

    records = normalize_external_candidates(
        rows,
        development_sequence_digests=set(),
        template_sequence_digests=set(),
        template_identity_audit=audits,
        expected_targets={"canonical_numbering": 1, "specialized_safety": 1},
    )

    assert len(records) == 2
    assert all(row["gold_status"] == "sealed" for row in records)
    assert all(row["gold_evaluation_allowed"] is False for row in records)

    with pytest.raises(ValueError, match="overlaps development"):
        normalize_external_candidates(
            rows,
            development_sequence_digests={digest_text(sequences[0])},
            template_sequence_digests=set(),
            template_identity_audit=audits,
            expected_targets={"canonical_numbering": 1, "specialized_safety": 1},
        )

    invalid_rows = [dict(rows[0], sequence="A" * 39 + "X"), rows[1]]
    with pytest.raises(ValueError, match="invalid symbols"):
        normalize_external_candidates(
            invalid_rows,
            development_sequence_digests=set(),
            template_sequence_digests=set(),
            template_identity_audit=audits,
            expected_targets={"canonical_numbering": 1, "specialized_safety": 1},
        )
