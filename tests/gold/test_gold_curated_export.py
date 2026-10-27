from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.domain.gold_curation import service


def _write_gold(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["seqname", "1", "2"])
        writer.writerow(["keep", "A", "C"])
        writer.writerow(["drop", "G", "U"])


def _read_names(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [str(row["seqname"]) for row in csv.DictReader(handle, delimiter="\t")]


def test_curated_export_excludes_manual_exclusions_by_default(tmp_path: Path, monkeypatch):
    gold = tmp_path / "gold.tsv"
    output = tmp_path / "curated.tsv"
    _write_gold(gold)
    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", gold)
    monkeypatch.setattr(
        service,
        "_latest_corrections",
        lambda: {"drop": {"status": "excluded"}},
    )

    result = service.export_curated_tsv(output)

    assert _read_names(output) == ["keep"]
    assert result["source_rows"] == 2
    assert result["rows"] == 1
    assert result["excluded"] == 1


def test_curated_export_can_reproduce_legacy_inclusive_view(tmp_path: Path, monkeypatch):
    gold = tmp_path / "gold.tsv"
    output = tmp_path / "curated.tsv"
    _write_gold(gold)
    monkeypatch.setattr(service, "DEFAULT_GOLD_TSV", gold)
    monkeypatch.setattr(
        service,
        "_latest_corrections",
        lambda: {"drop": {"status": "excluded"}},
    )

    result = service.export_curated_tsv(output, include_excluded=True)

    assert _read_names(output) == ["keep", "drop"]
    assert result["rows"] == 2
    assert result["excluded"] == 0


def test_mysql_export_preserves_variable_arm_sequence_order(tmp_path, monkeypatch):
    # Deliberately shuffled columns emulate a database row; order must come
    # from the Sprinzl schema, including the reverse-numbered variable arm.
    row = {"seqname": "long-arm", "46": "U", "V21": "G", "V23": "C", "45": "A", "V1": "U", "V12": "C", "V11": "G", "V22": "A"}
    monkeypatch.setattr(service.sequence_db, "enabled", lambda: True)
    monkeypatch.setattr(service.sequence_db, "baseline_rows", lambda: {"long-arm": row})
    # The exported answer comes from trna_gold, so the fake has to feed both the
    # column schema and the answer table.
    monkeypatch.setattr(service.sequence_db, "gold_is_populated", lambda: True)
    monkeypatch.setattr(
        service.sequence_db,
        "gold_rows",
        lambda seqnames=None: {
            "long-arm": {
                "seqname": "long-arm",
                "raw_labels": ["45", "V11", "V12", "V1", "V23", "V22", "V21", "46"],
                "raw_bases": ["A", "G", "C", "U", "C", "A", "G", "U"],
                "gold_labels": ["45", "V11", "V12", "V1", "V23", "V22", "V21", "46"],
                "gold_bases": ["A", "G", "C", "U", "C", "A", "G", "U"],
                "gold_source": "raw",
                "gold_sha256": "0" * 64,
                "decided_at": None,
                "decision_hash": None,
                "decision_run_id": None,
                "evaluation_allowed": True,
            }
        },
    )
    output = tmp_path / "mysql-export.tsv"

    service.export_curated_tsv(output)

    with output.open() as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        actual = next(reader)
        numbering = reader.fieldnames[1:]
    assert numbering == ["45", "V11", "V12", "V1", "V23", "V22", "V21", "46"]
    assert "".join(actual[label] for label in numbering) == "AGCUCAGU"


def test_new_insertions_are_sorted_numerically_after_their_parent():
    raw = service.csv_from_residue_labels(
        ["65", "65i1", "65i2", "65i10", "66"],
        list("ACGUA"),
        ["65", "65i2", "66"],
    )
    rows = list(csv.reader(io.StringIO(raw)))
    assert rows[0] == ["65", "65i1", "65i2", "65i10", "66"]
    assert "".join(rows[2]) == "ACGUA"
