from __future__ import annotations

from backend.domain.benchmarking.submission_protocol import (
    GoldLabelRecord,
    apply_template_identity_audit,
    build_submission_records,
    cluster_sequences,
    global_identity,
    group_templates_by_length,
    nearest_template_match,
)


def test_global_identity_uses_global_edit_distance():
    assert global_identity("A" * 20, "A" * 19 + "G") == 0.95
    assert global_identity("A" * 20, "A" * 18 + "GG", threshold=0.95) == 0.0


def test_similarity_clusters_are_transitive_and_deterministic():
    sequences = {
        "a": "A" * 20,
        "b": "A" * 19 + "G",
        "c": "A" * 18 + "GG",
        "d": "C" * 20,
    }

    clusters = cluster_sequences(sequences, identity_threshold=0.95)

    assert clusters["a"] == clusters["b"] == clusters["c"]
    assert clusters["d"] != clusters["a"]


def test_nearest_template_match_uses_exact_normalized_global_distance():
    templates = group_templates_by_length(
        {
            "far": "C" * 20,
            "near": "A" * 19 + "G",
            "length-shift": "A" * 19,
        }
    )

    match = nearest_template_match("A" * 20, templates)

    assert match.identity == 0.95
    assert match.edit_distance == 1
    assert match.template_sequence_sha256 == "length-shift"


def test_identity_audit_requires_exact_cohort_coverage():
    records = [{"sequence_sha256": "a"}, {"sequence_sha256": "b"}]
    audit = {
        "a": {
            "nearest_template_identity": 0.9,
            "edit_distance": 2,
            "template_length": 20,
            "template_sequence_sha256": "template-a",
        }
    }

    try:
        apply_template_identity_audit(records, audit)
    except ValueError as exc:
        assert "missing=1" in str(exc)
    else:
        raise AssertionError("partial identity audit was accepted")


def test_submission_records_collapse_duplicates_and_queue_model_gold():
    cases = [
        {
            "seqname": "case-a",
            "eligible": True,
            "template_exact_overlap": False,
            "sequence_sha256": "seq-a",
            "correction_decision": "accepted_model",
            "metadata": {"broad_source": "bacteria"},
        },
        {
            "seqname": "case-b",
            "eligible": True,
            "template_exact_overlap": False,
            "sequence_sha256": "seq-a",
            "correction_decision": "no_manual_correction",
            "metadata": {"broad_source": "bacteria"},
        },
        {
            "seqname": "visible",
            "eligible": True,
            "template_exact_overlap": True,
            "sequence_sha256": "seq-visible",
            "correction_decision": "accepted_gold",
            "metadata": {"broad_source": "bacteria"},
        },
    ]
    gold = {
        "case-a": GoldLabelRecord("case-a", "A" * 20, "seq-a", "labels-a"),
        "case-b": GoldLabelRecord("case-b", "A" * 20, "seq-a", "labels-a"),
        "visible": GoldLabelRecord("visible", "C" * 20, "seq-visible", "labels-v"),
    }

    records = build_submission_records(cases, gold, seed="test")

    assert len(records) == 1
    assert records[0]["source_record_count"] == 2
    assert records[0]["curation_state"] == "blind_rereview_required"
    assert records[0]["primary_reference_ready"] is False


def test_similarity_cluster_cannot_cross_splits_when_sources_differ():
    cases = [
        {
            "seqname": "case-a",
            "eligible": True,
            "template_exact_overlap": False,
            "sequence_sha256": "seq-a",
            "correction_decision": "accepted_gold",
            "metadata": {"broad_source": "bacteria"},
        },
        {
            "seqname": "case-b",
            "eligible": True,
            "template_exact_overlap": False,
            "sequence_sha256": "seq-b",
            "correction_decision": "accepted_gold",
            "metadata": {"broad_source": "archaea"},
        },
    ]
    gold = {
        "case-a": GoldLabelRecord("case-a", "A" * 20, "seq-a", "labels-a"),
        "case-b": GoldLabelRecord(
            "case-b",
            "A" * 19 + "G",
            "seq-b",
            "labels-b",
        ),
    }

    records = build_submission_records(cases, gold, seed="test")

    assert records[0]["similarity_cluster"]["cluster_id"] == records[1][
        "similarity_cluster"
    ]["cluster_id"]
    assert records[0]["retrospective_split"] == records[1]["retrospective_split"]
