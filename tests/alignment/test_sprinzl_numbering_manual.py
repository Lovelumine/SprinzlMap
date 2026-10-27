# -*- coding: utf-8 -*-
"""The Agent's coordinate manual must state the rules the guards actually apply."""
from __future__ import annotations

import re

from backend.agent.evaluation import hard_guards
from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.sprinzl_coordinates import (
    SPRINZL_MATCH_ORDER,
    SPRINZL_PARENT_INDEX,
    SPRINZL_PARENT_ORDER,
)
from backend.domain.alignment.sprinzl_numbering_manual import (
    ACCEPTOR_PAIR_MAP,
    ANTICODON_PAIR_MAP,
    D_OPTIONAL_FAMILIES,
    D_STEM_PAIR_MAP,
    T_STEM_PAIR_MAP,
    VARIABLE_PAIR_MAP,
    compact_row_view,
    manual_text,
    planner_brief,
    region_groups,
)


def test_manual_order_is_the_order_the_guards_enforce():
    order_line = re.search(
        r"never reorder them:\n(.+)", manual_text()
    ).group(1)
    assert order_line.split() == list(SPRINZL_PARENT_ORDER)


def test_manual_vocabulary_claim_matches_the_legal_label_set():
    # The textbook Sprinzl row letters insertions at 26, 47, 51, 57 and 64.  This
    # system does not, and a model that follows the textbook loses the candidate,
    # so the manual's claim has to keep agreeing with the label table.
    lettered = [
        label
        for label in SPRINZL_PARENT_ORDER
        if not label.startswith(("-", "V")) and not label.isdigit()
    ]
    assert sorted(lettered) == ["17a", "20a", "20b"]
    text = manual_text()
    assert "17a, 20a and 20b are the only lettered labels" in text
    assert "26i1, not 26a" in text
    for textbook in ("26a", "47a", "51a", "57a", "64a"):
        assert textbook not in SPRINZL_PARENT_INDEX
    for insertion in ("26i1", "47i1", "51i1", "57i1", "64i1"):
        assert insertion.split("i")[0] in SPRINZL_PARENT_INDEX


def test_manual_quotes_only_canonical_labels():
    for mapping in (
        ACCEPTOR_PAIR_MAP,
        D_STEM_PAIR_MAP,
        ANTICODON_PAIR_MAP,
        T_STEM_PAIR_MAP,
        VARIABLE_PAIR_MAP,
    ):
        for label in mapping:
            assert label in SPRINZL_PARENT_ORDER
    for label in ("17a", "20a", "20b", "V11", "V27", "-1", "47", "73", "74", "75", "76"):
        assert label in SPRINZL_PARENT_ORDER


def test_manual_helices_match_the_guard_pair_tables():
    assert ACCEPTOR_PAIR_MAP == hard_guards._ACCEPTOR_PAIR_MAP
    assert D_STEM_PAIR_MAP == hard_guards._D_STEM_PAIR_MAP
    assert ANTICODON_PAIR_MAP == hard_guards._ANTICODON_PAIR_MAP
    assert T_STEM_PAIR_MAP == hard_guards._T_STEM_PAIR_MAP
    assert VARIABLE_PAIR_MAP == hard_guards._VARIABLE_PAIR_MAP
    assert D_OPTIONAL_FAMILIES == (("17", "17a"), ("20", "20a", "20b"))
    text = manual_text()
    for mapping in (
        ACCEPTOR_PAIR_MAP,
        D_STEM_PAIR_MAP,
        ANTICODON_PAIR_MAP,
        T_STEM_PAIR_MAP,
        VARIABLE_PAIR_MAP,
    ):
        for left, right in mapping.items():
            if SPRINZL_PARENT_INDEX[left] > SPRINZL_PARENT_INDEX[right]:
                continue
            assert f"{left}-{right}" in text


def test_manual_region_ranges_agree_with_number_to_region():
    for region, labels in region_groups():
        assert number_to_region(labels[0]) == region
    text = manual_text()
    assert "(-1..9)" in text
    assert "Aminoacyl arm 3' (66..76)" in text
    assert "Variable loop (44, 45, V labels, 46, 47, 48)" in text


def test_manual_stays_bounded_for_the_planner_prompt():
    # The manual is pasted into every planning call, so it is kept to roughly
    # the size of one alignment row rather than a chapter.
    assert len(manual_text()) < 3200
    assert manual_text() == manual_text()


def test_the_planner_digest_is_shorter_than_the_full_manual():
    # The planner only chooses a tool, so it gets the digest.  The full manual
    # is paid for once, in the action where the model writes labels itself.
    brief = planner_brief()
    assert brief == planner_brief()
    assert len(brief) < 1600
    assert len(brief) < len(manual_text()) * 0.6
    assert "REFERENCE -" in brief


def test_the_planner_digest_states_only_legal_labels():
    # The digest gives label examples, and each example has to be legal here
    # for the same reason it is legal to the guards.
    from backend.domain.alignment.coordinate_proposal import is_legal_label

    digest = planner_brief()
    # "26i1 and not 26a" is a counterexample, so it is the one label-shaped
    # token in the digest that must not be legal.
    examples = [
        token
        for token in re.findall(r"(?<![\w.])\d{1,2}(?:i\d|[ab])(?![\w-])", digest)
        if token != "26a"
    ]
    assert "26i1" in examples
    assert examples
    assert all(is_legal_label(label) for label in examples), examples
    assert "not 26a" in digest
    assert "no 0" in digest and "no 77" in digest


def test_compact_row_view_groups_labels_and_skips_gaps():
    labels = ["-1", "1", "2", "47i1", "", "34", "35", "36"]
    bases = ["A", "G", "C", "U", "-", "G", "G", "A"]
    view = compact_row_view(labels, bases)
    assert view.startswith("Aminoacyl arm 5' end: -1 1 2")
    assert "47i1" in view
    assert "Anticodon loop + Anticodon stem: 34 35 36" in view
    tokens = [
        label
        for chunk in view.split("|")
        for label in chunk.split(":", 1)[1].split()
    ]
    assert tokens == ["-1", "1", "2", "47i1", "34", "35", "36"]
    full_row = compact_row_view(list(SPRINZL_MATCH_ORDER), None)
    assert len(full_row.split("|")) == 6
    assert "Variable loop: 44 45 V11" in full_row
