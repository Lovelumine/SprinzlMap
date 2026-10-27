from __future__ import annotations

import pytest

from backend.api.routes.align_payloads import parse_align_request
from backend.api.routes.align_resources import _split_alignment_meta


def test_alignment_input_contract_preserves_unknown_defaults():
    payload = parse_align_request({"target_seq": "ATGC", "use_llm": False})

    assert payload.input_type == "unknown"
    assert payload.domain_hint == "unknown"
    assert payload.cca_status == "unknown"
    assert payload.intron_status == "unknown"
    assert payload.trna_class == "unknown"
    assert payload.mitochondrial_subtype == "unknown"
    assert payload.enable_root_recovery is False


def test_alignment_input_contract_accepts_explicit_biological_provenance():
    payload = parse_align_request(
        {
            "target_seq": "AUGC",
            "input_type": "mature",
            "domain_hint": "archaea",
            "cca_status": "input_encoded",
            "intron_status": "removed",
            "trna_class": "selenocysteine",
            "mitochondrial_subtype": "vertebrate",
            "enable_root_recovery": True,
        }
    )

    assert payload.input_type == "mature"
    assert payload.domain_hint == "archaea"
    assert payload.cca_status == "input_encoded"
    assert payload.intron_status == "removed"
    assert payload.trna_class == "selenocysteine"
    assert payload.mitochondrial_subtype == "vertebrate"
    assert payload.enable_root_recovery is True


def test_alignment_input_contract_rejects_unversioned_clade_claim():
    with pytest.raises(ValueError, match="domain_hint"):
        parse_align_request({"target_seq": "AUGC", "domain_hint": "mammalia_clade"})


def test_alignment_api_exposes_same_scientific_provenance_object():
    cm_numbering = {"residues": [{"residue_index": 1, "label": "1", "base": "G"}]}
    boundary_contract = {
        "resolver": "deterministic_multi_evidence_search",
        "llm_used": False,
    }
    _, _, _, scientific = _split_alignment_meta(
        {
            "numbering_backend": "cmalign",
            "numbering_model": {"name": "arch-num", "sha256": "abc"},
            "cm_score": {"bit_score": 72.7},
            "cm_portfolio": {"score_margin_calibrated": False},
            "cm_numbering": cm_numbering,
            "cm_tool": {"tool_provenance": {"version": "INFERNAL test"}},
            "boundary_refinement_contract": boundary_contract,
        }
    )

    assert scientific["numbering_backend"] == "cmalign"
    assert scientific["numbering_model"]["name"] == "arch-num"
    assert scientific["cm_portfolio"]["score_margin_calibrated"] is False
    assert scientific["cm_numbering"] == cm_numbering
    assert scientific["cm_tool"]["tool_provenance"]["version"] == "INFERNAL test"
    assert scientific["boundary_refinement_contract"] == boundary_contract
