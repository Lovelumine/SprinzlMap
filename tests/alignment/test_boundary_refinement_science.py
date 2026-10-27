from __future__ import annotations

from copy import deepcopy

from backend.domain.alignment.engine import STANDARD_ALIGNMENT_REGIONS
from backend.domain.alignment.engine.boundary_refinement import (
    BOUNDARY_SCORING_POLICY,
    maybe_refine_anticodon_boundaries,
)


def _comparison(
    *,
    current_lengths: list[int],
    template_lengths: list[int],
    raw_seq: str | None = None,
    structure: str | None = None,
    annotation_extra: dict | None = None,
) -> dict:
    sequence = raw_seq or ("A" * sum(current_lengths))
    full_structure = structure or ("." * len(sequence))
    regions = {}
    left = 0
    for region, current_length, template_length in zip(
        STANDARD_ALIGNMENT_REGIONS,
        current_lengths,
        template_lengths,
    ):
        right = left + current_length
        regions[region] = {
            "input_seq": sequence[left:right],
            "template_seq": "A" * template_length,
            "input_struct": full_structure[left:right],
        }
        left = right
    annotation_meta = {
        "secondary_structure": full_structure,
        "annotation_source": "tRNAscan-SE",
    }
    annotation_meta.update(dict(annotation_extra or {}))
    return {
        "raw_input_seq": sequence,
        "template_name": "synthetic-template",
        "regions": regions,
        "annotation_meta": annotation_meta,
    }


def test_anticodon_span_has_priority_over_template_length_fit() -> None:
    lengths = [7, 18, 15, 6, 17, 7]
    cmp = _comparison(
        current_lengths=lengths,
        template_lengths=lengths,
        annotation_extra={
            "trnascan_anticodon": "AAA",
            "trnascan_anticodon_start": 24,
            "trnascan_anticodon_end": 26,
        },
    )

    maybe_refine_anticodon_boundaries(cmp, list(STANDARD_ALIGNMENT_REGIONS))

    meta = cmp["annotation_meta"]
    assert meta["boundary_resolved"] is True
    assert meta["boundary_deltas"] == {"d_to_ac": -2, "ac_to_var": 0}
    assert meta["boundary_evidence_before"]["anticodon_span_contained"] is False
    assert meta["boundary_evidence_after"]["anticodon_span_contained"] is True
    assert meta["boundary_score_after"] > meta["boundary_score_before"]


def test_structure_cut_pair_has_priority_over_template_length_fit() -> None:
    lengths = [7, 18, 15, 6, 17, 7]
    structure = ["."] * sum(lengths)
    structure[24] = ">"
    structure[25] = "<"
    cmp = _comparison(
        current_lengths=lengths,
        template_lengths=lengths,
        structure="".join(structure),
    )

    maybe_refine_anticodon_boundaries(cmp, list(STANDARD_ALIGNMENT_REGIONS))

    meta = cmp["annotation_meta"]
    assert meta["boundary_resolved"] is True
    assert meta["boundary_deltas"] == {"d_to_ac": -1, "ac_to_var": 0}
    assert meta["boundary_evidence_before"]["structure_cut_pairs"] == 1
    assert meta["boundary_evidence_after"]["structure_cut_pairs"] == 0
    assert meta["boundary_scoring_policy"] == BOUNDARY_SCORING_POLICY


def test_disabled_boundary_component_is_a_strict_no_op() -> None:
    current_lengths = [7, 18, 15, 6, 17, 7]
    template_lengths = [7, 17, 17, 5, 17, 7]
    cmp = _comparison(
        current_lengths=current_lengths,
        template_lengths=template_lengths,
    )
    before = deepcopy(cmp)

    maybe_refine_anticodon_boundaries(
        cmp,
        list(STANDARD_ALIGNMENT_REGIONS),
        runtime_values_override={
            "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT": False,
        },
    )

    assert cmp == before


def test_unbalanced_structure_is_not_used_as_boundary_evidence() -> None:
    lengths = [7, 18, 15, 6, 17, 7]
    structure = ["."] * sum(lengths)
    structure[24] = ">"
    cmp = _comparison(
        current_lengths=lengths,
        template_lengths=lengths,
        structure="".join(structure),
    )
    before = deepcopy(cmp)

    maybe_refine_anticodon_boundaries(cmp, list(STANDARD_ALIGNMENT_REGIONS))

    assert cmp == before
