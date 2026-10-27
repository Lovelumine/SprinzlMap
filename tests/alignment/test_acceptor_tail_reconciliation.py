from __future__ import annotations

from types import SimpleNamespace

from backend.agent.contracts import AlignmentRequest, CandidateAction, RootSnapshot
from backend.agent.evaluation.acceptor_tail_reconciliation import (
    assess_acceptor_tail_ambiguity,
    reconcile_acceptor_tail,
)
from backend.agent.orchestrator.service import AgentAlignmentService


HIS_TARGET = (
    "GGCGAUUGUGGCGAAGUGGUUAACGCACCUGAUUGUGGAUCAGGCAUUCGUGGGUUCAAUUCCCAUCAGUCGCCCA"
)
HIS_STRUCTURE_SEQUENCE = HIS_TARGET[1:-2]
HIS_STRUCTURE = (
    ">>>>>>>..>>>..........<<<.>>>>>.......<<<<<.....>>>>>.......<<<<<<<<<<<<."
)
SIX_PAIR_TARGET = (
    "CGUGGGGUGGAGCAAUUGGUAGCUCGUCGGGCUCAUAACCCGAAGGUUGCAGGUUCAAGUCCUGUCCCCACCA"
)
SIX_PAIR_STRUCTURE = (
    ".>>>>>>..>>>>........<<<<.>>>>>.......<<<<<.....>>>>>.......<<<<<<<<<<<.."
)


def _histidine_parent_labels() -> list[str]:
    labels = [f"x{index}" for index in range(1, len(HIS_TARGET) + 1)]
    labels[:8] = ["1", "2", "3", "4", "5", "6", "6i1", "7"]
    labels[-14:] = [
        "62",
        "63",
        "64",
        "65",
        "66",
        "66i1",
        "67",
        "68",
        "69",
        "70",
        "71",
        "72",
        "72i1",
        "73",
    ]
    return labels


def test_reconcile_acceptor_tail_flags_non_cca_suffix_without_assigning_tail_coordinates() -> None:
    labels = _histidine_parent_labels()

    result = reconcile_acceptor_tail(
        target_sequence=HIS_TARGET,
        current_labels=labels,
        current_bases=list(HIS_TARGET),
        struct_file_sequence=HIS_STRUCTURE_SEQUENCE,
        secondary_structure=HIS_STRUCTURE,
        trna_type="His",
    )

    assert result["resolved"] is False
    assert result["reason"] == (
        "non_cca_terminal_extension_not_mappable_to_74_76"
    )
    assert result["terminal_coordinate_labels"] == ["73"]
    assert result["terminal_extension_sequence"] == "CA"
    assert result["terminal_motif_observation"] == "non_cca_extension"
    assert result["discriminator_residue_index"] == 74
    assert result["terminal_extension_sequence"] == "CA"
    assert result["mature_cca_claim"] == "not_asserted"
    assert result["answer_free"] is True
    assert result["gold_coordinates_used"] is False


def test_reconcile_acceptor_tail_maps_single_structural_prefix_to_minus_1() -> None:
    result = reconcile_acceptor_tail(
        target_sequence=HIS_TARGET,
        current_labels=_histidine_parent_labels(),
        current_bases=list(HIS_TARGET),
        struct_file_sequence=HIS_STRUCTURE_SEQUENCE,
        secondary_structure=HIS_STRUCTURE,
        trna_type="Ser",
    )

    assert result["resolved"] is False
    assert result["reason"] == (
        "non_cca_terminal_extension_not_mappable_to_74_76"
    )
    assert result["terminal_coordinate_labels"] == ["73"]
    assert result["terminal_extension_sequence"] == "CA"


def test_reconcile_acceptor_tail_restores_unmodeled_terminal_suffix() -> None:
    target = (
        "GGGUAGGUACUCAAGUGGCCAACGAGGGCAGACUGUAAAUCUGCUGACUAUGUCUUCGAAGGUUCGAAUCCUUCCCUGCCCACC"
    )
    structure_sequence = target[:-2]
    structure = ">>>>>>>..>>>...........<<<.>>>>>.......<<<<<.>>>...<<<...>>>>>.......<<<<<<<<<<<<."
    labels = [f"x{index}" for index in range(1, len(target) + 1)]
    labels[:7] = [str(index) for index in range(1, 8)]
    labels[-10:] = ["66", "67", "68", "69", "70", "71", "72", "72i1", "72i2", "73"]

    result = reconcile_acceptor_tail(
        target_sequence=target,
        current_labels=labels,
        current_bases=list(target),
        struct_file_sequence=structure_sequence,
        secondary_structure=structure,
        trna_type="Tyr",
    )

    assert result["resolved"] is False
    assert result["independent_structure_terminal_suffix_appended"] is True
    assert result["independent_structure_terminal_suffix"] == "CC"
    assert result["reason"] == "incomplete_terminal_cca_not_mappable_to_74_76"
    assert result["terminal_coordinate_labels"] == ["73"]
    assert result["terminal_motif_observation"] == "incomplete_terminal"
    assert result["mature_cca_claim"] == "not_asserted"


def test_acceptor_detector_prefers_fresh_annotation_over_stale_agent_context() -> None:
    target = (
        "GGGUAGGUACUCAAGUGGCCAACGAGGGCAGACUGUAAAUCUGCUGACUAUGUCUUCGAAGGUUCGAAUCCUUCCCUGCCCACC"
    )
    labels = [f"x{index}" for index in range(1, len(target) + 1)]
    labels[:7] = [str(index) for index in range(1, 8)]
    labels[-10:] = ["66", "67", "68", "69", "70", "71", "72", "72i1", "72i2", "73"]
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        numbering_row=labels,
        template_row=["-"] * len(labels),
        sequence_row=list(target),
        raw_meta={
            "numbering_backend": "cmalign",
            "agent_ann_meta": {
                "struct_file_sequence": target,
                "secondary_structure": "." * len(target),
            },
            "annotation_meta": {
                "struct_file_sequence": target[:-2],
                "secondary_structure": ">>>>>>>..>>>...........<<<.>>>>>.......<<<<<.>>>...<<<...>>>>>.......<<<<<<<<<<<<.",
                "trnascan_isotype": "Tyr",
            },
        },
    )

    evidence = assess_acceptor_tail_ambiguity(snapshot)

    assert evidence is not None
    assert evidence["trigger_basis"] == "narrow_acceptor_tail_label_pattern"


def test_acceptor_tail_detector_uses_structure_disagreement_without_gold() -> None:
    labels = _histidine_parent_labels()
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        numbering_row=labels,
        template_row=["-"] * len(labels),
        sequence_row=list(HIS_TARGET),
        raw_meta={
            "numbering_backend": "cmalign",
            "annotation_meta": {
                "struct_file_sequence": HIS_STRUCTURE_SEQUENCE,
                "secondary_structure": HIS_STRUCTURE,
                "trnascan_isotype": "His",
            },
        },
    )

    evidence = assess_acceptor_tail_ambiguity(snapshot)

    assert evidence is not None
    assert evidence["code"] == "cm_acceptor_tail_boundary_ambiguity"
    assert evidence["trigger_basis"] == "narrow_acceptor_tail_label_pattern"
    assert evidence["coordinate_assertion"] == "none"


def test_acceptor_tail_detector_flags_unmapped_terminal_cc_prefix() -> None:
    target = "GACCGGCC"
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        numbering_row=["1", "2", "3", "4", "5", "6", "7", "73"],
        template_row=["-"] * len(target),
        sequence_row=list(target),
        raw_meta={"numbering_backend": "cmalign"},
    )

    evidence = assess_acceptor_tail_ambiguity(snapshot)

    assert evidence is not None
    assert evidence["trigger_basis"] == (
        "terminal_cca_prefix_not_mapped_to_74_75"
    )


def test_reconcile_acceptor_tail_accepts_mirrored_seven_residue_arm_with_mismatch() -> None:
    labels = [f"x{index}" for index in range(1, len(SIX_PAIR_TARGET) + 1)]
    labels[-8:] = ["66", "67", "68", "69", "70", "71", "71i1", "72"]

    result = reconcile_acceptor_tail(
        target_sequence=SIX_PAIR_TARGET,
        current_labels=labels,
        current_bases=list(SIX_PAIR_TARGET),
        struct_file_sequence=SIX_PAIR_TARGET,
        secondary_structure=SIX_PAIR_STRUCTURE,
        trna_type="Met",
    )

    assert result["resolved"] is True
    assert result["pair_count"] == 6
    assert result["candidate_labels"][:7] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
    ]
    assert result["candidate_labels"][-8:] == [
        "66",
        "67",
        "68",
        "69",
        "70",
        "71",
        "72",
        "73",
    ]


def test_acceptor_tail_risk_starts_with_the_scoped_structure_action() -> None:
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        raw_meta={"numbering_backend": "cmalign"},
    )
    work_state = SimpleNamespace(current_snapshot=snapshot)

    actions = AgentAlignmentService._with_soft_risk_actions(
        [],
        work_state=work_state,
        evidence=[{"code": "cm_acceptor_tail_boundary_ambiguity"}],
    )

    assert [action.action_type for action in actions] == [
        "repair_acceptor_tail_with_structure"
    ]
    assert actions[0].allowed_regions == [
        "Aminoacyl arm 5' end",
        "T loop + T stem",
        "Aminoacyl arm 3' end",
    ]


def test_acceptor_tail_review_precedes_remaining_local_risks() -> None:
    evidence = [
        {"code": "cm_average_posterior_below_threshold"},
        {"code": "cm_local_45_48_insertion_deletion_ambiguity"},
        {"code": "cm_acceptor_tail_boundary_ambiguity"},
    ]

    first = AgentAlignmentService._next_soft_risk_review_batch(
        evidence,
        reviewed_codes=set(),
    )
    second = AgentAlignmentService._next_soft_risk_review_batch(
        evidence,
        reviewed_codes={"cm_acceptor_tail_boundary_ambiguity"},
    )

    assert [item["code"] for item in first] == [
        "cm_acceptor_tail_boundary_ambiguity"
    ]
    assert [item["code"] for item in second] == [
        "cm_average_posterior_below_threshold",
        "cm_local_45_48_insertion_deletion_ambiguity",
    ]


def test_acceptor_tail_review_is_exposed_when_another_hard_conflict_exists() -> None:
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        numbering_row=_histidine_parent_labels(),
        sequence_row=list(HIS_TARGET),
        raw_meta={
            "numbering_backend": "cmalign",
            "annotation_meta": {
                "struct_file_sequence": HIS_STRUCTURE_SEQUENCE,
                "secondary_structure": HIS_STRUCTURE,
                "trnascan_isotype": "His",
            },
        },
    )
    request = AlignmentRequest(
        target_seq=HIS_TARGET,
        use_llm=True,
        runtime_values_override={"AGENT_LLM_TRIAGE_MODE": "dual_trigger"},
    )
    work_state = SimpleNamespace(
        current_snapshot=snapshot,
        current_valid=False,
        issues=["variable_singleton_occupied:V15"],
        current_route=SimpleNamespace(out_of_scope=False),
    )

    evidence = AgentAlignmentService._soft_risk_evidence(request, work_state)

    assert [item["code"] for item in evidence] == [
        "cm_acceptor_tail_boundary_ambiguity"
    ]


def test_ambiguous_endpoint_opens_template_consensus_fallback() -> None:
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        raw_meta={"numbering_backend": "cmalign"},
    )
    actions = AgentAlignmentService._with_soft_risk_actions(
        [],
        work_state=SimpleNamespace(
            current_snapshot=snapshot,
            current_valid=False,
        ),
        evidence=[
            {
                "code": "cm_acceptor_tail_boundary_ambiguity",
                "structural_check_reason": (
                    "acceptor_pairs_do_not_define_one_mirrored_seven_residue_arm"
                ),
            }
        ],
    )

    assert [action.action_type for action in actions] == [
        "reconcile_acceptor_tail_with_template_consensus"
    ]


def test_ambiguous_structure_observation_is_available_after_candidate_rollback() -> None:
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        numbering_row=["1", "2", "71", "72", "72i1", "73"],
        template_row=list("GCAUCG"),
        sequence_row=list("GCAUCG"),
        raw_meta={
            "numbering_backend": "cmalign",
            "acceptor_tail_reconciliation": {
                "status": "unresolved",
                "result": {
                    "resolved": False,
                    "reason": (
                        "acceptor_pairs_do_not_define_one_mirrored_"
                        "seven_residue_arm"
                    ),
                    "candidate_right_arm_starts": [64, 65],
                },
            },
        },
    )

    evidence = assess_acceptor_tail_ambiguity(snapshot)

    assert evidence is not None
    assert evidence["endpoint_is_ambiguous"] is True
    assert evidence["candidate_right_arm_starts"] == [64, 65]
    assert evidence["trigger_basis"] == (
        "retained_independent_structure_observation"
    )


def test_missing_structure_opens_structure_endpoint_tool_first() -> None:
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        raw_meta={"numbering_backend": "cmalign"},
    )
    actions = AgentAlignmentService._with_soft_risk_actions(
        [],
        work_state=SimpleNamespace(
            current_snapshot=snapshot,
            current_valid=False,
        ),
        evidence=[
            {
                "code": "cm_acceptor_tail_boundary_ambiguity",
                "structural_check_reason": (
                    "independent_structure_sequence_and_shape_length_mismatch"
                ),
            }
        ],
    )

    assert [action.action_type for action in actions] == [
        "repair_acceptor_tail_with_structure"
    ]


def test_template_consensus_requires_distinct_sequence_support() -> None:
    action = CandidateAction(
        action_type="reconcile_acceptor_tail_with_template_consensus"
    )
    snapshot = RootSnapshot(
        template_name="arch-num",
        csv_content="",
        raw_meta={
            "acceptor_tail_template_consensus": {
                "status": "resolved",
                "established": True,
                "answer_free": True,
                "gold_coordinates_used": False,
                "minimum_support": 3,
                "distinct_sequence_support": 3,
                "consensus_signature": ["66", "67", "69", "70", "71", "72", "73"],
                "selected_changed_residue_indices": [70, 71, 72],
            }
        },
    )

    assert AgentAlignmentService._accept_acceptor_template_consensus_candidate(
        action=action,
        candidate_snapshot=snapshot,
        candidate_valid=True,
    )


def test_strong_valid_endpoint_evidence_can_precede_generic_score() -> None:
    action = CandidateAction(action_type="repair_acceptor_tail_with_structure")
    snapshot = RootSnapshot(
        template_name="bact-num",
        csv_content="",
        raw_meta={
            "acceptor_tail_reconciliation": {
                "status": "resolved",
                "result": {
                    "resolved": True,
                    "answer_free": True,
                    "gold_coordinates_used": False,
                    "left_arm_reconciled": True,
                    "canonical_pair_count": 6,
                    "pair_count": 6,
                    "tail_length": 1,
                    "changed_residue_indices": [1, 75],
                },
            }
        },
    )

    assert AgentAlignmentService._accept_acceptor_endpoint_candidate(
        action=action,
        candidate_snapshot=snapshot,
        candidate_valid=True,
    )
    snapshot.raw_meta["acceptor_tail_reconciliation"]["result"][
        "canonical_pair_count"
    ] = 4
    assert not AgentAlignmentService._accept_acceptor_endpoint_candidate(
        action=action,
        candidate_snapshot=snapshot,
        candidate_valid=True,
    )
