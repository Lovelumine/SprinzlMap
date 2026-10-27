# -*- coding: utf-8 -*-
import sys

sys.path.insert(0, ".")

from backend.agent.contracts import AlignmentRequest, RootSnapshot
from backend.agent.evaluation.hard_guards import HardGuards


def _snapshot(numbering_row, sequence_row) -> RootSnapshot:
    return RootSnapshot(
        template_name="tpl",
        csv_content="",
        numbering_row=list(numbering_row),
        template_row=["-"] * len(numbering_row),
        sequence_row=list(sequence_row),
        raw_meta={"annotation_meta": {"trnascan_hit": False, "trnascan_anticodon": ""}},
    )


def test_variable_mirror_positions_allow_one_sided_gap():
    request = AlignmentRequest(target_seq="A", use_llm=False)
    snapshot = _snapshot(
        ["44", "45", "V14", "V24", "46", "47", "48"],
        ["-", "-", "A", "-", "-", "-", "-"],
    )

    result = HardGuards().validate(request, snapshot)

    assert result.valid is True
    assert result.reasons == []


def test_variable_singleton_position_still_rejected():
    request = AlignmentRequest(target_seq="A", use_llm=False)
    snapshot = _snapshot(
        ["44", "45", "V14", "46", "47", "48"],
        ["-", "-", "A", "-", "-", "-"],
    )

    result = HardGuards().validate(request, snapshot)

    assert result.valid is False
    assert result.reasons == ["variable_singleton_occupied:V14"]


def test_structure_view_rejects_variable_topology_pair_drift():
    request = AlignmentRequest(target_seq="UGGGCUGCCUC", use_llm=False)
    snapshot = RootSnapshot(
        template_name="tpl",
        csv_content="",
        numbering_row=["44", "45", "V11", "V12", "V13", "V23", "V22", "V21", "46", "47", "48"],
        template_row=["-"] * 11,
        sequence_row=["U", "G", "G", "G", "C", "U", "G", "C", "C", "U", "C"],
        structure_view={
            "id_pairs": [
                {"left_id": "V11", "right_id": "46", "left_index": 3, "right_index": 9},
            ]
        },
        raw_meta={"annotation_meta": {"trnascan_hit": False, "trnascan_anticodon": ""}},
    )

    result = HardGuards().validate(request, snapshot)

    assert result.valid is False
    assert result.reasons == ["variable_topology_pair_violation:V11-46"]


def test_unpaired_variable_region_rejects_left_stem_occupancy_before_later_labels():
    request = AlignmentRequest(target_seq="UGGC", use_llm=False)
    snapshot = RootSnapshot(
        template_name="tpl",
        csv_content="",
        numbering_row=["44", "45", "V11", "V1", "V2", "V27", "V21", "46", "47", "48"],
        template_row=["-"] * 10,
        sequence_row=["U", "G", "G", "-", "-", "-", "C", "-", "-", "-"],
        raw_meta={
            "annotation_meta": {"trnascan_hit": False, "trnascan_anticodon": ""},
            "agent_blocks": [
                {
                    "region": "Variable loop",
                    "meta": {"input_struct": "...."},
                }
            ],
        },
    )

    result = HardGuards().validate(request, snapshot)

    assert result.valid is False
    assert len(result.reasons) == 1
    assert result.reasons[0].startswith("variable_topology_unpaired_priority_violation:")
    assert "exhaust V1..V5 before stem extension" in result.reasons[0]


def test_unpaired_variable_region_rejects_right_heavy_extension():
    request = AlignmentRequest(target_seq="UGGCUAGCAAC", use_llm=False)
    snapshot = RootSnapshot(
        template_name="tpl",
        csv_content="",
        numbering_row=["44", "45", "V11", "V12", "V13", "V14", "V1", "V2", "V24", "V23", "V22", "V21", "46", "47", "48"],
        template_row=["-"] * 15,
        sequence_row=["U", "G", "-", "-", "-", "-", "G", "C", "U", "A", "G", "C", "A", "A", "C"],
        raw_meta={
            "annotation_meta": {"trnascan_hit": False, "trnascan_anticodon": ""},
            "agent_blocks": [
                {
                    "region": "Variable loop",
                    "meta": {"input_struct": "........."},
                }
            ],
        },
    )

    result = HardGuards().validate(request, snapshot)

    assert result.valid is False
    assert len(result.reasons) == 1
    assert result.reasons[0].startswith("variable_topology_unpaired_priority_violation:")
    assert "stem extension must stay left/right balanced" in result.reasons[0]
