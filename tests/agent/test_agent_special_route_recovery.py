from __future__ import annotations

import shutil

import pytest

from backend.agent import AgentAlignmentService, AlignmentRequest
from backend.domain.alignment.special_routes import (
    SpecialRouteEvidence,
    SpecialRouteEvidenceService,
)


ARCH_SEQUENCE = "GGGCTCATCGCTCAGCGGTAGAGTGCCTCCTTTGCAAGGAGGATGCCCTGGGTTCGAATCCCAGTGAGTCCA"


class _FailingRootBuilder:
    def build(self, request):
        raise ValueError("legacy root could not establish a cloverleaf")


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_cm_primary_does_not_build_legacy_root_for_canonical_mature_input():
    response = AgentAlignmentService(root_builder=_FailingRootBuilder()).align_with_meta(
        AlignmentRequest(
            target_seq=ARCH_SEQUENCE,
            use_llm=False,
            input_type="mature",
            domain_hint="archaea",
            intron_status="absent",
            runtime_values_override={"AGENT_CANONICAL_ROOT_STRATEGY": "cm_first"},
        )
    )

    assert response.template_name == "arch-num"
    assert response.meta["agent_v2"]["best_source_action"] == "align_numbering_cm"
    assert response.meta["agent_v2"]["hard_guard_passed"] is True
    assert "legacy_root_failure" not in response.meta


def test_primary_and_fallback_tool_failures_return_structured_abstention():
    response = AgentAlignmentService(root_builder=_FailingRootBuilder()).align_with_meta(
        AlignmentRequest(
            target_seq=ARCH_SEQUENCE,
            use_llm=False,
            input_type="mature",
            domain_hint="archaea",
            runtime_values_override={
                "CMALIGN_EXECUTABLE": "/missing/cmalign",
                "AGENT_ENABLE_LEGACY_FALLBACK": True,
                "AGENT_CANONICAL_ROOT_STRATEGY": "cm_first",
            },
        )
    )

    stages = [
        event["stage"] for event in response.meta["agent_v2"]["trace_events"]
    ]
    assert response.csv_content == ""
    assert response.meta["agent_v2"]["work_state_summary"]["disposition"] == "abstained"
    assert "primary_tool_failed" in stages
    assert "fallback_tool_failed" in stages


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_explicit_mature_scope_can_recover_after_legacy_root_failure():
    response = AgentAlignmentService(root_builder=_FailingRootBuilder()).align_with_meta(
        AlignmentRequest(
            target_seq=ARCH_SEQUENCE,
            use_llm=False,
            input_type="mature",
            domain_hint="archaea",
            intron_status="absent",
            enable_root_recovery=True,
            runtime_values_override={"AGENT_CANONICAL_ROOT_STRATEGY": "legacy_only"},
        )
    )

    agent = response.meta["agent_v2"]
    assert response.template_name == "arch-num"
    assert agent["hard_guard_passed"] is True
    assert agent["best_source_action"] == "align_numbering_cm"
    assert response.meta["legacy_root_failure"]["preserved"] is True
    stages = [event["stage"] for event in agent["trace_events"]]
    assert "initial_tool_failed" in stages
    assert "root_recovery_tool_called" in stages


def test_root_recovery_abstains_when_mature_scope_is_not_explicit():
    response = AgentAlignmentService(root_builder=_FailingRootBuilder()).align_with_meta(
        AlignmentRequest(
            target_seq=ARCH_SEQUENCE,
            use_llm=False,
            input_type="unknown",
            enable_root_recovery=True,
        )
    )

    assert response.csv_content == ""
    assert response.meta["agent_v2"]["hard_guard_passed"] is False
    assert response.meta["agent_v2"]["work_state_summary"]["disposition"] == "abstained"
    assert "root_recovery_requires_input_type_mature" in response.warnings


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_mitochondrial_source_can_be_numbered_with_canonical_like_scope_warning():
    response = AgentAlignmentService().align_with_meta(
        AlignmentRequest(
            target_seq=ARCH_SEQUENCE,
            use_llm=False,
            input_type="mature",
            domain_hint="mitochondrial",
            mitochondrial_subtype="unknown",
        )
    )

    special = response.meta["specialized_route"]
    assert response.csv_content
    assert response.template_name == "all-num"
    assert response.meta["cm_tool"]["selection_mode"] == "explicit_model_group"
    assert response.meta["agent_strategy"]["selected_model_group"] == "all"
    assert response.meta["agent_strategy"]["domain_selector_used"] is False
    assert special["route"] == "mitochondrial"
    assert special["evidence_disposition"] == "subtype_required"
    assert special["disposition"] == "accepted_with_scope_warning"
    assert special["canonical_like_structure"]["qualified"] is True
    assert response.meta["agent_v2"]["route"] == "mitochondrial"
    assert response.meta["agent_v2"]["work_state_summary"]["disposition"] == "accepted"
    assert "accepted_with_scope_warning" in response.warnings


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_virus_source_can_be_numbered_with_canonical_like_scope_warning():
    response = AgentAlignmentService().align_with_meta(
        AlignmentRequest(
            target_seq=ARCH_SEQUENCE,
            use_llm=False,
            input_type="mature",
            domain_hint="virus",
        )
    )

    special = response.meta["specialized_route"]
    assert response.csv_content
    assert response.template_name == "all-num"
    assert response.meta["cm_tool"]["selection_mode"] == "explicit_model_group"
    assert response.meta["agent_strategy"]["selected_model_group"] == "all"
    assert response.meta["agent_strategy"]["domain_selector_used"] is False
    assert special["route"] == "virus"
    assert special["evidence_disposition"] == "validated_numbering_model_unavailable"
    assert special["disposition"] == "accepted_with_scope_warning"
    assert response.meta["agent_v2"]["work_state_summary"]["disposition"] == "accepted"


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_intron_route_numbers_only_derived_mature_sequence_with_raw_mapping():
    raw = ARCH_SEQUENCE[:36] + "AAAAAA" + ARCH_SEQUENCE[36:]
    retained = [index for index in range(1, len(raw) + 1) if not (37 <= index <= 42)]
    mapping = [
        {
            "mature_residue_index": mature_index,
            "raw_residue_index": raw_index,
            "base": raw[raw_index - 1],
        }
        for mature_index, raw_index in enumerate(retained, start=1)
    ]
    evidence = SpecialRouteEvidence(
        route="archaeal_ncintron",
        status="mature_sequence_ready",
        disposition="ready_for_canonical_numbering",
        message="test evidence",
        canonical_numbering_eligible=True,
        mature_sequence=ARCH_SEQUENCE,
        raw_to_mature=mapping,
        evidence={"removed_intron_interval": {"start": 37, "end": 42}},
    )

    class _EvidenceService:
        def run_if_applicable(self, request):
            return evidence

    response = AgentAlignmentService(special_route_service=_EvidenceService()).align_with_meta(
        AlignmentRequest(
            target_seq=raw,
            use_llm=False,
            input_type="pre_trna",
            domain_hint="archaea",
            intron_status="present",
        )
    )

    assert response.template_name == "arch-num"
    assert response.meta["agent_v2"]["hard_guard_passed"] is True
    assert response.meta["agent_v2"]["route"] == "archaeal_ncintron"
    assert response.meta["input_transform"]["mature_sequence"] == ARCH_SEQUENCE
    assert response.meta["input_transform"]["removed_interval"] == {"start": 37, "end": 42}


@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("tRNAscan-SE", "cmsearch", "cmalign")),
    reason="tRNAscan-SE and Infernal tools are required",
)
def test_unknown_archaeal_pre_trna_uses_real_tools_to_detect_and_remove_intron():
    raw = ARCH_SEQUENCE[:36] + "AAAAAA" + ARCH_SEQUENCE[36:]

    response = AgentAlignmentService().align_with_meta(
        AlignmentRequest(
            target_seq=raw,
            use_llm=False,
            input_type="pre_trna",
            domain_hint="archaea",
            intron_status="unknown",
        )
    )

    transform = response.meta["input_transform"]
    assert response.meta["agent_v2"]["hard_guard_passed"] is True
    assert response.meta["agent_v2"]["work_state_summary"]["disposition"] == "accepted"
    assert transform["type"] == "extract_tRNA_body_and_remove_intron"
    assert transform["mature_sequence"] == ARCH_SEQUENCE.replace("T", "U")
    assert transform["removed_interval"] == {"start": 37, "end": 42}
    assert len(transform["raw_to_mature"]) == len(ARCH_SEQUENCE)


def test_trnascan_and_cmsearch_parsers_keep_scientific_coordinates():
    trnascan = "query 1 1 72 Ala TGC 37 42 73.4 Ala 99.0\n"
    cmsearch = "query - arch-SeC-050516 - cm 1 89 2 90 + no 1 0.50 0.0 80.4 1e-20 ! desc\n"

    hits = SpecialRouteEvidenceService._parse_trnascan_results(trnascan)
    cm_hits = SpecialRouteEvidenceService._parse_cmsearch_table(cmsearch)

    assert hits[0]["intron_begin"] == 37
    assert hits[0]["intron_end"] == 42
    assert hits[0]["anticodon"] == "UGC"
    assert cm_hits[0]["sequence_from"] == 2
    assert cm_hits[0]["score"] == pytest.approx(80.4)
