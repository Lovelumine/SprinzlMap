from __future__ import annotations

from backend.agent.contracts import AlignmentRequest, CandidateAction, RootSnapshot
from backend.agent.evaluation import (
    assess_local_coordinate_ambiguity,
    candidate_local_resolution,
)
from backend.agent.orchestrator import AgentAlignmentService
from backend.agent.execution import BranchExecutor


def _snapshot(labels: list[str], bases: list[str], *, backend: str = "cmalign") -> RootSnapshot:
    return RootSnapshot(
        template_name="test",
        csv_content="",
        numbering_row=list(labels),
        template_row=["-"] * len(labels),
        sequence_row=list(bases),
        raw_meta={"numbering_backend": backend},
    )


def test_local_detector_records_a_noncanonical_variable_bridge():
    snapshot = _snapshot(
        ["44", "45", "45i1", "46", "48", "49", "65"],
        ["U", "A", "U", "G", "C", "A", "G"],
    )

    evidence = assess_local_coordinate_ambiguity(snapshot)

    assert evidence is not None
    assert evidence["code"] == "cm_variable_bridge_coordinate_ambiguity"
    assert evidence["residue_indices"] == [3, 4, 5]
    assert evidence["observed_labels"] == ["45i1", "46", "48"]
    assert evidence["reference_candidate_labels"] == ["46", "47", "48"]


def test_local_detector_does_not_force_a_genuine_missing_47():
    missing_standard = _snapshot(
        ["44", "45", "46", "48", "49", "65"],
        ["U", "A", "G", "C", "A", "G"],
    )
    already_standard = _snapshot(
        ["44", "45", "46", "47", "48", "49", "65"],
        ["U", "A", "U", "G", "C", "A", "G"],
    )

    assert assess_local_coordinate_ambiguity(missing_standard) is None
    assert assess_local_coordinate_ambiguity(already_standard) is None


def test_local_detector_does_not_treat_long_arm_insertions_as_bridge_residues():
    snapshot = _snapshot(
        ["44", "45", "V22", "V22i1", "46", "48", "49", "65"],
        ["U", "A", "C", "U", "G", "C", "A", "G"],
    )

    assert assess_local_coordinate_ambiguity(snapshot) is None


def test_bridge_resolution_requires_the_same_input_residues():
    parent = _snapshot(
        ["44", "45", "46", "47", "49", "65"],
        ["U", "A", "C", "U", "G", "C"],
    )
    evidence = assess_local_coordinate_ambiguity(parent)
    assert evidence is not None

    canonical_assignment = _snapshot(
        ["44", "45", "46", "48", "49", "65"],
        ["U", "A", "C", "U", "G", "C"],
        backend="template_regional_dp",
    )
    reversed_assignment = _snapshot(
        ["44", "45", "48", "46", "49", "65"],
        ["U", "A", "C", "U", "G", "C"],
        backend="template_regional_dp",
    )

    canonical = candidate_local_resolution(parent, canonical_assignment, evidence)
    reversed_result = candidate_local_resolution(parent, reversed_assignment, evidence)

    assert canonical["resolved"] is True
    assert reversed_result["resolved"] is False
    assert canonical["resolution_key"] == "sprinzl_bridge_at_residues:3,4=46,48"


def test_local_resolution_requires_the_same_residues_and_standard_window():
    parent = _snapshot(
        ["44", "45", "45i1", "46", "48", "49", "65"],
        ["U", "A", "U", "G", "C", "A", "G"],
    )
    evidence = assess_local_coordinate_ambiguity(parent)
    assert evidence is not None

    resolved = _snapshot(
        ["44", "45", "46", "47", "48", "49", "65"],
        ["U", "A", "U", "G", "C", "A", "G"],
        backend="template_regional_dp",
    )
    changed_base = _snapshot(
        ["44", "45", "46", "47", "48", "49", "65"],
        ["U", "A", "C", "G", "C", "A", "G"],
        backend="template_regional_dp",
    )

    assert candidate_local_resolution(parent, resolved, evidence)["resolved"] is True
    assert candidate_local_resolution(parent, changed_base, evidence)["resolved"] is False


def test_local_consensus_uses_a_lower_replacement_margin():
    parent = _snapshot(
        ["44", "45", "45i1", "46", "48", "49", "65"],
        ["U", "A", "U", "G", "C", "A", "G"],
    )
    candidate = _snapshot(
        ["44", "45", "46", "47", "48", "49", "65"],
        ["U", "A", "U", "G", "C", "A", "G"],
        backend="template_regional_dp",
    )
    candidate.raw_meta["multi_template_reference_search"] = {
        "consensus_established": True,
        "selected_local_resolution": {
            "resolved": True,
            "base_preserved": True,
        },
    }
    action = CandidateAction(
        action_type="align_legacy_reference",
        params={
            "soft_risk_reason_codes": [
                "cm_variable_bridge_coordinate_ambiguity"
            ]
        },
    )

    gain = AgentAlignmentService._candidate_minimum_score_gain(
        request=AlignmentRequest(target_seq="UUAGCA"),
        parent_snapshot=parent,
        candidate_snapshot=candidate,
        action=action,
    )

    assert gain == 0.1


def test_reference_action_passes_requested_template_search_depth():
    captured = {}

    class _RootBuilder:
        def build(self, request):
            captured.update(request.runtime_values_override)
            return _snapshot(
                ["44", "45", "46", "47", "48"],
                ["U", "A", "U", "G", "C"],
                backend="template_regional_dp",
            )

    executor = BranchExecutor(root_builder=_RootBuilder())
    parent = _snapshot(
        ["44", "45", "45i1", "46", "48"],
        ["U", "A", "U", "G", "C"],
    )
    action = CandidateAction(
        action_type="align_legacy_reference",
        params={"template_top_k": 10},
    )

    executor.execute(
        request=AlignmentRequest(target_seq="UAUGC"),
        parent_snapshot=parent,
        action=action,
        branch_id="test-reference-depth",
    )

    assert captured["AGENT_TEMPLATE_TOP_K"] == 10
