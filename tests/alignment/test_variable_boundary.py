from __future__ import annotations

import pytest

from backend.agent.contracts import AlignmentRequest, RootSnapshot
from backend.agent.evaluation.hard_guards import (
    _validate_variable_bridge_convention,
)
from backend.domain.alignment.variable_boundary import (
    assess_variable_bridge,
    canonical_bridge_labels,
    preferred_standard_variable_labels,
)


@pytest.mark.parametrize(
    ("residue_count", "expected"),
    [
        (0, ()),
        (1, ("48",)),
        (2, ("46", "48")),
        (3, ("46", "47", "48")),
        (4, None),
    ],
)
def test_canonical_bridge_labels_follow_sprinzl_occupancy_rule(
    residue_count: int,
    expected: tuple[str, ...] | None,
):
    assert canonical_bridge_labels(residue_count) == expected


@pytest.mark.parametrize(
    ("residue_count", "expected"),
    [
        (1, ("44",)),
        (2, ("44", "48")),
        (3, ("44", "46", "48")),
        (4, ("44", "46", "47", "48")),
        (5, ("44", "45", "46", "47", "48")),
    ],
)
def test_standard_variable_allocator_uses_revised_sprinzl_occupancy_order(
    residue_count: int,
    expected: tuple[str, ...],
):
    labels = ["44", "45", "46", "47", "48"]

    assert preferred_standard_variable_labels(residue_count, labels) == expected


def test_long_variable_arm_uses_the_last_v_label_as_the_bridge_anchor():
    labels = ["44", "45", "V11", "V1", "V21", "46", "48", "49", "65"]
    bases = ["A", "U", "G", "C", "U", "A", "G", "C", "G"]

    evidence = assess_variable_bridge(labels, bases)

    assert evidence["status"] == "conforming"
    assert evidence["left_anchor_label"] == "V21"
    assert evidence["observed_labels"] == ["46", "48"]
    assert evidence["expected_labels"] == ["46", "48"]


def test_position_47_without_position_48_is_a_bridge_conflict():
    labels = ["44", "45", "46", "47", "49", "65"]
    bases = ["A", "U", "G", "C", "A", "G"]

    evidence = assess_variable_bridge(labels, bases)

    assert evidence["status"] == "conflict"
    assert evidence["observed_labels"] == ["46", "47"]
    assert evidence["expected_labels"] == ["46", "48"]
    assert evidence["answer_free"] is True
    assert evidence["gold_coordinates_used"] is False
    assert evidence["rule"] == "sprinzl_1993_variable_bridge_48_46_47"


def test_insertion_state_is_counted_as_an_observed_bridge_residue():
    labels = ["44", "45", "45i1", "46", "48", "49", "65"]
    bases = ["A", "U", "G", "C", "A", "U", "G"]

    evidence = assess_variable_bridge(labels, bases)

    assert evidence["status"] == "conflict"
    assert evidence["observed_labels"] == ["45i1", "46", "48"]
    assert evidence["expected_labels"] == ["46", "47", "48"]
    assert evidence["contains_insertion_state"] is True


def test_bridge_assessment_requires_the_49_65_t_stem_anchor():
    evidence = assess_variable_bridge(
        ["44", "45", "46", "47", "49"],
        ["A", "U", "G", "C", "A"],
    )

    assert evidence["status"] == "insufficient_evidence"
    assert evidence["reason"] == "t_stem_49_65_anchor_missing"


def _snapshot(labels: list[str], bases: list[str]) -> RootSnapshot:
    return RootSnapshot(
        template_name="test",
        csv_content="",
        numbering_row=labels,
        template_row=["-"] * len(labels),
        sequence_row=bases,
    )


def test_hard_guard_rejects_a_noncanonical_classic_bridge():
    request = AlignmentRequest(target_seq="AUGCAG", domain_hint="bacteria")
    snapshot = _snapshot(
        ["44", "45", "46", "47", "49", "65"],
        ["A", "U", "G", "C", "A", "G"],
    )

    reasons = _validate_variable_bridge_convention(request, snapshot)

    assert reasons == [
        "variable_bridge_sprinzl_convention_violation:"
        "observed=46,47:expected=46,48"
    ]


def test_hard_guard_leaves_mitochondrial_bridge_for_specialized_review():
    request = AlignmentRequest(target_seq="AUGCAG", domain_hint="mitochondrial")
    snapshot = _snapshot(
        ["44", "45", "46", "47", "49", "65"],
        ["A", "U", "G", "C", "A", "G"],
    )

    assert _validate_variable_bridge_convention(request, snapshot) == []


def test_hard_guard_requires_structure_review_for_more_than_three_bridge_residues():
    request = AlignmentRequest(target_seq="AUGCAGU", domain_hint="bacteria")
    snapshot = _snapshot(
        ["44", "45", "45i1", "46", "47", "48", "49", "65"],
        ["A", "U", "G", "C", "A", "G", "U", "C"],
    )

    reasons = _validate_variable_bridge_convention(request, snapshot)

    assert reasons == [
        "variable_bridge_structure_review_required:"
        "observed=45i1,46,47,48:expected=(empty)"
    ]
