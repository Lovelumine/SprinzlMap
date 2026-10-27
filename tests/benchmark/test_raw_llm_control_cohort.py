"""The raw-LLM control must sit on the same cohort as the run it is compared with."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "APP"))
sys.path.insert(0, str(REPO / "benchmark" / "scripts"))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load("raw_llm_control_runner", "benchmark/scripts/run_numbering_benchmark.py")
control = _load("raw_llm_control", "benchmark/scripts/raw_llm_numbering_baseline.py")

RECORDS = [
    ("tdbD00000001|AAA001", "Bacteria", True, "baseline", None),
    ("tdbD00000390|DD1150", "Bacteria", False, "excluded", "atypical_tRNA"),
]


def _manifest(tmp_path: Path) -> Path:
    lines = []
    for name, source, allowed, status, reason in RECORDS:
        lines.append(
            json.dumps(
                {
                    "schema_version": "3.1",
                    "representative_seqname": name,
                    "cohort_source": "classic_three_domain",
                    "metadata": {"broad_source": source},
                    "gold_evaluation_allowed": allowed,
                    "curation_status": status,
                    "exclusion_reason": reason,
                }
            )
        )
    path = tmp_path / "case_manifest.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_control_shares_the_runner_manifest_reader(tmp_path: Path) -> None:
    """The control used to carry its own reader keyed on ``seqname``.

    The manifest identifies a case by ``representative_seqname``, so that reader
    matched nothing: a reviewer-excluded sequence stayed in the denominator and
    the cohort source metadata was dropped.
    """
    assert not hasattr(control, "submission_metadata")
    manifest = _manifest(tmp_path)
    flags = runner.load_submission_case_metadata(manifest)
    assert flags["tdbD00000390|DD1150"]["gold_evaluation_allowed"] is False
    assert flags["tdbD00000001|AAA001"]["metadata"]["broad_source"] == "Bacteria"


def test_excluded_sequence_stays_out_of_the_control_denominator(tmp_path: Path) -> None:
    """A reviewer exclusion must survive into the control's scoring, as it does in the main run."""
    gold = tmp_path / "reference.tsv"
    gold.write_text(
        "seqname\t1\t2\t73\t74\t75\t76\n"
        "tdbD00000001|AAA001\tA\tU\tA\tC\tC\tA\n"
        "tdbD00000390|DD1150\tA\tU\tA\tC\tC\tA\n",
        encoding="utf-8",
    )
    flags = runner.load_submission_case_metadata(_manifest(tmp_path))
    by_name = {case.seqname: case for case in runner.load_gold_cases(gold, case_metadata=flags)}
    assert by_name["tdbD00000390|DD1150"].benchmark_meta["gold_evaluation_allowed"] is False
    assert by_name["tdbD00000001|AAA001"].benchmark_meta["gold_evaluation_allowed"] is True
