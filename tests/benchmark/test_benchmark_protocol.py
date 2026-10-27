from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.domain.benchmarking.protocol import (
    GoldRecord,
    assign_split,
    build_case_records,
    leakage_checks,
    load_case_manifest,
    select_manifest_cases,
    validate_split_ratios,
)


def _fixtures():
    gold = [
        GoldRecord("a", "ACGU"),
        GoldRecord("b", "ACGU"),
        GoldRecord("c", "GGCA"),
        GoldRecord("d", "UUAA"),
    ]
    metadata = {
        "a": {"kingdom": "Bacteria", "organism": "Org 1", "amino_acid": "Ala"},
        "b": {"kingdom": "Bacteria", "organism": "Org 2", "amino_acid": "Ala"},
        "c": {"kingdom": "Archaea", "organism": "Org 3", "amino_acid": "Gly"},
        "d": {"kingdom": "Eukaryota", "organism": "Org 4", "amino_acid": "Leu"},
    }
    corrections = {"d": {"decision": "excluded"}}
    template_digests = {gold[0].sequence_sha256}
    return gold, metadata, corrections, template_digests


def test_split_assignment_is_deterministic_and_ratio_checked():
    first = assign_split("same-group", protocol="unique_sequence", seed="seed")
    second = assign_split("same-group", protocol="unique_sequence", seed="seed")
    assert first == second
    assert first in {"train", "calibration", "test"}
    with pytest.raises(ValueError):
        validate_split_ratios((0.8, 0.3, -0.1))


def test_manifest_keeps_duplicates_and_isotypes_in_single_splits():
    records = build_case_records(*_fixtures(), seed="test-seed")
    by_name = {record["seqname"]: record for record in records}

    assert by_name["a"]["splits"]["unique_sequence"] == by_name["b"]["splits"]["unique_sequence"]
    assert by_name["a"]["splits"]["isotype_holdout"] == by_name["b"]["splits"]["isotype_holdout"]
    assert by_name["a"]["duplicate_group_size"] == 2
    assert by_name["b"]["duplicate_group_size"] == 2
    assert by_name["a"]["template_exact_overlap"] is True
    assert by_name["c"]["template_exact_overlap"] is False
    assert by_name["d"]["eligible"] is False
    assert set(by_name["d"]["splits"].values()) == {"excluded"}

    checks = leakage_checks(records)
    assert all(item["cross_split_group_count"] == 0 for item in checks.values())


def test_manifest_selection_filters_excluded_and_template_view(tmp_path: Path):
    records = build_case_records(*_fixtures(), seed="test-seed")
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    loaded = load_case_manifest(manifest)
    split = records[0]["splits"]["unique_sequence"]

    selected = select_manifest_cases(
        loaded,
        protocol="unique_sequence",
        split=split,
        template_overlap="visible",
    )
    assert set(selected) == {"a", "b"}
    assert "d" not in selected


def test_unknown_protocol_and_split_are_rejected():
    with pytest.raises(ValueError):
        select_manifest_cases([], protocol="unknown", split="test")
    with pytest.raises(ValueError):
        select_manifest_cases([], protocol="unique_sequence", split="validation")
