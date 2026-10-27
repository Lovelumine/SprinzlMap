from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "benchmark"
    / "scripts"
    / "summarize_submission_runs.py"
)
SPEC = importlib.util.spec_from_file_location("submission_results_summary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SUMMARY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SUMMARY
SPEC.loader.exec_module(SUMMARY)


def _row(*, source="bacteria", amino_acid="Ala", accepted=True, exact=True):
    return {
        "benchmark_case": {
            "metadata": {
                "broad_source": source,
                "amino_acid": amino_acid,
            }
        },
        "agent": {"disposition": "accepted" if accepted else "investigate"},
        "metrics": {
            "exact_match": exact,
            "label_correct": 9 if exact else 8,
            "label_total": 10,
            "variable_correct": 4,
            "variable_total": 5,
            "anticodon_correct": 3,
            "anticodon_total": 3,
            "cca_correct": 3,
            "cca_total": 4,
        },
    }


def test_scope_definition_excludes_specialized_routes():
    assert SUMMARY.is_canonical_supported(_row(source="archaea")) is True
    assert SUMMARY.is_canonical_supported(_row(source="bacteria", amino_acid="Sec")) is False
    assert SUMMARY.is_canonical_supported(_row(source="mitochondrial")) is False
    assert SUMMARY.is_canonical_supported(_row(source="virus")) is False


def test_cohort_summary_uses_accepted_cases_and_weighted_residues():
    rows = [
        _row(accepted=True, exact=True),
        _row(accepted=True, exact=False),
        _row(accepted=False, exact=False),
    ]

    result = SUMMARY.summarize_cohort(rows)

    assert result["case_count"] == 3
    assert result["accepted_count"] == 2
    assert result["accepted_coverage"] == 2 / 3
    assert result["accepted_exact_match_rate"] == 0.5
    assert result["accepted_residue_label_accuracy"] == 0.85
