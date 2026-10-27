from backend.domain.alignment.canonical_like_structure import (
    assess_canonical_like_structure,
)


def complete_pairs():
    return [
        {"left_id": "1", "right_id": "72"},
        {"left_id": "2", "right_id": "71"},
        {"left_id": "3", "right_id": "70"},
        {"left_id": "4", "right_id": "69"},
        {"left_id": "10", "right_id": "25"},
        {"left_id": "11", "right_id": "24"},
        {"left_id": "27", "right_id": "43"},
        {"left_id": "28", "right_id": "42"},
        {"left_id": "29", "right_id": "41"},
        {"left_id": "49", "right_id": "65"},
        {"left_id": "50", "right_id": "64"},
        {"left_id": "51", "right_id": "63"},
    ]


def test_canonical_like_structure_requires_four_arms_anchors_and_cm_score():
    labels = ["1", "2", "3", "4", "10", "11", "24", "25", "27", "28", "29", "34", "35", "36", "41", "42", "43", "49", "50", "51", "63", "64", "65", "69", "70", "71", "72", "73"]
    result = assess_canonical_like_structure(
        numbering_row=labels,
        sequence_row=["A"] * len(labels),
        structure_pairs=complete_pairs(),
        numbering_backend="cmalign",
        cm_bit_score=20.0,
    )

    assert result.qualified is True
    assert result.pair_counts == {
        "acceptor_stem": 4,
        "d_stem": 2,
        "anticodon_stem": 3,
        "t_stem": 3,
    }


def test_canonical_like_structure_does_not_treat_forced_partial_projection_as_cloverleaf():
    result = assess_canonical_like_structure(
        numbering_row=["1", "2", "34", "35", "36", "72", "73"],
        sequence_row=["A"] * 7,
        structure_pairs=[{"left_id": "1", "right_id": "72"}],
        numbering_backend="cmalign",
        cm_bit_score=-1.0,
    )

    assert result.qualified is False
    assert "insufficient_d_stem_pairs:0<2" in result.reasons
    assert any(reason.startswith("cm_bit_score_below") for reason in result.reasons)
