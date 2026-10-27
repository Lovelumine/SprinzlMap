import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "benchmark/scripts/retry_numbering_technical_failures.py"
SPEC = importlib.util.spec_from_file_location("numbering_campaign", SCRIPT)
CAMPAIGN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAMPAIGN)


class StubSourceStore:
    """The MySQL/MinIO handle the controller uses to inspect the source run."""

    def __init__(self, manifest: dict, seqnames):
        self._manifest = json.dumps(manifest).encode("utf-8")
        self._seqnames = list(seqnames)

    def get_artifact(self, name):
        return self._manifest if name == "manifest.json" else None

    def case_rows(self, *, run_id=None, seqnames=None, status="", active_answer=True, columns=None):
        # Progress and roster checks read narrow, ungraded rows; grading happens
        # only when a score is being reported.
        assert active_answer is False
        return [{"seqname": name, "status": "ok", "error": "", "technical_issue_count": 0}
                for name in self._seqnames]


class StubProgressStore:
    """Answers the live progress query the controller polls every pass."""

    rows = []

    @classmethod
    def readonly(cls):
        return cls()

    def case_rows(self, *, run_id=None, seqnames=None, status="", active_answer=True, columns=None):
        assert active_answer is False, "a progress poll must not re-grade the whole cohort"
        return list(StubProgressStore.rows)


def _cache(tmp_path, monkeypatch):
    cache = tmp_path / ".runtime" / "runs"
    monkeypatch.setattr(CAMPAIGN, "default_run_cache_dir", lambda: cache)
    return cache


def _inputs(tmp_path, monkeypatch, sequence="AC"):
    manifest = {"input_files": {"gold": {"sha256": "older-reference"}}}
    store = StubSourceStore(manifest, ["sample"])
    monkeypatch.setattr(CAMPAIGN, "connect_store", lambda run_id, create_schema=True: store)
    monkeypatch.setattr(CAMPAIGN, "BenchmarkRunStore", StubProgressStore)
    monkeypatch.setattr(
        CAMPAIGN, "load_case_jsons",
        lambda run_id, names: {"sample": {"seqname": "sample", "input_seq": sequence}},
    )
    StubProgressStore.rows = []

    gold = tmp_path / "reference.tsv"
    gold.write_text("seqname\t1\t2\nsample\tA\tC\n")
    names = tmp_path / "names.txt"
    names.write_text("sample\n")
    config = tmp_path / "runtime.json"
    config.write_text("{}")
    metadata = tmp_path / "metadata.jsonl"
    metadata.write_text(json.dumps({"schema_version": "3.1", "representative_seqname": "sample", "gold_evaluation_allowed": False}) + "\n")
    monkeypatch.setattr(CAMPAIGN, "ROOT", tmp_path)
    _cache(tmp_path, monkeypatch)
    return [str(SCRIPT), "--input-tsv", str(gold), "--runtime-config", str(config),
            "--source-run", "original", "--expected-cases", "1", "--seqnames-file", str(names),
            "--run-id", "fresh", "--submission-manifest", str(metadata)]


def test_full_rerun_accepts_updated_answers_but_reruns_original_inputs(tmp_path, monkeypatch):
    argv = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", [*argv, "--full-rerun"])
    cache = tmp_path / ".runtime" / "runs"
    commands = []

    class FinishedRunner:
        pid = 123
        returncode = 0

        def __init__(self, command, cwd):
            commands.append(command)
            # The runner writes its own index rows; the controller only reads them.
            StubProgressStore.rows = [
                {"seqname": "sample", "status": "ok", "error": "", "technical_issue_count": 0}
            ]

        def poll(self):
            return 0

    monkeypatch.setattr(CAMPAIGN.subprocess, "Popen", FinishedRunner)

    assert CAMPAIGN.main() == 0
    assert len(commands) == 1
    assert "--strict-llm-failures" in commands[0]
    assert "--submission-manifest" in commands[0]
    assert "--rerun-all" not in commands[0]
    assert "--keep-local" not in commands[0]
    status = json.loads((cache / "fresh" / "retry_status.json").read_text())
    assert status["status"] == "completed" and status["ok"] == 1
    assert not (cache / "fresh" / "cases").exists()


def test_full_rerun_rejects_unintentionally_changed_input_sequence(tmp_path, monkeypatch):
    argv = _inputs(tmp_path, monkeypatch, sequence="AG")
    monkeypatch.setattr(sys, "argv", [*argv, "--full-rerun"])
    with pytest.raises(ValueError, match="Input sequence changed"):
        CAMPAIGN.main()
    assert not (tmp_path / ".runtime" / "runs" / "fresh").exists()


def test_technical_retry_still_requires_the_original_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", _inputs(tmp_path, monkeypatch))
    with pytest.raises(ValueError, match="Input differs"):
        CAMPAIGN.main()


def test_index_rows_with_technical_issues_count_as_failures(tmp_path, monkeypatch):
    argv = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", [*argv, "--full-rerun", "--max-passes", "1"])
    StubProgressStore.rows = [
        {"seqname": "sample", "status": "ok", "error": "", "technical_issue_count": 2}
    ]

    class FinishedRunner:
        pid = 1
        returncode = 0

        def __init__(self, command, cwd):
            pass

        def poll(self):
            return 0

    monkeypatch.setattr(CAMPAIGN.subprocess, "Popen", FinishedRunner)

    assert CAMPAIGN.main() == 1
    status = json.loads((tmp_path / ".runtime" / "runs" / "fresh" / "retry_status.json").read_text())
    assert status["status"] == "exhausted" and status["errors"] == 1
    assert status["failure_examples"][0]["technical_issue_count"] == 2
