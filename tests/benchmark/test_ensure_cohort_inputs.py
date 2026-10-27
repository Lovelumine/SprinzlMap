"""The ENSURE cohort must hand the runner the record's own sequence.

The benchmark reads a sequence by joining a reference.tsv row left to right, so a
lexically sorted column header moves a long variable arm to the 3' end and the
model numbers a molecule the record never was.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "benchmark/scripts/ensure_cohort_inputs.py"

sys.path.insert(0, str(ROOT / "APP"))
spec = importlib.util.spec_from_file_location("ensure_cohort_inputs", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


# Positions 1-45, then the variable arm (V11..V17, V21..V22) and only then 46..76;
# a lexical header would put the V block after 76 instead.
CASES = {
    "long-variable-arm": {
        "sequence": "A" * 45 + "GGGUUUC" + "AA" + "A" * 31,
        "columns": [(str(n), "A") for n in range(1, 46)]
        + [(f"V{11 + i}", "GGGUUUC"[i]) for i in range(7)]
        + [("V22", "A"), ("V21", "A")]
        + [(str(n), "A") for n in range(46, 77)],
    },
    "with-insertion": {
        "sequence": "A" * 5 + "C" + "A" * 10,
        "columns": [(str(n), "A") for n in range(1, 6)]
        + [("5i1", "C")]
        + [(str(n), "A") for n in range(6, 16)],
    },
}


def write_cohort(path: Path, columns: list[str]) -> None:
    lines = ["seqname\t" + "\t".join(columns)]
    for name, rec in CASES.items():
        place = dict(rec["columns"])
        lines.append(name + "\t" + "\t".join(place.get(c, "-") for c in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_frame_columns_keep_the_chain_order(tmp_path: Path) -> None:
    columns = module.frame_columns(CASES)
    write_cohort(tmp_path / "reference.tsv", columns)
    bad = module.verify_written(
        tmp_path / "reference.tsv", {k: r["sequence"] for k, r in CASES.items()}
    )
    assert bad == [], bad
    # The variable arm sits between 45 and 46, and an insertion follows its anchor.
    assert columns.index("V11") < columns.index("46")
    assert columns.index("V11") > columns.index("45")
    assert columns.index("V22") < columns.index("46")
    assert columns.index("5i1") == columns.index("5") + 1


def test_lexical_header_would_corrupt_the_input(tmp_path: Path) -> None:
    columns = sorted(module.frame_columns(CASES))
    write_cohort(tmp_path / "reference.tsv", columns)
    assert module.verify_written(
        tmp_path / "reference.tsv", {k: r["sequence"] for k, r in CASES.items()}
    ), "a lexically sorted frame must be rejected"
