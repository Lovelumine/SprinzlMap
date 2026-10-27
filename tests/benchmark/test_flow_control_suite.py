"""The flow-control campaign and its paired summary tables."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, script: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SUITE = _load("flow_control_suite", "benchmark/scripts/run_flow_control_suite.py")
SUMMARY = _load("summarize_flow_controls", "benchmark/scripts/summarize_flow_controls.py")


def test_every_control_avoids_the_language_model():
    """A control run is only interpretable when the planner is provably silent."""
    assert set(SUITE.VARIANTS) == {
        "cm-direct", "harness-cm-root", "harness-retrieval-dp", "fixed-planner",
    }
    for name, config in SUITE.VARIANTS.items():
        # The planner is silent in every control: either there is no planner at
        # all (cm backend, --no-llm) or the selector is the fixed policy.
        silent = config["method_backend"] == "cm" or config["no_llm"] or config["agent_planner_policy"] == "fixed"
        assert silent, name
        assert config["method_backend"] in ("agent", "cm"), name
        assert config["root_strategy"] in ("agent_toolbox", "cm_first", "cm_only", "legacy_only"), name
        assert config["workers"] >= 1 and config["purpose"], name
    # The three Harness rungs share one cohort but differ in exactly one stage.
    assert SUITE.VARIANTS["harness-cm-root"]["root_strategy"] == "cm_first"
    assert SUITE.VARIANTS["harness-retrieval-dp"]["root_strategy"] == "agent_toolbox"
    assert SUITE.VARIANTS["fixed-planner"]["no_llm"] is False


def test_controller_command_carries_the_variant(monkeypatch, tmp_path):
    captured = {}

    def fake_call(command, cwd=None, **kwargs):
        captured["command"] = command
        return 0

    monkeypatch.setattr(SUITE.subprocess, "call", fake_call)
    staged = {
        "reference.tsv": tmp_path / "reference.tsv",
        "seqnames.txt": tmp_path / "seqnames.txt",
        "case_manifest.jsonl": tmp_path / "case_manifest.jsonl",
    }
    staged["seqnames.txt"].write_text("a|one\nb|two\n")
    from argparse import Namespace

    args = Namespace(workers=16, fallback_workers="12,8", max_passes=3, main_run="main-run")
    code = SUITE.run_controller("harness-cm-root", SUITE.VARIANTS["harness-cm-root"], "run-ctl-1", staged, args, tmp_path)
    assert code == 0
    command = " ".join(captured["command"])
    assert "--no-llm" in captured["command"]
    assert "--agent-canonical-root-strategy cm_first" in command
    assert "--agent-planner-policy fixed" in command
    assert "--variant harness-cm-root" in command
    assert "--full-rerun" in captured["command"]


# ---------------------------------------------------------------- summary tables
def case(seqname, labels, bases, *, scored=True, gold=None, **metrics):
    """One rebuilt case trace: frozen prediction plus the active answer.

    ``labels`` is what the model produced; ``gold`` is the answer the trace was
    re-graded against, which a rebuilt run directory carries on the trace.
    """
    numbering = ",".join(labels)
    target = ",".join(bases)
    merged = {"label_correct": len(labels), "label_total": len(labels), "exact_match": True,
              "legality_passed": True, "anticodon_correct": 0, "anticodon_total": 0,
              "discriminator_correct": 0, "discriminator_total": 0,
              "terminal_coordinate_correct": 0, "terminal_coordinate_total": 0}
    merged.update(metrics)
    return {
        "seqname": seqname,
        "status": "ok",
        "gold_evaluation_allowed": scored,
        "base_preserved": True,
        "gold_labels": list(gold if gold is not None else labels),
        "metrics": merged,
        "csv_content": "\n".join(
            [numbering, ",".join(["-"] * len(labels)), ",".join(bases), ""]
        ),
    }


def test_region_boundaries_match_the_methods_text():
    region_for = SUMMARY.region_for
    assert region_for("-1") == "Acceptor 5'"
    assert region_for("-1i1") == "Acceptor 5'"
    assert region_for("17a") == "D" and region_for("26") == "D"
    assert region_for("27") == "Anticodon" and region_for("43") == "Anticodon"
    assert region_for("44") == "Variable" and region_for("V12") == "Variable"
    assert region_for("49") == "T" and region_for("65") == "T"
    assert region_for("66") == "Acceptor 3'" and region_for("76") == "Acceptor 3'"


def test_position_counts_score_by_index_not_by_label_lookup():
    cases = {
        "a": case("a", ["1", "2", "3"], ["A", "C", "G"]),
        "b": case("b", ["1", "3"], ["A", "C"], gold=["1", "2", "3"],
                  label_correct=1, exact_match=False),
    }
    counts = SUMMARY.position_counts(cases)
    assert counts["1"] == {"total": 2, "correct": 2}
    assert counts["2"] == {"total": 2, "correct": 1}
    assert counts["3"] == {"total": 2, "correct": 1}


def test_summary_and_case_tables_report_paired_deltas(tmp_path):
    main_cases = {
        "a": case("a", ["1", "2", "3"], ["A", "C", "G"]),
        "b": case("b", ["1", "2", "4"], ["A", "C", "G"], gold=["1", "2", "3"],
                  label_correct=2, exact_match=False),
        "x": case("x", ["1"], ["A"], scored=False),
    }
    base_cases = {
        "a": case("a", ["1", "2", "3"], ["A", "C", "G"]),
        "b": case("b", ["1", "3", "4"], ["A", "C", "G"], gold=["1", "2", "3"],
                  label_correct=1, exact_match=False),
        "x": case("x", ["1"], ["A"], scored=False),
    }
    main_positions = SUMMARY.position_counts(main_cases)
    base_positions = SUMMARY.position_counts(base_cases)
    rows = [
        SUMMARY.summary_row("检索+DP", list(base_cases.values()), base_positions),
        SUMMARY.summary_row("完整流程", list(main_cases.values()), main_positions),
    ]
    assert rows[0]["n_total"] == 3 and rows[0]["paired_ok"] == 3
    # The unscored case stays out of every accuracy denominator.
    assert rows[0]["residue_label_accuracy"] == pytest.approx(4 / 6)
    assert rows[1]["residue_label_accuracy"] == pytest.approx(5 / 6)

    case_rows = []
    for seqname in sorted(main_cases):
        if not main_cases[seqname].get("gold_evaluation_allowed", True):
            continue
        total = (main_cases[seqname]["metrics"])["label_total"]
        full = main_cases[seqname]["metrics"]["label_correct"] / total
        base = base_cases[seqname]["metrics"]["label_correct"] / total
        case_rows.append({
            "seqname": seqname,
            "label_total": total,
            "delta_label_accuracy": full - base,
            "change": "improved" if full > base else ("worsened" if full < base else "unchanged"),
        })
    assert [row["change"] for row in case_rows] == ["unchanged", "improved"]
