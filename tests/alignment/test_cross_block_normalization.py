from __future__ import annotations

from copy import deepcopy

from backend.domain.alignment.safety.normalization import (
    normalize_blocks_with_rollback,
)
from backend.domain.alignment.safety.validation import validate_alignment


def _normalize(blocks, target_seq: str) -> bool:
    return normalize_blocks_with_rollback(
        blocks=blocks,
        target_seq=target_seq,
        validate_alignment_fn=validate_alignment,
    )


def test_cross_block_gap_is_filled_despite_template_substitution() -> None:
    blocks = [
        {
            "region": "Aminoacyl arm 5' end",
            "lines": [
                ["8", "9", "9i1"],
                ["U", "A", "-"],
                ["U", "A", "U"],
            ],
        },
        {
            "region": "D loop + D stem",
            "lines": [
                ["10", "11"],
                ["G", "C"],
                ["-", "C"],
            ],
        },
    ]

    assert _normalize(blocks, "UAUC")
    assert blocks[0]["lines"] == [["8", "9"], ["U", "A"], ["U", "A"]]
    assert blocks[1]["lines"] == [["10", "11"], ["G", "C"], ["U", "C"]]
    assert blocks[1]["meta"]["deterministic_boundary_repairs"] == [
        {
            "rule": "cross_block_canonical_successor_fill_v1",
            "from_label": "9i1",
            "to_label": "10",
            "remaining_insertion_count": 0,
            "template_substitution": True,
        }
    ]


def test_cross_block_true_insertion_is_kept_when_next_position_is_occupied() -> None:
    blocks = [
        {
            "region": "Aminoacyl arm 5' end",
            "lines": [
                ["8", "9", "9i1"],
                ["U", "A", "-"],
                ["U", "A", "U"],
            ],
        },
        {
            "region": "D loop + D stem",
            "lines": [
                ["10", "11"],
                ["G", "C"],
                ["G", "C"],
            ],
        },
    ]
    original = deepcopy(blocks)

    assert _normalize(blocks, "UAUGC")
    assert blocks == original


def test_cross_block_multiple_insertions_preserve_order_after_fill() -> None:
    blocks = [
        {
            "region": "Aminoacyl arm 5' end",
            "lines": [
                ["8", "9", "9i1", "9i2"],
                ["U", "A", "-", "-"],
                ["U", "A", "U", "G"],
            ],
        },
        {
            "region": "D loop + D stem",
            "lines": [
                ["10", "11"],
                ["G", "C"],
                ["-", "C"],
            ],
        },
    ]

    assert _normalize(blocks, "UAUGC")
    assert blocks[0]["lines"] == [["8", "9"], ["U", "A"], ["U", "A"]]
    assert blocks[1]["lines"] == [
        ["10", "10i1", "11"],
        ["G", "-", "C"],
        ["U", "G", "C"],
    ]


def test_cross_block_fill_requires_immediate_canonical_successor() -> None:
    blocks = [
        {
            "region": "Aminoacyl arm 5' end",
            "lines": [
                ["8", "9", "9i1"],
                ["U", "A", "-"],
                ["U", "A", "U"],
            ],
        },
        {
            "region": "D loop + D stem",
            "lines": [
                ["11", "12"],
                ["G", "C"],
                ["-", "C"],
            ],
        },
    ]
    original = deepcopy(blocks)

    assert _normalize(blocks, "UAUC")
    assert blocks == original


def test_cross_block_fill_rolls_back_without_stale_audit_metadata() -> None:
    blocks = [
        {
            "region": "Aminoacyl arm 5' end",
            "lines": [
                ["8", "9", "9i1"],
                ["U", "A", "-"],
                ["U", "A", "U"],
            ],
        },
        {
            "region": "D loop + D stem",
            "lines": [
                ["10", "11"],
                ["G", "C"],
                ["-", "C"],
            ],
        },
    ]
    original = deepcopy(blocks)

    accepted = normalize_blocks_with_rollback(
        blocks=blocks,
        target_seq="UAUC",
        validate_alignment_fn=lambda _target, _aligned: False,
    )

    assert not accepted
    assert blocks == original
