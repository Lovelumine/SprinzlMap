from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import backend.application.alignment.scientific_interpretation as interpretation_module

from backend.application.alignment.scientific_interpretation import (
    ScientificInterpretationError,
    _messages,
    _validate_interpretation,
    build_interpretation_facts,
    generate_scientific_interpretation,
    normalize_interpretation_language,
)


def _variable_insertion_facts():
    return build_interpretation_facts(
        sequence="A" * 74,
        labels=["43", "44", "45", "45i1", "46", "47", "48", "49", "65"],
        bases=["A"] * 9,
        structure={
            "annotation_source": "tRNAscan-SE",
            "id_pairs": [{"left_id": "49", "right_id": "65"}],
        },
        agent={"route": "canonical", "hard_guard_passed": True},
        scientific_provenance={},
        warnings=[],
        record={"id": "example"},
    )


def test_variable_insertion_requires_review_without_variable_arm_pairing():
    facts = _variable_insertion_facts()

    assert facts["system_disposition"]["code"] == "review_required"
    codes = {item["code"] for item in facts["evidence"]}
    assert "variable_bridge_requires_review" in codes
    assert "alignment_insertions_present" in codes
    assert "no_paired_extra_variable_arm" in codes


def test_interpretation_rejects_coordinate_not_present_in_candidate():
    facts = _variable_insertion_facts()
    evidence_ids = [item["id"] for item in facts["evidence"]]
    payload = {
        "sequence_assessment": {
            "text": "The candidate requires local review.",
            "evidence_ids": [evidence_ids[0]],
        },
        "structural_basis": [
            {
                "text": "A labelled structure annotation is available.",
                "evidence_ids": [evidence_ids[2]],
            }
        ],
        "attention_regions": [
            {
                "region": "variable region",
                "title": "Variable-region insertion",
                "text": "The candidate contains an alignment-extension label.",
                "coordinate_labels": ["V2"],
                "evidence_ids": [evidence_ids[-2]],
            }
        ],
    }

    with pytest.raises(ScientificInterpretationError, match="unsupported coordinate"):
        _validate_interpretation(payload, facts)


def test_interpretation_rejects_unsupported_coordinate_claim_in_prose():
    facts = _variable_insertion_facts()
    evidence_ids = [item["id"] for item in facts["evidence"]]
    payload = {
        "sequence_assessment": {
            "text": "The candidate contains an insertion at position 53i1.",
            "evidence_ids": [evidence_ids[0]],
        },
        "structural_basis": [
            {
                "text": "A labelled structure annotation is available.",
                "evidence_ids": [evidence_ids[2]],
            }
        ],
        "attention_regions": [
            {
                "region": "variable region",
                "title": "Insertion after coordinate 45",
                "text": "Residue 4 is labelled 45i1 between occupied coordinates 45 and 46.",
                "coordinate_labels": ["45", "45i1", "46"],
                "evidence_ids": [evidence_ids[-2]],
            }
        ],
    }

    with pytest.raises(ScientificInterpretationError, match="unsupported coordinates in prose"):
        _validate_interpretation(payload, facts)


def test_interpretation_accepts_candidate_coordinates_in_prose():
    facts = _variable_insertion_facts()
    evidence_ids = [item["id"] for item in facts["evidence"]]
    payload = {
        "sequence_assessment": {
            "text": "The candidate contains an insertion at position 45i1.",
            "evidence_ids": [evidence_ids[0]],
        },
        "structural_basis": [
            {
                "text": "Anticodon coordinates 43-45 are represented in the candidate.",
                "evidence_ids": [evidence_ids[2]],
            }
        ],
        "attention_regions": [
            {
                "region": "variable region",
                "title": "Insertion after coordinate 45",
                "text": "Residue 4 is labelled 45i1 between occupied coordinates 45 and 46.",
                "coordinate_labels": ["45", "45i1", "46"],
                "evidence_ids": [evidence_ids[-2]],
            }
        ],
    }

    result = _validate_interpretation(payload, facts)

    assert result["sequence_assessment"]["text"].endswith("45i1.")


def test_structure_evidence_contains_explicit_coordinate_pairs():
    facts = _variable_insertion_facts()
    structure = next(
        item for item in facts["evidence"] if item["code"] == "structure_pair_links"
    )

    assert structure["data"]["pairs"] == [
        {
            "left_label": "49",
            "left_base": "A",
            "left_residue_index": 8,
            "right_label": "65",
            "right_base": "A",
            "right_residue_index": 9,
        }
    ]


def test_insertion_evidence_names_base_and_neighboring_anchors():
    facts = _variable_insertion_facts()
    insertion = next(
        item for item in facts["evidence"] if item["code"] == "alignment_insertions_present"
    )

    assert insertion["data"]["coordinates"] == [
        {
            "label": "45i1",
            "base": "A",
            "residue_index": 4,
            "parent_label": "45",
            "previous_anchor": "45",
            "next_anchor": "46",
        }
    ]


def test_interpretation_language_is_validated_and_sent_to_prompt():
    facts = _variable_insertion_facts()

    assert normalize_interpretation_language("zh") == "zh-CN"
    with pytest.raises(ValueError, match="language"):
        normalize_interpretation_language("es")

    messages = _messages(facts, "de")
    assert "scientific German" in messages[0]["content"]
    prompt = json.loads(messages[1]["content"])
    assert prompt["constraints"]["language_code"] == "de"
    assert "45i1" in prompt["constraints"]["required_attention_coordinate_labels"]


@pytest.mark.parametrize(
    "text, message",
    [
        ("Evidence E3 supports this mapping.", "evidence IDs"),
        ("The V-series coordinates form a class II tRNA.", "unsupported tRNA class"),
        ("The mean posterior indicates high-confidence support.", "confidence adjective"),
        ("The terminus contains a discriminator base and CCA tail.", "without CCA evidence"),
    ],
)
def test_interpretation_rejects_unsupported_narrative_claims(text, message):
    facts = _variable_insertion_facts()
    evidence_ids = [item["id"] for item in facts["evidence"]]
    payload = {
        "sequence_assessment": {"text": text, "evidence_ids": [evidence_ids[0]]},
        "structural_basis": [
            {"text": "A labelled structure annotation is available.", "evidence_ids": [evidence_ids[5]]}
        ],
        "attention_regions": [
            {
                "region": "variable region",
                "title": "Insertion after coordinate 45",
                "text": "Residue 4 is labelled 45i1 between occupied coordinates 45 and 46.",
                "coordinate_labels": ["45", "45i1", "46"],
                "evidence_ids": [evidence_ids[-2]],
            }
        ],
    }

    with pytest.raises(ScientificInterpretationError, match=message):
        _validate_interpretation(payload, facts)


def test_interpretation_cache_is_isolated_by_language(monkeypatch, tmp_path):
    calls: list[list[dict[str, str]]] = []

    class FakeClient:
        def complete(self, *, messages, **_kwargs):
            calls.append(messages)
            return SimpleNamespace(
                provider="mimo",
                model="test-model",
                payload={
                    "sequence_assessment": {
                        "text": "The three-residue candidate is mapped.",
                        "evidence_ids": ["E1"],
                    },
                    "structural_basis": [
                        {
                            "text": "The submitted sequence contains three nucleotides.",
                            "evidence_ids": ["E1"],
                        }
                    ],
                    "attention_regions": [],
                },
            )

    monkeypatch.setattr(interpretation_module, "StructuredJsonLLMClient", FakeClient)
    monkeypatch.setattr(interpretation_module, "CACHE_PATH", tmp_path / "cache.jsonl")
    monkeypatch.setattr(interpretation_module, "_CACHE", {})
    monkeypatch.setattr(interpretation_module, "_CACHE_LOADED", False)
    kwargs = {
        "sequence": "ACG",
        "labels": ["1", "2", "3"],
        "bases": ["A", "C", "G"],
        "agent": {"hard_guard_passed": True},
    }

    english = generate_scientific_interpretation(**kwargs, language="en")
    german = generate_scientific_interpretation(**kwargs, language="de")
    english_cached = generate_scientific_interpretation(**kwargs, language="en")

    assert len(calls) == 2
    assert english["provenance"]["language"] == "en"
    assert german["provenance"]["language"] == "de"
    assert english_cached["cached"] is True


def test_literature_questions_follow_reviewed_regions_and_the_terminal_signal():
    facts = _variable_insertion_facts()
    query = interpretation_module._literature_evidence(
        facts["evidence"],
        agent={"route": "canonical"},
        scientific_provenance={"trnascan": {"domain_hint": "bacteria"}},
        record={"id": "example"},
    )

    # The recorded conflicts sit between the last variable anchor and coordinate 49,
    # and the insertion extends 45, so only the variable region should be asked about.
    assert query["conflict_regions"] == ["variable_loop"]
    # A sequence that does not end in CCA is where the 74-76 question is genuinely open.
    assert query["terminal_cca"] == "absent"


def test_reference_list_is_built_once_and_survives_the_cache(monkeypatch, tmp_path):
    class FakeClient:
        def complete(self, *, messages, **_kwargs):
            return SimpleNamespace(
                provider="mimo",
                model="test-model",
                payload={
                    "sequence_assessment": {
                        "text": "The three-residue candidate is mapped.",
                        "evidence_ids": ["E1"],
                    },
                    "structural_basis": [
                        {
                            "text": "The submitted sequence contains three nucleotides.",
                            "evidence_ids": ["E1"],
                        }
                    ],
                    "attention_regions": [],
                },
            )

    lookups: list[dict[str, object]] = []

    def fake_collect(query):
        lookups.append(query)
        return (
            [{"citation_id": "L1", "source": "local_pdf", "title": "Sprinzl numbering", "year": "1980"}],
            "102:index-build-1",
        )

    monkeypatch.setattr(interpretation_module, "StructuredJsonLLMClient", FakeClient)
    monkeypatch.setattr(interpretation_module, "CACHE_PATH", tmp_path / "cache.jsonl")
    monkeypatch.setattr(interpretation_module, "_CACHE", {})
    monkeypatch.setattr(interpretation_module, "_CACHE_LOADED", False)
    monkeypatch.setattr(interpretation_module, "_collect_citations", fake_collect)
    monkeypatch.setattr(interpretation_module, "_corpus_stamp", lambda: "102:index-build-1")
    kwargs = {
        "sequence": "ACG",
        "labels": ["1", "2", "3"],
        "bases": ["A", "C", "G"],
        "agent": {"hard_guard_passed": True},
    }

    generated = interpretation_module.generate_scientific_interpretation(**kwargs, language="en")
    cached = interpretation_module.generate_scientific_interpretation(**kwargs, language="en")

    assert [item["citation_id"] for item in generated["citations"]] == ["L1"]
    assert cached["cached"] is True
    assert [item["citation_id"] for item in cached["citations"]] == ["L1"]
    assert len(lookups) == 1

    # A rebuilt literature index refreshes the list without regenerating the narrative.
    interpretation_module._CACHE[
        next(iter(interpretation_module._CACHE))
    ]["citations_corpus"] = "103:index-build-2"
    refreshed = interpretation_module.generate_scientific_interpretation(**kwargs, language="en")
    assert refreshed["cached"] is True
    assert len(lookups) == 2
