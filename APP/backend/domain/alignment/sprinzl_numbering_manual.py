"""The Sprinzl coordinate rules the Agent is told about.

Two things have to stay true at once: the Agent reads a short manual, and the
Harness rejects a candidate with the rules in ``agent.evaluation.hard_guards``.
Both now come from this module, so a prompt cannot drift away from the check
that decides whether a numbering survives.  ``tests/alignment/
test_sprinzl_numbering_manual.py`` fails if a label or a base pair quoted here
stops agreeing with ``number_to_region`` and the guard tables.

The manual is prose for a language model, not documentation for a reader: it is
regenerated on import at most once and stays under about 700 tokens.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Sequence, Tuple

from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.sprinzl_coordinates import (
    SPRINZL_PARENT_INDEX,
    SPRINZL_PARENT_ORDER,
)


# Canonical helices.  A candidate whose recorded pairing disagrees with these is
# rejected, so the manual and the guard read from one table.
ACCEPTOR_PAIR_MAP: Dict[str, str] = {
    "1": "72", "72": "1",
    "2": "71", "71": "2",
    "3": "70", "70": "3",
    "4": "69", "69": "4",
    "5": "68", "68": "5",
    "6": "67", "67": "6",
    "7": "66", "66": "7",
}
D_STEM_PAIR_MAP: Dict[str, str] = {
    "10": "25", "25": "10",
    "11": "24", "24": "11",
    "12": "23", "23": "12",
    "13": "22", "22": "13",
}
ANTICODON_PAIR_MAP: Dict[str, str] = {
    "27": "43", "43": "27",
    "28": "42", "42": "28",
    "29": "41", "41": "29",
    "30": "40", "40": "30",
    "31": "39", "39": "31",
}
T_STEM_PAIR_MAP: Dict[str, str] = {
    "49": "65", "65": "49",
    "50": "64", "64": "50",
    "51": "63", "63": "51",
    "52": "62", "62": "52",
    "53": "61", "61": "53",
}
VARIABLE_PAIR_MAP: Dict[str, str] = {
    **{f"V1{k}": f"V2{k}" for k in range(1, 8)},
    **{f"V2{k}": f"V1{k}" for k in range(1, 8)},
}
VARIABLE_UNPAIRED_LABELS = frozenset(
    {"44", "45", "46", "47", "48", "V1", "V2", "V3", "V4", "V5"}
)

# Optional D-loop positions may only appear in this order: 17 then 17a, and
# 20 then 20a then 20b.
D_OPTIONAL_FAMILIES: Tuple[Tuple[str, ...], ...] = (("17", "17a"), ("20", "20a", "20b"))

# Positions 74-76 are the CCA tail; they end the row and take no insertions.
TERMINAL_CCA_LABELS = ("74", "75", "76")
DISCRIMINATOR_LABEL = "73"
ANTICODON_LABELS = ("34", "35", "36")


def region_of(label: str) -> str:
    return number_to_region(str(label))


def region_groups() -> List[Tuple[str, List[str]]]:
    """Canonical labels grouped into the runs a numbering row visits."""
    groups: List[Tuple[str, List[str]]] = []
    for label in SPRINZL_PARENT_ORDER:
        region = number_to_region(label)
        if groups and groups[-1][0] == region:
            groups[-1][1].append(label)
        else:
            groups.append((region, [label]))
    return groups


def pair_notation(mapping: Dict[str, str]) -> str:
    """Render one helix as ``1-72 2-71 ...``, counting each pair once."""
    pairs: List[str] = []
    for left, right in mapping.items():
        if SPRINZL_PARENT_INDEX[left] > SPRINZL_PARENT_INDEX[right]:
            continue
        pairs.append(f"{left}-{right}")
    return " ".join(pairs)


def _ordered_labels() -> str:
    return " ".join(SPRINZL_PARENT_ORDER)


def _region_lines() -> List[str]:
    return [
        (
            "Aminoacyl arm 5' (-1..9): the 5' terminus. "
            f"{pair_notation(ACCEPTOR_PAIR_MAP)} close the acceptor stem. "
            "-1 is the flank for a residue before position 1 and needs position 1."
        ),
        (
            "D arm (10..26 plus 17a, 20a, 20b): "
            f"{pair_notation(D_STEM_PAIR_MAP)} pair the D stem and 14..21 form the D "
            "loop. G18/G19 and U26 are class II identity elements. 17a, 20a and 20b "
            "appear only in a longer loop, after 17 and after 20."
        ),
        (
            "Anticodon arm (27..43): "
            f"{pair_notation(ANTICODON_PAIR_MAP)} pair the stem and 32..38 form the "
            "loop, with the anticodon at 34-35-36 (34 is the wobble position) and the "
            "U33 turn just before it."
        ),
        (
            "Variable loop (44, 45, V labels, 46, 47, 48): 44..48 is the short form "
            "of this arm and the V labels are the long form; a row normally uses one "
            f"or the other. In the long form {pair_notation(VARIABLE_PAIR_MAP)} are "
            "the stem and V1..V5 close the loop. A stem pair is rejected when only "
            "one side carries a residue, and 44..48 and V1..V5 never pair."
        ),
        (
            "T arm (49..65): "
            f"{pair_notation(T_STEM_PAIR_MAP)} pair the T stem and 54..60 form the T "
            "loop. T54 and the hypermodified U55 form the receptor that packs "
            "against the acceptor stem and hold the L shape of the molecule."
        ),
        (
            "Aminoacyl arm 3' (66..76): 66..72 complete the acceptor stem, 73 is the "
            "discriminator base and 74-75-76 is the CCA tail, esterified to an amino "
            "acid at A76. CCA stays contiguous at the 3' end and takes no insertions."
        ),
    ]


def manual_text() -> str:
    """The block pasted into the Agent's system prompt."""
    return "\n".join(
        [
            "REFERENCE - SPRINZL COORDINATE SYSTEM",
            "A numbering row gives every input residue, in 5' to 3' order, one Sprinzl",
            "label or '-' when it carries none. These are the rules the Harness",
            "enforces on every candidate.",
            "",
            "ORDER. The row visits canonical labels in one fixed order. It may skip",
            "positions, but never reorder them:",
            _ordered_labels(),
            "",
            "REGIONS AND CANONICAL HELICES.",
            *[f"  {line}" for line in _region_lines()],
            "",
            "VOCABULARY. A label is a canonical label above or an insertion <parent>i",
            "<k>. 17a, 20a and 20b are the only lettered labels: an extra residue",
            "after 26 is 26i1, not 26a, and the same holds at 47, 51, 57 and 64. There",
            "is no position 0 and no position 77.",
            "",
            "INSERTIONS. An extra residue takes the label <parent>i<k>: the parent",
            "position must already be assigned, k starts at 1 and stays consecutive, and",
            "the insertion sits directly after its parent in the order above.",
            "",
            "ACCEPTANCE. A candidate is rejected when it changes an input base",
            "(base_preservation_failed), assigns one residue two labels or two residues",
            "the same label (duplicate_numbering_labels, row_length_inconsistent), breaks",
            "the order or an insertion parent (sprinzl_order_violation,",
            "insertion_without_parent, nonconsecutive_insertion_index,",
            "illegal_numbering_label), moves 34-35-36 off the expected anticodon",
            "(anticodon_anchor_missing/gap/mismatch), splits or shifts the CCA tail",
            "(cca_not_contiguous, cca_74_76_*), leaves one side of a variable pair",
            "occupied (variable_singleton_occupied) or records pairing that disagrees",
            "with the helices above (*_topology_pair_violation).",
        ]
    )


def planner_brief() -> str:
    """Reference digest for the action-selection prompt.

    A planner call chooses a tool and ends in one JSON field, so it is given
    what a choice needs -- which regions exist, which labels are legal, and
    where a row that passes every rule is still likely to be wrong.  The full
    helix tables belong to the one action where the model writes labels
    itself, ``propose_coordinate_revision``, and cost measurements showed
    repeating them at every selection step buys nothing: on 258 paired cases
    the residue accuracy was identical while tokens rose 110%.
    """
    return "\n".join(
        [
            "REFERENCE - WHAT THIS HARNESS NUMBERS",
            "A numbering row gives every input residue, in 5' to 3' order, one",
            "Sprinzl label or '-' when it carries none. Tools write the labels; you",
            "choose the tool and decide when enough evidence is in.",
            "",
            "REGIONS. 1..7 and 66..72 close the acceptor stem, 10..26 the D arm,",
            "27..43 the anticodon arm with the anticodon at 34-35-36, 44..48 and the",
            "V labels the variable loop, 49..65 the T arm, 73 the discriminator and",
            "74-75-76 the CCA tail.",
            "",
            "LABELS. An extra residue is <parent>i<k>, so 26i1 and not 26a; 17a,",
            "20a and 20b are the only lettered positions. There is no 0 and no 77.",
            "",
            "RISK. A row is rejected when it breaks the order, an insertion parent,",
            "a helix pair, the anticodon anchors or CCA contiguity. A row can pass",
            "every rule and still be wrong, and that happens in the variable loop,",
            "the acceptor end and runs of insertions.",
        ]
    )


def compact_row_view(
    labels: Sequence[str],
    bases: Sequence[str] | None = None,
) -> str:
    """Render one candidate as ``region: label(label)...`` for the Agent prompt.

    Empty alignment columns are dropped, so the string length tracks the residues
    that actually carry a coordinate instead of padding the prompt with gaps.
    """
    name_labels = list(labels)
    base_row = list(bases) if bases else []
    grouped: Dict[str, List[str]] = {}
    order: List[str] = []
    for index, label in enumerate(name_labels):
        raw = str(label or "").strip()
        if not raw or raw == "-":
            continue
        if base_row:
            base = str(base_row[index] or "").strip().upper().replace("T", "U")
            if base in {"", "-"}:
                continue
        try:
            region = number_to_region(raw)
        except ValueError:
            region = "unlabelled"
        if region not in grouped:
            grouped[region] = []
            order.append(region)
        grouped[region].append(raw)
    return " | ".join(f"{region}: {' '.join(grouped[region])}" for region in order)


__all__ = [
    "ANTICODON_LABELS",
    "ANTICODON_PAIR_MAP",
    "ACCEPTOR_PAIR_MAP",
    "D_OPTIONAL_FAMILIES",
    "D_STEM_PAIR_MAP",
    "DISCRIMINATOR_LABEL",
    "T_STEM_PAIR_MAP",
    "TERMINAL_CCA_LABELS",
    "VARIABLE_PAIR_MAP",
    "VARIABLE_UNPAIRED_LABELS",
    "compact_row_view",
    "manual_text",
    "planner_brief",
    "pair_notation",
    "region_groups",
    "region_of",
]
