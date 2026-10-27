from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agent.contracts import AlignmentRequest, CandidateAction, RootSnapshot
from backend.agent.evaluation import assess_variable_boundary_ambiguity
from backend.agent.evaluation.variable_boundary_reconciliation import (
    assess_variable_boundary_reference_agreement,
    reconcile_variable_boundary,
)
from backend.agent.execution import BranchExecutor
from backend.agent.orchestrator.service import AgentAlignmentService


def _long_arm_case() -> tuple[list[str], list[str], str]:
    variable = [
        "44",
        "V11",
        "V12",
        "V13",
        "V14",
        "V15",
        "V1",
        "V2",
        "V3",
        "V4",
        "V25",
        "V24",
        "V23",
        "V22",
        "V21",
        "46",
        "48",
    ]
    labels = ["43", *variable, "49", *[str(value) for value in range(50, 66)]]
    bases = ["A"] * len(labels)
    structure = ["."] * len(labels)
    for left, right in ((3, 14), (4, 13), (5, 12), (6, 11), (18, 34)):
        structure[left] = ">"
        structure[right] = "<"
        bases[left] = "G"
        bases[right] = "C"
    return labels, bases, "".join(structure)


def _snapshot(
    labels: list[str],
    bases: list[str],
    *,
    backend: str = "cmalign",
) -> RootSnapshot:
    return RootSnapshot(
        template_name="bact-num",
        csv_content="",
        numbering_row=list(labels),
        template_row=["-"] * len(labels),
        sequence_row=list(bases),
        structure_view={"id_pairs": []},
        raw_meta={
            "numbering_backend": backend,
            "cm_numbering": {
                "residues": [
                    {
                        "residue_index": index,
                        "label": label,
                        "base": base,
                    }
                    for index, (label, base) in enumerate(
                        zip(labels, bases),
                        start=1,
                    )
                ]
            },
        },
    )


def test_independent_structure_recovers_long_arm_and_position_47() -> None:
    labels, bases, structure = _long_arm_case()

    result = reconcile_variable_boundary(
        target_sequence="".join(bases),
        current_labels=labels,
        current_bases=bases,
        struct_file_sequence="".join(bases),
        secondary_structure=structure,
    )

    assert result["resolved"] is True
    assert result["mode"] == "long_variable_arm_from_independent_pairs"
    assert result["pair_count"] == 4
    assert result["canonical_pair_count"] == 4
    assert result["variable_labels_after"] == [
        "44",
        "45",
        "V11",
        "V12",
        "V13",
        "V14",
        "V1",
        "V2",
        "V3",
        "V4",
        "V24",
        "V23",
        "V22",
        "V21",
        "46",
        "47",
        "48",
    ]
    assert result["variable_loop_dialect_policy"].startswith("preserve_existing")
    assert result["answer_free"] is True
    assert result["gold_coordinates_used"] is False


@pytest.mark.parametrize(
    ("current_variable_labels", "expected_variable_labels"),
    [
        (["47"], ["44"]),
        (["45", "47"], ["44", "48"]),
        (["44", "45", "47"], ["44", "46", "48"]),
        (["44", "45", "46", "48"], ["44", "46", "47", "48"]),
        (
            ["44", "46", "47", "47i1", "48"],
            ["44", "45", "46", "47", "48"],
        ),
    ],
)
def test_unpaired_short_region_uses_revised_sprinzl_occupancy_order(
    current_variable_labels: list[str],
    expected_variable_labels: list[str],
) -> None:
    labels = [
        "43",
        *current_variable_labels,
        "49",
        *[str(value) for value in range(50, 66)],
    ]
    bases = ["A"] * len(labels)
    structure = ["."] * len(labels)
    index_49 = labels.index("49")
    structure[index_49] = ">"
    structure[-1] = "<"
    bases[index_49] = "G"
    bases[-1] = "C"

    result = reconcile_variable_boundary(
        target_sequence="".join(bases),
        current_labels=labels,
        current_bases=bases,
        struct_file_sequence="".join(bases),
        secondary_structure="".join(structure),
    )

    assert result["resolved"] is True
    assert result["variable_labels_after"] == expected_variable_labels


def test_reference_agreement_requires_exact_core_coordinates_not_v_offsets() -> None:
    proposed = ["43", "44", "45", "V11", "V12", "V1", "V22", "V21", "46", "47", "48", "49"]
    reference = ["43", "44", "45", "V13", "V14", "V2", "V24", "V23", "46", "47", "48", "49"]

    agreement = assess_variable_boundary_reference_agreement(
        proposed_labels=proposed,
        reference_labels=reference,
        variable_span_residue_indices=range(2, 12),
    )

    assert agreement["agrees"] is True
    assert agreement["standard_coordinate_checks"] == 5
    assert agreement["variable_arm_partition_checks"] == 5

    reference[9] = "48"
    disagreement = assess_variable_boundary_reference_agreement(
        proposed_labels=proposed,
        reference_labels=reference,
        variable_span_residue_indices=range(2, 12),
    )
    assert disagreement["agrees"] is False
    assert disagreement["disagreements"] == [
        {
            "residue_index": 10,
            "proposed_label": "47",
            "reference_label": "48",
            "comparison": "exact_standard_coordinate",
        }
    ]


def test_variable_boundary_reconciliation_requires_an_independent_t_stem_anchor() -> None:
    labels, bases, structure = _long_arm_case()
    structure = structure[:18] + "." + structure[19:-1] + "."

    result = reconcile_variable_boundary(
        target_sequence="".join(bases),
        current_labels=labels,
        current_bases=bases,
        struct_file_sequence="".join(bases),
        secondary_structure=structure,
    )

    assert result["resolved"] is False
    assert result["reason"] == (
        "independent_structure_does_not_confirm_49_65_t_stem_anchor"
    )


def test_variable_boundary_risk_is_answer_free_and_cm_specific() -> None:
    labels, bases, _structure = _long_arm_case()

    evidence = assess_variable_boundary_ambiguity(_snapshot(labels, bases))

    assert evidence is not None
    assert evidence["code"] == "cm_variable_boundary_structure_ambiguity"
    assert evidence["coordinate_assertion"] == "none"
    assert evidence["answer_free"] is True
    assert evidence["gold_coordinates_used"] is False
    assert (
        assess_variable_boundary_ambiguity(
            _snapshot(labels, bases, backend="template_regional_dp")
        )
        is None
    )


def test_variable_boundary_risk_starts_with_the_scoped_structure_action() -> None:
    work_state = SimpleNamespace(
        current_snapshot=RootSnapshot(
            template_name="bact-num",
            csv_content="",
            raw_meta={"numbering_backend": "cmalign"},
        )
    )

    actions = AgentAlignmentService._with_soft_risk_actions(
        [],
        work_state=work_state,
        evidence=[{"code": "cm_variable_boundary_structure_ambiguity"}],
    )

    assert [action.action_type for action in actions] == [
        "reconcile_variable_boundary_with_structure"
    ]
    assert actions[0].allowed_regions == ["Variable loop"]


def test_strong_variable_structure_evidence_can_precede_generic_score() -> None:
    labels, bases, _structure = _long_arm_case()
    snapshot = _snapshot(labels, bases)
    snapshot.raw_meta["variable_boundary_reconciliation"] = {
        "status": "resolved",
        "reference_consensus": {
            "established": True,
            "distinct_sequence_support": 2,
            "minimum_support": 2,
        },
        "result": {
            "resolved": True,
            "answer_free": True,
            "gold_coordinates_used": False,
            "t_stem_49_65_anchor_confirmed": True,
            "canonical_pair_count": 4,
            "pair_count": 4,
            "changed_residue_indices": [3, 17],
        },
    }
    action = CandidateAction(
        action_type="reconcile_variable_boundary_with_structure"
    )

    assert AgentAlignmentService._accept_variable_boundary_candidate(
        action=action,
        candidate_snapshot=snapshot,
        candidate_valid=True,
    )
    snapshot.raw_meta["variable_boundary_reconciliation"]["result"][
        "canonical_pair_count"
    ] = 2
    assert not AgentAlignmentService._accept_variable_boundary_candidate(
        action=action,
        candidate_snapshot=snapshot,
        candidate_valid=True,
    )


def test_branch_executor_applies_variable_projection_and_updates_provenance(
    monkeypatch,
) -> None:
    labels, bases, structure = _long_arm_case()
    parent = _snapshot(labels, bases)
    monkeypatch.setattr(
        "backend.agent.execution.branch_executor.run_trnascan_directly",
        lambda *_args, **_kwargs: SimpleNamespace(
            struct_file_sequence="".join(bases),
            secondary_structure=structure,
            type="Leu",
            anticodon="UAA",
        ),
    )
    action = CandidateAction(
        action_type="reconcile_variable_boundary_with_structure",
        scope="region",
        target_region="Variable loop",
        allowed_regions=["Variable loop"],
        success_condition="validated_independent_structure_projection",
        rollback_condition="retain_parent_on_failure",
    )

    candidate = BranchExecutor().execute(
        request=AlignmentRequest(target_seq="".join(bases)),
        parent_snapshot=parent,
        action=action,
        branch_id="variable-structure-test",
    )

    audit = candidate.raw_meta["variable_boundary_reconciliation"]
    assert audit["status"] == "structure_resolved_reference_pending"
    assert candidate.numbering_row[17] == "48"
    assert candidate.numbering_row[16] == "47"
    assert candidate.root_source == "variable_boundary_structure_reconciliation"
    residues = candidate.raw_meta["cm_numbering"]["residues"]
    assert [item["label"] for item in residues] == candidate.numbering_row


class _AlwaysValidGuards:
    @staticmethod
    def validate(_request, _snapshot):
        return SimpleNamespace(valid=True, reasons=[])


class _VariableConsensusExecutor:
    def __init__(
        self,
        *,
        parent: RootSnapshot,
        candidate_labels: list[str],
        support_count: int,
    ) -> None:
        self.parent = parent
        self.candidate_labels = candidate_labels
        self.support_count = support_count

    def execute(self, **kwargs):
        action = kwargs["action"]
        if action.action_type == "reconcile_variable_boundary_with_structure":
            return RootSnapshot(
                template_name="bact-num",
                csv_content="",
                numbering_row=list(self.candidate_labels),
                template_row=["-"] * len(self.candidate_labels),
                sequence_row=list(self.parent.sequence_row),
                raw_meta={
                    "numbering_backend": "cmalign",
                    "variable_boundary_reconciliation": {
                        "status": "structure_resolved_reference_pending",
                        "result": {
                            "resolved": True,
                            "mode": "long_variable_arm_from_independent_pairs",
                            "candidate_labels": list(self.candidate_labels),
                            "variable_span_residue_indices": list(range(2, 19)),
                            "changed_residue_indices": [3, 17],
                            "pair_count": 4,
                            "canonical_pair_count": 4,
                            "t_stem_49_65_anchor_confirmed": True,
                            "answer_free": True,
                            "gold_coordinates_used": False,
                        },
                    },
                },
            )

        requested = str(action.params.get("template_name") or "ref-1")
        rank = int(requested.rsplit("-", 1)[-1])
        labels = (
            list(self.candidate_labels)
            if rank <= self.support_count
            else list(self.parent.numbering_row)
        )
        template_base = {1: "C", 2: "G", 3: "U"}[rank]
        return RootSnapshot(
            template_name=requested,
            csv_content="",
            numbering_row=labels,
            template_row=[template_base] * len(labels),
            sequence_row=list(self.parent.sequence_row),
            template_candidates=(
                [{"seqname": "ref-2"}, {"seqname": "ref-3"}]
                if requested == "ref-1"
                else []
            ),
            raw_meta={"numbering_backend": "template_regional_dp"},
        )


def _run_variable_consensus(support_count: int) -> RootSnapshot:
    labels, bases, structure = _long_arm_case()
    parent = _snapshot(labels, bases)
    result = reconcile_variable_boundary(
        target_sequence="".join(bases),
        current_labels=labels,
        current_bases=bases,
        struct_file_sequence="".join(bases),
        secondary_structure=structure,
    )
    service = object.__new__(AgentAlignmentService)
    service.branch_executor = _VariableConsensusExecutor(
        parent=parent,
        candidate_labels=list(result["candidate_labels"]),
        support_count=support_count,
    )
    service.hard_guards = _AlwaysValidGuards()
    action = CandidateAction(
        action_id="variable-consensus",
        action_type="reconcile_variable_boundary_with_structure",
        scope="region",
        target_region="Variable loop",
        allowed_regions=["Variable loop"],
        success_condition="multi_evidence_consensus",
        rollback_condition="retain_parent",
    )
    return service._execute_variable_boundary_multi_evidence_search(
        request=AlignmentRequest(target_seq="".join(bases)),
        parent_snapshot=parent,
        action=action,
        branch_id="variable-consensus-candidate",
        work_state=SimpleNamespace(),
    )


def test_long_arm_requires_two_distinct_nonidentical_reference_sequences() -> None:
    candidate = _run_variable_consensus(support_count=2)

    audit = candidate.raw_meta["variable_boundary_reconciliation"]
    assert audit["status"] == "resolved"
    assert audit["reference_consensus"]["established"] is True
    assert audit["reference_consensus"]["distinct_sequence_support"] == 2
    assert candidate.numbering_row[16:18] == ["47", "48"]


def test_long_arm_retains_parent_when_reference_consensus_is_insufficient() -> None:
    candidate = _run_variable_consensus(support_count=1)

    audit = candidate.raw_meta["variable_boundary_reconciliation"]
    assert audit["status"] == "unresolved"
    assert audit["reference_consensus"]["established"] is False
    assert audit["result"]["reason"] == (
        "long_variable_arm_requires_reference_consensus"
    )
    assert candidate.numbering_row[16:18] == ["46", "48"]


@pytest.mark.parametrize(
    ("error_message", "expected_reason", "expected_tool_status"),
    [
        (
            "simulated reference failure",
            "variable_boundary_reference_search_failed",
            "failed",
        ),
        (
            "Annotation failed: sequence does not show a standard cloverleaf "
            "structure (empty regions: Variable loop)",
            "variable_boundary_reference_tool_not_applicable",
            "not_applicable",
        ),
    ],
)
def test_reference_search_exception_is_audited_and_retains_parent(
    error_message: str,
    expected_reason: str,
    expected_tool_status: str,
) -> None:
    labels, bases, structure = _long_arm_case()
    parent = _snapshot(labels, bases)
    projection = reconcile_variable_boundary(
        target_sequence="".join(bases),
        current_labels=labels,
        current_bases=bases,
        struct_file_sequence="".join(bases),
        secondary_structure=structure,
    )

    class _FailingReferenceExecutor:
        def execute(self, **kwargs):
            if kwargs["action"].action_type == "align_legacy_reference":
                raise ValueError(error_message)
            return RootSnapshot(
                template_name="bact-num",
                csv_content="",
                numbering_row=list(projection["candidate_labels"]),
                template_row=["-"] * len(bases),
                sequence_row=list(bases),
                raw_meta={
                    "numbering_backend": "cmalign",
                    "variable_boundary_reconciliation": {
                        "status": "structure_resolved_reference_pending",
                        "result": dict(projection),
                    },
                },
            )

    service = object.__new__(AgentAlignmentService)
    service.branch_executor = _FailingReferenceExecutor()
    service.hard_guards = _AlwaysValidGuards()
    candidate = service._execute_variable_boundary_multi_evidence_search(
        request=AlignmentRequest(target_seq="".join(bases)),
        parent_snapshot=parent,
        action=CandidateAction(
            action_id="variable-reference-failure",
            action_type="reconcile_variable_boundary_with_structure",
            scope="region",
            target_region="Variable loop",
            allowed_regions=["Variable loop"],
        ),
        branch_id="variable-reference-failure",
        work_state=SimpleNamespace(),
    )

    audit = candidate.raw_meta["variable_boundary_reconciliation"]
    assert candidate.numbering_row == parent.numbering_row
    assert audit["status"] == "unresolved"
    assert audit["result"]["reason"] == expected_reason
    assert audit["reference_consensus"]["tool_status"] == expected_tool_status
    assert audit["reference_consensus"]["tool_error_type"] == "ValueError"
