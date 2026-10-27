from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping, Sequence


PAIR_FAMILIES: dict[str, dict[str, str]] = {
    "acceptor_stem": {
        "1": "72",
        "2": "71",
        "3": "70",
        "4": "69",
        "5": "68",
        "6": "67",
        "7": "66",
    },
    "d_stem": {"10": "25", "11": "24", "12": "23", "13": "22"},
    "anticodon_stem": {
        "27": "43",
        "28": "42",
        "29": "41",
        "30": "40",
        "31": "39",
    },
    "t_stem": {"49": "65", "50": "64", "51": "63", "52": "62", "53": "61"},
}
MINIMUM_PAIR_COUNTS = {
    "acceptor_stem": 4,
    "d_stem": 2,
    "anticodon_stem": 3,
    "t_stem": 3,
}
REQUIRED_ANCHORS = ("34", "35", "36", "73")
MINIMUM_CM_BIT_SCORE = 20.0


@dataclass(frozen=True, slots=True)
class CanonicalLikeStructureAssessment:
    qualified: bool
    source: str
    pair_counts: dict[str, int]
    minimum_pair_counts: dict[str, int]
    required_anchors: tuple[str, ...]
    missing_anchors: tuple[str, ...]
    cm_bit_score: float | None
    minimum_cm_bit_score: float
    reasons: tuple[str, ...]

    def metadata(self) -> dict[str, Any]:
        return asdict(self)


def assess_canonical_like_structure(
    *,
    numbering_row: Sequence[str],
    sequence_row: Sequence[str],
    structure_pairs: Sequence[Mapping[str, Any]],
    numbering_backend: str,
    cm_bit_score: Any,
) -> CanonicalLikeStructureAssessment:
    occupied = {
        str(label or "").strip()
        for label, base in zip(numbering_row, sequence_row)
        if str(base or "").strip() not in {"", "-"}
    }
    observed_pairs = {
        (
            str((pair or {}).get("left_id") or "").strip(),
            str((pair or {}).get("right_id") or "").strip(),
        )
        for pair in structure_pairs
    }
    pair_counts = {
        family: sum(
            mapping.get(left) == right or mapping.get(right) == left
            for left, right in observed_pairs
        )
        for family, mapping in PAIR_FAMILIES.items()
    }
    missing_anchors = tuple(anchor for anchor in REQUIRED_ANCHORS if anchor not in occupied)
    try:
        score = float(cm_bit_score)
    except (TypeError, ValueError):
        score = None
    if score is not None and not math.isfinite(score):
        score = None

    reasons: list[str] = []
    if str(numbering_backend or "") != "cmalign":
        reasons.append("numbering_cm_evidence_required")
    for family, minimum in MINIMUM_PAIR_COUNTS.items():
        if pair_counts[family] < minimum:
            reasons.append(
                f"insufficient_{family}_pairs:{pair_counts[family]}<{minimum}"
            )
    if missing_anchors:
        reasons.append("missing_required_anchors:" + ",".join(missing_anchors))
    if score is None or score < MINIMUM_CM_BIT_SCORE:
        observed = "missing" if score is None else f"{score:.3f}"
        reasons.append(
            f"cm_bit_score_below_special_route_threshold:{observed}<{MINIMUM_CM_BIT_SCORE:.1f}"
        )

    return CanonicalLikeStructureAssessment(
        qualified=not reasons,
        source="numbering_cm_consensus_structure",
        pair_counts=pair_counts,
        minimum_pair_counts=dict(MINIMUM_PAIR_COUNTS),
        required_anchors=REQUIRED_ANCHORS,
        missing_anchors=missing_anchors,
        cm_bit_score=score,
        minimum_cm_bit_score=MINIMUM_CM_BIT_SCORE,
        reasons=tuple(reasons),
    )


__all__ = [
    "CanonicalLikeStructureAssessment",
    "MINIMUM_CM_BIT_SCORE",
    "MINIMUM_PAIR_COUNTS",
    "PAIR_FAMILIES",
    "REQUIRED_ANCHORS",
    "assess_canonical_like_structure",
]
