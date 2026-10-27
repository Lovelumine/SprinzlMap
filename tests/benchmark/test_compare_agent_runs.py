from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "benchmark"
    / "scripts"
    / "compare_agent_runs.py"
)
SPEC = importlib.util.spec_from_file_location("compare_agent_runs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def row(disposition: str, exact: bool, correct: int, total: int = 10):
    return {
        "agent": {"disposition": disposition},
        "metrics": {
            "exact_match": exact,
            "label_correct": correct,
            "label_total": total,
        },
    }


def scored_row(
    disposition: str,
    exact: bool,
    correct: int,
    *,
    allowed: bool,
    cluster: str,
):
    value = row(disposition, exact, correct)
    value["gold_evaluation_allowed"] = allowed
    value["benchmark_case"] = {
        "gold_evaluation_allowed": allowed,
        "similarity_cluster": {"cluster_id": cluster},
    }
    return value


def test_compare_runs_reports_paired_transitions_and_direction():
    left = {
        "a": row("accepted", True, 10),
        "b": row("investigate", False, 7),
        "c": row("accepted", True, 10),
    }
    right = {
        "a": row("accepted", False, 9),
        "b": row("accepted", False, 8),
        "c": row("investigate", False, 7),
    }

    result = MODULE.compare_runs(
        left,
        right,
        left_label="llm",
        right_label="fixed",
        bootstrap_samples=20,
        seed=7,
    )

    assert result["common_accepted_count"] == 1
    assert result["left_only_accepted_count"] == 1
    assert result["right_only_accepted_count"] == 1
    assert result["cohort_metrics"]["llm"]["accepted_coverage"] == 2 / 3
    assert result["cohort_metrics"]["fixed"]["accepted_coverage"] == 2 / 3
    assert result["disposition_transition_right_to_left"] == {
        "accepted->accepted": 1,
        "accepted->investigate": 1,
        "investigate->accepted": 1,
    }
    paired = result["paired_bootstrap"]
    assert paired["difference_direction"] == "llm_minus_fixed"
    assert paired["common_accepted_exact_match_rate"]["estimate"] == 1.0


def test_compare_runs_rejects_different_case_sets():
    try:
        MODULE.compare_runs(
            {"a": row("accepted", True, 10)},
            {"b": row("accepted", True, 10)},
            left_label="left",
            right_label="right",
            bootstrap_samples=10,
            seed=1,
        )
    except ValueError as exc:
        assert "run case sets differ" in str(exc)
    else:
        raise AssertionError("different case sets must be rejected")


def test_canonical_scope_filter_excludes_special_routes():
    canonical = {
        "benchmark_case": {
            "metadata": {"broad_source": "bacteria", "amino_acid": "Ala"}
        }
    }
    mitochondrial = {
        "benchmark_case": {
            "metadata": {"broad_source": "mitochondrial", "amino_acid": "Ala"}
        }
    }
    sec = {
        "benchmark_case": {
            "metadata": {"broad_source": "bacteria", "amino_acid": "Sec"}
        }
    }
    left = {"canonical": canonical, "mitochondrial": mitochondrial, "sec": sec}
    right = {name: dict(row) for name, row in left.items()}

    left_selected, right_selected = MODULE.filter_scope(
        left,
        right,
        "canonical_supported",
    )

    assert set(left_selected) == {"canonical"}
    assert set(right_selected) == {"canonical"}


def test_broad_source_filter_requires_matching_source_metadata():
    left = {
        "bact": {
            "benchmark_case": {"metadata": {"broad_source": "Bacteria"}}
        },
        "arch": {
            "benchmark_case": {"metadata": {"broad_source": "archaea"}}
        },
    }
    right = {
        name: {
            "benchmark_case": {
                "metadata": dict(row["benchmark_case"]["metadata"])
            }
        }
        for name, row in left.items()
    }

    left_selected, right_selected = MODULE.filter_broad_source(
        left,
        right,
        "bacteria",
    )

    assert set(left_selected) == {"bact"}
    assert set(right_selected) == {"bact"}

    right["bact"]["benchmark_case"]["metadata"]["broad_source"] = "archaea"
    try:
        MODULE.filter_broad_source(left, right, "bacteria")
    except ValueError as exc:
        assert "metadata differs" in str(exc)
    else:
        raise AssertionError("source metadata mismatch must be rejected")


def test_source_label_conflict_filter_uses_frozen_case_metadata():
    left = {
        "clean": {"benchmark_case": {"source_label_conflict": False}},
        "conflict": {"benchmark_case": {"source_label_conflict": True}},
    }
    right = {
        name: {"benchmark_case": dict(row["benchmark_case"])}
        for name, row in left.items()
    }

    left_selected, right_selected = MODULE.filter_source_label_conflicts(
        left,
        right,
        exclude=True,
    )

    assert set(left_selected) == {"clean"}
    assert set(right_selected) == {"clean"}

    right["clean"]["benchmark_case"]["source_label_conflict"] = True
    try:
        MODULE.filter_source_label_conflicts(left, right, exclude=True)
    except ValueError as exc:
        assert "metadata differs" in str(exc)
    else:
        raise AssertionError("conflict metadata mismatch must be rejected")


def test_gold_scope_and_cluster_bootstrap_exclude_unreviewed_cases():
    left = {
        "a": scored_row("accepted", True, 10, allowed=True, cluster="cluster-1"),
        "b": scored_row("accepted", True, 10, allowed=True, cluster="cluster-1"),
        "hidden": scored_row("accepted", False, 0, allowed=False, cluster="cluster-2"),
    }
    right = {
        "a": scored_row("accepted", False, 9, allowed=True, cluster="cluster-1"),
        "b": scored_row("investigate", False, 8, allowed=True, cluster="cluster-1"),
        "hidden": scored_row("accepted", False, 0, allowed=False, cluster="cluster-2"),
    }

    left_scored, right_scored = MODULE.filter_gold_scope(left, right, "scored")
    result = MODULE.compare_runs(
        left_scored,
        right_scored,
        left_label="agent",
        right_label="baseline",
        bootstrap_samples=20,
        seed=7,
        bootstrap_unit="similarity_cluster",
    )

    assert set(left_scored) == {"a", "b"}
    assert result["case_count"] == 2
    assert result["paired_bootstrap"]["unit"] == "similarity_cluster"
    assert result["paired_bootstrap"]["coverage_unit_count"] == 1


def test_compare_runs_allows_no_common_accepted_cases():
    left = {"a": row("accepted", True, 10)}
    right = {"a": row("investigate", False, 0)}

    result = MODULE.compare_runs(
        left,
        right,
        left_label="agent",
        right_label="baseline",
        bootstrap_samples=20,
        seed=7,
    )

    assert result["common_accepted_count"] == 0
    assert result["paired_bootstrap"]["common_accepted_unit_count"] == 0
    assert result["paired_bootstrap"]["common_accepted_exact_match_rate"] == {
        "estimate": None,
        "ci95": None,
    }
