from __future__ import annotations

import shutil
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agent import AgentAlignmentService, AlignmentRequest, RootSnapshot
from backend.agent.contracts import CandidateAction
from backend.agent.evaluation.hard_guards import HardGuards
from backend.agent.execution import BranchExecutor
from backend.agent.tools.cm_numbering_tool import CMNumberingTool
from backend.agent.orchestrator.context_engineering import SpecialistContext
from backend.agent.orchestrator.specialists import CMNumberingSpecialist
from backend.domain.alignment.safety.validation import validate_alignment
from backend.domain.alignment.cm_numbering import NumberedResidue


ARCH_SEQUENCE = "GGGCTCATCGCTCAGCGGTAGAGTGCCTCCTTTGCAAGGAGGATGCCCTGGGTTCGAATCCCAGTGAGTCCA"
VARIABLE_TOPOLOGY_SEQUENCE = (
    "GUAGUCGUGGCCGAGUGGUUAAGGCGAUGGUCUUCAAAGCUAUAGGGGCUCCGCCUCACGGGUUCGAAUCCCGUCGACGACG"
)


def test_cm_specialist_requires_explicit_flag_and_canonical_scope():
    specialist = CMNumberingSpecialist()
    disabled = SpecialistContext(
        target_length=72,
        anticodon_hint="",
        use_llm=False,
        llm_reasoning=False,
        selected_template="legacy",
        cm_tool_enabled=False,
    )
    enabled = SpecialistContext(
        target_length=72,
        anticodon_hint="",
        use_llm=False,
        llm_reasoning=False,
        selected_template="legacy",
        cm_tool_enabled=True,
        domain_hint="archaea",
        input_type="mature",
    )
    out_of_scope = SpecialistContext(
        target_length=72,
        anticodon_hint="",
        use_llm=False,
        llm_reasoning=False,
        selected_template="legacy",
        cm_tool_enabled=True,
        canonical_eligible=False,
    )
    pre_trna = SpecialistContext(
        target_length=90,
        anticodon_hint="",
        use_llm=False,
        llm_reasoning=False,
        selected_template="legacy",
        cm_tool_enabled=True,
        input_type="pre_trna",
        intron_status="present",
    )

    assert specialist.propose(disabled) == []
    assert specialist.propose(out_of_scope) == []
    assert specialist.propose(pre_trna) == []
    actions = specialist.propose(enabled)
    assert len(actions) == 1
    assert actions[0].action_type == "align_numbering_cm"
    assert actions[0].params["selection_mode"] == "portfolio"
    assert actions[0].budget_cost.llm_calls_reserved == 0


def test_planning_can_be_enabled_without_enabling_llm():
    request = AlignmentRequest(
        target_seq="ACU",
        use_llm=False,
        runtime_values_override={"AGENT_ENABLE_PLANNING": True},
    )

    assert AgentAlignmentService._planning_enabled(request) is True
    assert AgentAlignmentService._planning_enabled(
        AlignmentRequest(target_seq="ACU", use_llm=False)
    ) is False
    assert AgentAlignmentService._planning_enabled(
        AlignmentRequest(target_seq="ACU", use_llm=False, input_type="mature")
    ) is False


def test_base_preservation_normalizes_dna_t_to_rna_u_without_hiding_deletions():
    assert validate_alignment("ATGCN", ["A", "U", "G", "C", "N"])
    assert not validate_alignment("ATGCN", ["A", "U", "G", "C"])


def test_cm_moves_non_cca_terminal_slots_to_73_insertions():
    result = SimpleNamespace(
        residues=[
            NumberedResidue(1, "A", "72", "standard", 1),
            NumberedResidue(2, "G", "73", "standard", 2),
            NumberedResidue(3, "C", "74", "standard", 3),
            NumberedResidue(4, "C", "75", "standard", 4),
        ],
        structure_pairs=[],
        warnings=[],
        trace_events=[],
        portfolio_evidence={},
    )

    CMNumberingTool._reassign_non_cca_terminal_extensions(result, "AGCC")

    assert [residue.label for residue in result.residues] == [
        "72", "73", "73i1", "73i2"
    ]
    assert [residue.base for residue in result.residues] == ["A", "G", "C", "C"]
    assert "non_cca_terminal_extension_reassigned_after_73" in result.warnings


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_cm_action_builds_full_snapshot_and_passes_shared_hard_guards():
    parent = RootSnapshot(
        template_name="legacy",
        csv_content="1\nG\nG\n",
        numbering_row=["1"],
        template_row=["G"],
        sequence_row=["G"],
        raw_meta={"annotation_meta": {"trnascan_hit": True}},
    )
    request = AlignmentRequest(
        target_seq=ARCH_SEQUENCE,
        use_llm=False,
        input_type="mature",
        domain_hint="archaea",
    )
    action = CandidateAction(
        action_type="align_numbering_cm",
        action_id="cm-explicit-arch",
        specialist="cm_numbering_specialist",
        params={"model_group": "arch"},
        allowed_regions=["Aminoacyl arm"],
        success_condition="base_preserving_cm_candidate",
        rollback_condition="shared_hard_guard_failure",
    )

    candidate = BranchExecutor().execute(
        request=request,
        parent_snapshot=parent,
        action=action,
        branch_id="step-cm",
    )

    assert parent.template_name == "legacy"
    assert parent.numbering_row == ["1"]
    assert candidate.template_name == "arch-num"
    assert candidate.source_action == "align_numbering_cm"
    assert candidate.parent_snapshot_id == "root"
    assert "".join(candidate.sequence_row) == ARCH_SEQUENCE.replace("T", "U")
    assert candidate.raw_meta["numbering_backend"] == "cmalign"
    assert candidate.raw_meta["cm_tool"]["input_contract"]["input_type"] == "mature"
    assert candidate.raw_meta["cm_tool"]["input_contract"]["alphabet"] == "DNA"
    assert "INFERNAL" in candidate.raw_meta["cm_tool"]["tool_provenance"]["version"]
    assert candidate.raw_meta["cm_tool"]["tool_provenance"]["models"]["arch"]["sha256"]
    assert len(candidate.raw_meta["cm_numbering"]["residues"]) == len(ARCH_SEQUENCE)
    assert HardGuards().validate(request, candidate).valid is True


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_agent_harness_uses_cm_as_primary_root_without_llm():
    response = AgentAlignmentService().align_with_meta(
        AlignmentRequest(
            target_seq=VARIABLE_TOPOLOGY_SEQUENCE,
            use_llm=False,
            return_meta=True,
            input_type="mature",
            runtime_values_override={
                "AGENT_ENABLE_PLANNING": True,
                "AGENT_ENABLE_CM_TOOL": True,
                "AGENT_CANONICAL_ROOT_STRATEGY": "cm_first",
                "AGENT_MAX_ACTION_STEPS": 2,
            },
        )
    )

    agent = response.meta["agent_v2"]
    primary_events = [
        event
        for event in agent["trace_events"]
        if event.get("stage") == "canonical_route_selected"
    ]
    assert agent["expanded"] is False
    assert agent["best_source_action"] == "align_numbering_cm"
    assert agent["hard_guard_passed"] is True
    assert agent["work_state_summary"]["disposition"] == "accepted"
    assert len(primary_events) == 1
    assert primary_events[0]["strategy"] == "cm_first"
    assert primary_events[0]["tool"] == "align_numbering_cm"
    assert primary_events[0]["planner_source"] == "deterministic_ablation"
    assert response.meta["numbering_backend"] == "cmalign"
    assert response.meta["agent_strategy"]["strategy"] == "cm_first"
    assert response.meta["structural_region_evidence"]["region_residue_counts"]
    assert response.meta["cm_portfolio"]["score_margin_calibrated"] is False
    assert len(response.meta["cm_numbering"]["residues"]) == len(VARIABLE_TOPOLOGY_SEQUENCE)


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_unaccepted_cm_root_keeps_unknown_terminal_cca_under_review():
    response = AgentAlignmentService().align_with_meta(
        AlignmentRequest(
            target_seq=ARCH_SEQUENCE,
            use_llm=False,
            return_meta=True,
            input_type="mature",
            runtime_values_override={
                "AGENT_ENABLE_PLANNING": True,
                "AGENT_CANONICAL_ROOT_STRATEGY": "cm_first",
                "AGENT_MAX_ACTION_STEPS": 2,
                "CM_CALIBRATION_ARTIFACT": "/tmp/missing-cm-calibration.json",
            },
        )
    )

    agent = response.meta["agent_v2"]
    action_events = [
        event for event in agent["trace_events"] if event.get("stage") == "action_executed"
    ]
    assert agent["expanded"] is True
    # A terminal CCA with unknown maturity/provenance cannot be promoted to
    # 74-76 merely because the legacy route also observes those characters.
    assert agent["best_source_action"] == "align_numbering_cm"
    assert agent["hard_guard_passed"] is False
    assert agent["work_state_summary"]["disposition"] == "investigate"
    assert action_events[0]["action_type"] == "align_legacy_reference"
    assert action_events[0]["tool_name"] == "align_legacy_reference"
    assert action_events[0]["disposition"] == "rejected"
    # Rejected candidates stay in the trace rather than contaminating the
    # final snapshot metadata.
    assert action_events[0]["tool_provenance"]["parent_cm_model"]["name"] == "arch-num"
