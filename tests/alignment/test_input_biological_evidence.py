from __future__ import annotations

from backend.agent import AlignmentRequest, RootSnapshot
from backend.agent.evaluation.hard_guards import HardGuards
from backend.domain.alignment.input_evidence import assess_input_evidence
from backend.domain.alignment.special_routes.evidence import SpecialRouteEvidenceService
from backend.domain.alignment.special_routes import SpecialRouteEvidence


def _snapshot(
    labels: list[str],
    sequence: str,
    *,
    raw_meta: dict | None = None,
) -> RootSnapshot:
    return RootSnapshot(
        template_name="test",
        csv_content="",
        numbering_row=labels,
        template_row=list(sequence),
        sequence_row=list(sequence),
        raw_meta=dict(raw_meta or {}),
    )


def test_terminal_cca_observation_is_not_treated_as_provenance_for_unknown_input():
    evidence = assess_input_evidence(
        target_seq="GGGCCA",
        input_type="unknown",
        cca_status="unknown",
    )

    assert evidence["sequence_observations"]["terminal_cca_observed"] is True
    assert evidence["cca_evidence"]["coordinate_assignment_allowed"] is False
    assert evidence["cca_evidence"]["genomic_or_post_transcriptional_origin"] == "unknown"


def test_mature_terminal_cca_is_not_projected_without_confirmed_provenance():
    evidence = assess_input_evidence(
        target_seq="GGGCCA",
        input_type="mature",
        cca_status="unknown",
    )

    assert evidence["cca_evidence"]["coordinate_assignment_allowed"] is False
    assert evidence["cca_evidence"]["coordinate_assignment_basis"] == (
        "terminal_motif_observed_but_provenance_unconfirmed"
    )
    assert evidence["cca_evidence"]["genomic_or_post_transcriptional_origin"] == "unknown"


def test_explicit_input_cca_claim_requires_an_observed_terminal_cca():
    evidence = assess_input_evidence(
        target_seq="GGGAAA",
        input_type="mature",
        cca_status="input_encoded",
    )

    assert evidence["claim_conflicts"] == [
        "cca_claim_input_encoded_but_terminal_cca_missing"
    ]


def test_hard_guard_rejects_cca_projection_for_unknown_input_contract():
    request = AlignmentRequest(
        target_seq="GCCA",
        input_type="unknown",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(["1", "74", "75", "76"], "GCCA"),
    )

    assert result.valid is False
    assert "cca_74_76_assignment_not_supported_by_input_evidence" in result.reasons


def test_unknown_input_can_use_cm_structure_evidence_for_terminal_cca():
    request = AlignmentRequest(
        target_seq="GACCA",
        input_type="unknown",
        cca_status="unknown",
        use_llm=True,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "73", "74", "75", "76"],
            "GACCA",
            raw_meta={
                "numbering_backend": "cmalign",
                "cm_score": {"bit_score": 42.0},
            },
        ),
    )

    assert result.valid is True


def test_unknown_input_rejects_out_of_domain_cm_forcing_as_tRNA_evidence():
    request = AlignmentRequest(
        target_seq="UGGGCUGCCUC",
        input_type="unknown",
        use_llm=True,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["16", "18", "19", "20", "21", "25", "26", "27", "28", "44", "46"],
            "UGGGCUGCCUC",
            raw_meta={
                "numbering_backend": "cmalign",
                "cm_score": {"bit_score": -91.4, "average_posterior": 0.762},
                "cm_portfolio": {
                    "selection_policy": "calibrated_domain_selector",
                    "calibration": {"disposition": "accepted"},
                },
            },
        ),
    )

    assert result.valid is False
    assert "cm_unknown_input_nonpositive_homology_score" in result.reasons
    assert (
        "cm_unknown_input_core_scaffold_incomplete:acceptor,anticodon,t_stem"
        in result.reasons
    )


def test_unknown_input_accepts_cm_with_positive_score_and_core_scaffold():
    request = AlignmentRequest(
        target_seq="AAAAAA",
        input_type="unknown",
        use_llm=True,
    )
    snapshot = _snapshot(
        ["1", "27", "43", "49", "65", "72"],
        "AAAAAA",
        raw_meta={
            "numbering_backend": "cmalign",
            "cm_score": {"bit_score": 42.0, "average_posterior": 0.99},
            "cm_portfolio": {
                "selection_policy": "calibrated_domain_selector",
                "calibration": {"disposition": "accepted"},
            },
        },
    )
    snapshot.structure_view = {
        "id_pairs": [
            {"left_id": "1", "right_id": "72"},
            {"left_id": "27", "right_id": "43"},
            {"left_id": "49", "right_id": "65"},
        ]
    }

    result = HardGuards().validate(request, snapshot)

    assert result.valid is True


def test_terminal_cca_like_motif_can_remain_body_coordinates_when_unconfirmed():
    request = AlignmentRequest(
        target_seq="GCCA",
        input_type="unknown",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(["1", "71", "72", "73"], "GCCA"),
    )

    assert result.valid is True


def test_confirmed_terminal_cca_requires_and_accepts_discriminator_73():
    request = AlignmentRequest(
        target_seq="GACCA",
        input_type="mature",
        cca_status="input_encoded",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(["1", "73", "74", "75", "76"], "GACCA"),
    )

    assert result.valid is True


def test_confirmed_terminal_cca_is_rejected_without_discriminator_73():
    request = AlignmentRequest(
        target_seq="GCCA",
        input_type="mature",
        cca_status="input_encoded",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "74", "75", "76"],
            "GCCA",
            raw_meta={
                "numbering_backend": "cmalign",
                "cm_portfolio": {
                    "cca_projection": {
                        "applied": True,
                        "basis": "confirmed_input_contract",
                    }
                },
            },
        ),
    )

    assert result.valid is False
    assert "cca_74_76_require_discriminator_73" in result.reasons


def test_partial_cca_prefix_coordinates_are_rejected():
    request = AlignmentRequest(
        target_seq="GACC",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(["1", "73", "74", "75", "76"], "GACC-"),
    )

    assert result.valid is False
    assert "cca_motif_invalid:CC-" in result.reasons


def test_terminal_insertions_cannot_replace_a_complete_cca_tail():
    request = AlignmentRequest(
        target_seq="GACCAU",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "73", "74", "75", "76", "74i1"],
            "GACCAU",
        ),
    )

    assert result.valid is False
    assert "terminal_insertion_not_allowed:74i1" in result.reasons


def test_73_insertion_is_allowed_for_unresolved_non_cca_extension():
    request = AlignmentRequest(
        target_seq="GACCAU",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(["1", "2", "3", "4", "73", "73i1"], "GACCAU"),
    )

    assert result.valid is True
    assert not any(
        reason.startswith("terminal_insertion_not_allowed:")
        for reason in result.reasons
    )


def test_reassigned_non_cca_extension_remains_investigate():
    request = AlignmentRequest(
        target_seq="GACCAU",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "2", "3", "4", "73", "73i1"],
            "GACCAU",
            raw_meta={
                "cm_portfolio": {
                    "terminal_coordinate_review": {
                        "required": True,
                        "reason": "non_complete_terminal_cca_represented_after_73",
                    }
                }
            },
        ),
    )

    assert result.valid is False
    assert "non_cca_terminal_extension_requires_review:non_complete_terminal_cca_represented_after_73" in result.reasons


def test_tail_insertions_after_cca_coordinates_are_rejected():
    request = AlignmentRequest(
        target_seq="GACCAU",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "73", "74", "75", "76", "76i1"],
            "GACCAU",
        ),
    )

    assert result.valid is False
    assert "cca_74_76_assignment_not_supported_by_input_evidence" in result.reasons
    assert "terminal_insertion_not_allowed:76i1" in result.reasons


def test_noncanonical_terminal_triplet_remains_invalid_for_tail_coordinates():
    request = AlignmentRequest(
        target_seq="GACAA",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(["1", "73", "74", "75", "76"], "GACAA"),
    )

    assert result.valid is False
    assert "cca_motif_invalid:CAA" in result.reasons


def test_independent_structure_cannot_support_noncanonical_terminal_coordinates():
    request = AlignmentRequest(
        target_seq="GACAA",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "73", "74", "75", "76"],
            "GACAA",
            raw_meta={
                "acceptor_tail_reconciliation": {
                    "status": "resolved",
                    "result": {
                        "resolved": True,
                        "reason": (
                            "independent_structure_recovers_acceptor_stem_endpoint"
                        ),
                        "terminal_coordinate_labels": ["73", "74", "75", "76"],
                        "terminal_extension_sequence": "CAA",
                        "terminal_motif_observation": "non_cca_extension",
                        "mature_cca_claim": "not_asserted",
                    },
                }
            },
        ),
    )

    assert result.valid is False
    assert "cca_motif_invalid:CAA" in result.reasons


def test_independent_structure_cannot_support_partial_noncanonical_terminal_coordinates():
    request = AlignmentRequest(
        target_seq="GAU",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "73", "74", "75", "76"],
            "GAU--",
            raw_meta={
                "acceptor_tail_reconciliation": {
                    "status": "resolved",
                    "result": {
                        "resolved": True,
                        "reason": (
                            "independent_structure_recovers_acceptor_stem_endpoint"
                        ),
                        "terminal_coordinate_labels": ["73", "74"],
                        "terminal_extension_sequence": "U",
                        "terminal_motif_observation": "non_cca_extension",
                        "mature_cca_claim": "not_asserted",
                    },
                }
            },
        ),
    )

    assert result.valid is False
    assert "cca_motif_invalid:U--" in result.reasons


def test_unknown_cca_claim_can_use_trnascan_unpaired_tail_evidence():
    request = AlignmentRequest(
        target_seq="GACCA",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "73", "74", "75", "76"],
            "GACCA",
            raw_meta={
                "annotation_meta": {
                    "struct_file_sequence": "GACCA",
                    "secondary_structure": "<<...",
                }
            },
        ),
    )

    assert result.valid is True


def test_unknown_input_can_use_trnascan_unpaired_tail_evidence():
    request = AlignmentRequest(
        target_seq="GACCA",
        input_type="unknown",
        cca_status="unknown",
        use_llm=True,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "73", "74", "75", "76"],
            "GACCA",
            raw_meta={
                "annotation_meta": {
                    "struct_file_sequence": "GACCA",
                    "secondary_structure": "<<...",
                }
            },
        ),
    )

    assert result.valid is True


def test_paired_cca_like_suffix_is_not_model_support_for_74_76():
    request = AlignmentRequest(
        target_seq="GACCA",
        input_type="mature",
        cca_status="unknown",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "73", "74", "75", "76"],
            "GACCA",
            raw_meta={
                "annotation_meta": {
                    "struct_file_sequence": "GACCA",
                    "secondary_structure": "<<<..",
                }
            },
        ),
    )

    assert result.valid is False
    assert "cca_74_76_assignment_not_supported_by_input_evidence" in result.reasons


def test_hard_guard_requires_cca_coordinates_to_use_terminal_residues():
    request = AlignmentRequest(
        target_seq="GCCAUCCA",
        input_type="mature",
        cca_status="input_encoded",
        use_llm=False,
    )
    result = HardGuards().validate(
        request,
        _snapshot(
            ["1", "74", "75", "76", "76i1", "76i2", "76i3", "76i4"],
            "GCCAUCCA",
        ),
    )

    assert result.valid is False
    assert "cca_74_76_do_not_map_to_terminal_input_residues" in result.reasons


def test_archaeal_unknown_pre_trna_is_eligible_for_intron_detection():
    evidence = assess_input_evidence(
        target_seq="AAGGGCCCUU",
        input_type="pre_trna",
        domain_hint="archaea",
        intron_status="unknown",
    )

    assert evidence["intron_evidence"]["automatic_detection_eligible"] is True


def test_archaeal_unknown_pre_trna_schedules_automatic_intron_tool(monkeypatch):
    service = SpecialRouteEvidenceService()
    expected = SpecialRouteEvidence(
        route="archaeal_ncintron",
        status="abstained",
        disposition="test",
        message="test",
    )
    calls: list[bool] = []

    def fake_intron(request, *, automatic_detection=False):
        calls.append(bool(automatic_detection))
        return expected

    monkeypatch.setattr(service, "_intron", fake_intron)
    result = service.run_if_applicable(
        AlignmentRequest(
            target_seq="AAGGGCCCUU",
            input_type="pre_trna",
            domain_hint="archaea",
            intron_status="unknown",
        )
    )

    assert result is expected
    assert calls == [True]


def test_intron_transform_removes_flanks_and_the_reported_interval():
    result = SpecialRouteEvidenceService._splice_from_hit(
        "AAGGCCUUTT",
        {
            "begin": 3,
            "end": 8,
            "intron_begin": 5,
            "intron_end": 6,
        },
    )

    assert result is not None
    mature, mapping, transform = result
    assert mature == "GGUU"
    assert [item["raw_residue_index"] for item in mapping] == [3, 4, 7, 8]
    assert transform["source_interval"] == {"start": 3, "end": 8}
    assert transform["removed_intron_interval"] == {"start": 5, "end": 6}
    assert transform["removed_flanks"] == {"five_prime": 2, "three_prime": 2}


def test_automatic_intron_inspection_can_derive_a_no_intron_trna_body():
    result = SpecialRouteEvidenceService._splice_from_hit(
        "AAGGCCUUTT",
        {
            "begin": 3,
            "end": 8,
            "intron_begin": 0,
            "intron_end": 0,
        },
        require_intron=False,
    )

    assert result is not None
    mature, mapping, transform = result
    assert mature == "GGCCUU"
    assert [item["raw_residue_index"] for item in mapping] == [3, 4, 5, 6, 7, 8]
    assert transform["intron_detected"] is False
    assert transform["removed_intron_interval"] == {}
