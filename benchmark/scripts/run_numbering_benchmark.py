#!/usr/bin/env python3
"""Run the formal single-sequence Sprinzl numbering benchmark.

The runner is designed around per-case atomic outputs so it can resume safely
without wasting model calls after interruptions.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import logging
import os
import shlex
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "APP"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.agent import AgentAlignmentService, AlignmentRequest, RootSnapshot
from backend.agent.evaluation.hard_guards import HardGuards
from backend.agent.orchestrator import FixedActionPlanner
from backend.domain.alignment.runtime_helpers import runtime_int
from backend.domain.benchmarking.protocol import (
    PROTOCOL_GROUP_FIELDS,
    SPLIT_NAMES,
    digest_text,
    load_case_manifest,
    select_manifest_cases,
    sha256_file,
)
from backend.domain.benchmarking.publication_protocol import terminal_reference_category
from backend.domain.benchmarking.case_index import (
    attach_case_comparison,
    case_traces_tsv_text,
    count_cm_tool_calls,
    gold_fingerprint,
)
from backend.domain.alignment.cm_numbering import (
    CmalignNumberingService,
    model_group_for_source,
)
from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.engine.scoring import required_alignment_backend_provenance
from backend.domain.common.trnascan_runtime import (
    TRNASCAN_SENSITIVE_OPTIONS,
    trnascan_runtime_provenance,
)
from backend.infra.config.runtime_settings import get_effective_runtime_settings
from backend.infra.storage.benchmark_run_store import (
    BenchmarkStoreError,
    connect_store,
    default_run_cache_dir,
)
from backend.infra.llm.json_client import (
    RETRYABLE_HTTP_STATUSES,
    ROTATABLE_CREDENTIAL_HTTP_STATUSES,
    StructuredJsonLLMClient,
)
from backend.infra.llm.provider_config import (
    MAX_COMPLETION_TOKENS,
    load_provider_runtime_config,
    openai_chat_completions_url,
)
from backend.infra.llm.usage_tracker import (
    get_usage_summary,
    reset_usage_context,
    summarize_usage_calls,
)


LOG = logging.getLogger("run_numbering_benchmark")
THREAD_LOCAL = threading.local()
HARD_GUARDS = HardGuards()
VALID_BASES = {"A", "C", "G", "U", "N"}


@dataclass(frozen=True)
class GoldCase:
    seqname: str
    input_seq: str
    gold_labels: Tuple[str, ...]
    gold_bases: Tuple[str, ...]
    benchmark_meta: Dict[str, Any] = field(default_factory=dict)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--method-backend",
        choices=("agent", "cm"),
        default="agent",
        help="Numbering implementation evaluated by the shared benchmark contract.",
    )
    parser.add_argument(
        "--cm-model-root",
        type=Path,
        default=Path("research/third_party/trnaviz/engine"),
        help="Directory containing the vendored Sprinzl-numbering CM files.",
    )
    parser.add_argument(
        "--cmalign",
        type=str,
        default="cmalign",
        help="Infernal cmalign executable used by the CM backend.",
    )
    parser.add_argument(
        "--cm-cpu",
        type=int,
        default=1,
        help="Infernal CPU threads per CM benchmark case.",
    )
    parser.add_argument(
        "--input-tsv",
        type=Path,
        default=None,
        help="Benchmark dataset TSV.  Use --gold-from-store instead to read trna_gold.",
    )
    parser.add_argument(
        "--gold-from-store",
        action="store_true",
        help=(
            "Take the answers from the MySQL trna_gold table and freeze that "
            "snapshot into the run inputs, instead of pointing at a gold TSV"
        ),
    )
    parser.add_argument(
        "--case-manifest",
        type=Path,
        default=None,
        help="Frozen JSONL case manifest used to select an evaluation split.",
    )
    parser.add_argument(
        "--submission-manifest",
        type=Path,
        default=None,
        help=(
            "Submission-v2 or publication-v3 JSONL manifest whose representative "
            "records are attached to cases selected by --seqnames-file."
        ),
    )
    parser.add_argument(
        "--protocol",
        choices=sorted(PROTOCOL_GROUP_FIELDS),
        default="unique_sequence",
        help="Manifest split protocol.",
    )
    parser.add_argument(
        "--split",
        choices=SPLIT_NAMES,
        default="test",
        help="Manifest split to evaluate.",
    )
    parser.add_argument(
        "--template-overlap-view",
        choices=("all", "visible", "novel"),
        default="all",
        help="Optionally restrict manifest cases by exact template visibility.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default="deepseek-numbering-benchmark",
        help="Run identifier recorded in MySQL and MinIO.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="With --keep-local: where the run copy is written. Defaults to "
             ".runtime/runs/<run_id>/, which git ignores.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=64,
        help="Maximum concurrent benchmark case workers.",
    )
    parser.add_argument(
        "--llm-global-max-workers",
        type=int,
        default=1000,
        help="Maximum concurrent LLM dispatcher workers.",
    )
    parser.add_argument(
        "--llm-max-inflight",
        type=int,
        default=0,
        help="Optional process-local LLM in-flight cap. 0 means no cap.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on number of cases.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N rows before benchmarking.",
    )
    parser.add_argument(
        "--seqnames-file",
        type=Path,
        default=None,
        help="Optional text file listing seqnames to include.",
    )
    parser.add_argument("--strict-llm-failures", action="store_true",
                        help="Treat planner API/JSON/allowlist failures as case errors instead of rule fallbacks.")
    parser.add_argument(
        "--runtime-config", type=Path, default=None,
        help="Private runtime JSON overrides (credentials are never copied into the manifest).",
    )
    parser.add_argument(
        "--rerun-failed",
        action="store_true",
        help="Compatibility flag; non-ok checkpoints are retried by default.",
    )
    parser.add_argument(
        "--rerun-all",
        action="store_true",
        help=(
            "Explicitly invalidate all existing case checkpoints and rerun everything. "
            "Use --resume after an interruption."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume from the existing per-case checkpoints. This takes precedence "
            "over --rerun-all and retries missing or non-ok cases only."
        ),
    )
    parser.add_argument(
        "--llm-reasoning",
        action="store_true",
        help="Enable llm_reasoning for the alignment pipeline.",
    )
    parser.add_argument(
        "--agent-enable-planning",
        action="store_true",
        help="Run the Agent planner even when --no-llm is set.",
    )
    parser.add_argument(
        "--agent-planner-policy",
        choices=("llm", "fixed"),
        default="llm",
        help="Action selector policy. Both policies use the same Harness action allowlist.",
    )
    parser.add_argument(
        "--agent-llm-triage-mode",
        choices=("uncertain_only", "dual_trigger", "always"),
        default="uncertain_only",
        help=(
            "Use hard-conflict-only planning, dual hard/soft-risk review, "
            "or call the model for every planning decision."
        ),
    )
    parser.add_argument(
        "--agent-soft-risk-min-cm-posterior",
        type=float,
        default=0.995,
        help=(
            "In dual-trigger mode, review otherwise legal CM candidates below "
            "this average-posterior threshold."
        ),
    )
    parser.add_argument(
        "--agent-soft-risk-min-cross-backend-score-gain",
        type=float,
        default=51.0,
        help=(
            "Minimum deterministic score gain required before a valid template/"
            "regional-DP candidate may replace a valid CM candidate."
        ),
    )
    parser.add_argument(
        "--agent-soft-risk-review-insertions",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Review otherwise legal CM candidates containing insertion states.",
    )
    parser.add_argument(
        "--agent-soft-risk-review-broad-cm",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Review otherwise legal candidates produced by the all-num CM.",
    )
    parser.add_argument(
        "--agent-soft-risk-review-local-coordinate-ambiguity",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Review CM position-47 ambiguity only when a neighboring insertion "
            "state supplies independent evidence, without forcing occupancy."
        ),
    )
    parser.add_argument(
        "--agent-soft-risk-review-acceptor-tail",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Review CM acceptor-arm endpoint disagreements using tRNAscan-SE "
            "secondary-structure evidence."
        ),
    )
    parser.add_argument(
        "--agent-local-ambiguity-reference-top-k",
        type=int,
        default=5,
        help="Maximum distinct reference templates used for local ambiguity review.",
    )
    parser.add_argument(
        "--agent-local-ambiguity-min-reference-support",
        type=int,
        default=2,
        help=(
            "Minimum distinct valid templates that must assign the same input "
            "residue to position 47."
        ),
    )
    parser.add_argument(
        "--agent-soft-risk-min-local-resolution-score-gain",
        type=float,
        default=0.1,
        help=(
            "Minimum score gain after distinct templates agree on a local "
            "position-47 resolution."
        ),
    )
    parser.add_argument(
        "--agent-memory-mode",
        choices=("off", "read_only"),
        default="off",
        help="Use no experience memory or a frozen read-only development snapshot.",
    )
    parser.add_argument(
        "--agent-memory-path",
        type=Path,
        default=Path("data/agent_memory/patterns.v2.jsonl"),
        help="Frozen structured experience snapshot used by --agent-memory-mode read_only.",
    )
    postcheck_group = parser.add_mutually_exclusive_group()
    postcheck_group.add_argument(
        "--agent-enable-llm-postcheck",
        dest="agent_enable_llm_postcheck",
        action="store_true",
        help=(
            "Enable explicit regional LLM candidate actions. Disabled by default "
            "for the scientific planner-only configuration."
        ),
    )
    postcheck_group.add_argument(
        "--agent-disable-llm-postcheck",
        dest="agent_enable_llm_postcheck",
        action="store_false",
        help=(
            "Deprecated compatibility spelling; regional LLM candidate actions are "
            "already disabled by default."
        ),
    )
    parser.set_defaults(agent_enable_llm_postcheck=False)
    parser.add_argument(
        "--agent-disable-deterministic-boundary-refinement",
        "--agent-disable-llm-boundary-refinement",
        dest="agent_disable_deterministic_boundary_refinement",
        action="store_true",
        help=(
            "Disable the deterministic multi-evidence boundary tool. The legacy "
            "--agent-disable-llm-boundary-refinement spelling is retained as an alias."
        ),
    )
    parser.add_argument(
        "--agent-enable-cm-tool",
        action="store_true",
        help="Expose the experimental numbering-CM portfolio action to the Agent planner.",
    )
    parser.add_argument(
        "--agent-enable-cm-root-recovery",
        action="store_true",
        help="Legacy-only compatibility flag for guarded CM recovery after root failure.",
    )
    parser.add_argument(
        "--agent-canonical-root-strategy",
        choices=("agent_toolbox", "cm_first", "cm_only", "legacy_only"),
        default="agent_toolbox",
        help="Initial scientific-tool policy used by the Agent Harness.",
    )
    parser.add_argument(
        "--agent-literature-tool",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Let the Agent request a bounded reference lookup as an evidence-only "
            "action. The lookup never changes a numbering candidate."
        ),
    )
    parser.add_argument(
        "--agent-numbering-manual",
        choices=("full", "brief", "off"),
        default="brief",
        help=(
            "How much of the Sprinzl coordinate reference the planner prompt "
            "carries: the whole manual, the short digest, or neither. The "
            "coordinate-proposal action always sees the whole manual, because "
            "that is the one action where the model writes labels. 'off' "
            "reproduces the bare tool-selection policy."
        ),
    )
    parser.add_argument(
        "--agent-literature-max-notes",
        type=int,
        default=4,
        help="Reference notes the Agent reads back after a literature lookup.",
    )
    parser.add_argument(
        "--agent-coordinate-proposal",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Let the Agent ask the model to rewrite the flagged regions of a "
            "numbering row. The proposal passes the same hard guards and "
            "scoring as a tool candidate, and is offered after every "
            "deterministic repair, so an off run is the current architecture."
        ),
    )
    parser.add_argument(
        "--agent-planner-max-tokens",
        type=int,
        default=6144,
        help=(
            "Completion budget for one tool-selection call. The answer is three "
            "short fields, but the gateway reasons before it writes, so this cap "
            "is what bounds that reasoning. A call that stops at the cap is "
            "retried once with three times the room rather than lost."
        ),
    )
    parser.add_argument(
        "--agent-proposal-max-tokens",
        type=int,
        default=6144,
        help="Completion budget for one coordinate-proposal call.",
    )
    parser.add_argument(
        "--agent-escalation-requires-attempt",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Refuse one punt: when the Agent asks to investigate or abstain "
            "while a repair it was offered has not been tried, ask the same "
            "round again with only the tools available. Without this, half the "
            "cohort ends after a single decision and no capability added to the "
            "Agent can be exercised."
        ),
    )
    parser.add_argument(
        "--agent-disable-legacy-fallback",
        action="store_true",
        help="Do not request the deterministic legacy reference when CM is unaccepted.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM/Agent postcheck for ablation runs.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="deepseek",
        help="LLM provider override.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="deepseekv4-flash-guangdong",
        help="Model override.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="Progress log interval.",
    )
    parser.add_argument(
        "--notes",
        type=str,
        default="",
        help="Optional free-text notes stored in manifest.",
    )
    parser.add_argument(
        "--keep-local",
        action="store_true",
        help=(
            "Also write per-case JSON and run artifacts under --out-dir. "
            "By default a run lives only in MySQL (index) and MinIO (traces)."
        ),
    )
    parser.add_argument(
        "--no-store",
        action="store_true",
        help=(
            "Skip MySQL and MinIO. Requires --keep-local; intended for local "
            "development smoke tests, never for a published run."
        ),
    )
    return parser.parse_args()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def git_commit() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=PROJECT_ROOT,
                text=True,
            )
            .strip()
        )
    except Exception:
        return ""


def git_worktree_dirty() -> Optional[bool]:
    """Check scientific code and resources, excluding generated run outputs.

    New checkpoints must not invalidate their own resume contract.
    """
    try:
        output = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all", "--",
             "APP/backend", "benchmark/scripts", "data/tRNAs_flat.tsv",
             "research/third_party/trnaviz/engine"],
            cwd=PROJECT_ROOT,
            text=True,
        )
        return bool(output.strip())
    except Exception:
        return None


def setup_logging(log_path: Path) -> None:
    """Log to stdout, to a file when the run keeps a local directory, and to
    an in-memory buffer so the log can be uploaded as a run artifact."""
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    handler = _BufferedLogHandler()
    handlers.append(handler)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )
    return handler


class _BufferedLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: List[str] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return
        with self._lock:
            self.lines.append(message)

    def text(self) -> str:
        with self._lock:
            return "\n".join(self.lines) + "\n" if self.lines else ""


def sanitize_base(raw: str) -> str:
    base = str(raw or "").strip().upper().replace("T", "U")
    return base if base in VALID_BASES else "-"


def safe_region(label: str) -> str:
    try:
        return number_to_region(str(label or "").strip())
    except Exception:
        return ""


def parse_csv_alignment(csv_raw: str) -> Tuple[List[str], List[str], List[str]]:
    try:
        rows = list(csv.reader(io.StringIO(csv_raw)))
    except Exception:
        return [], [], []
    if len(rows) < 3:
        return [], [], []
    return list(rows[0]), list(rows[1]), list(rows[2])


def residue_label_sequence(numbering_row: Sequence[str], sequence_row: Sequence[str]) -> Tuple[List[str], str]:
    labels: List[str] = []
    bases: List[str] = []
    for label, base in zip(numbering_row, sequence_row):
        norm = sanitize_base(base)
        if norm == "-":
            continue
        labels.append(str(label or "").strip())
        bases.append(norm)
    return labels, "".join(bases)


def load_seqname_filter(path: Optional[Path]) -> Optional[set[str]]:
    if path is None:
        return None
    names = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    return names or None


def load_submission_case_metadata(path: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid submission manifest JSON at {path}:{line_number}"
                ) from exc
            seqname = str(payload.get("representative_seqname") or "").strip()
            if not seqname or seqname in records:
                raise ValueError(
                    f"Missing or duplicate representative seqname at {path}:{line_number}"
                )
            if str(payload.get("schema_version") or "") not in {"2.0", "3.0", "3.1"}:
                raise ValueError(
                    f"Unsupported submission manifest schema at {path}:{line_number}"
                )
            records[seqname] = dict(payload)
    if not records:
        raise ValueError(f"Submission manifest is empty: {path}")
    return records


def load_gold_provenance(input_tsv: Path) -> Dict[str, Dict[str, Any]]:
    """Per-sequence answer metadata that travels with a staged gold table.

    ``stage_gold_from_store.py`` writes ``reference.provenance.json`` beside the
    reference TSV, so a stored score can name the exact answer row it used.
    """
    sidecar = input_tsv.with_name(f"{input_tsv.stem}.provenance.json")
    if not sidecar.exists():
        return {}
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for seqname, meta in dict(payload.get("sequences") or {}).items():
        if isinstance(meta, dict):
            out[str(seqname)] = dict(meta)
    return out


def load_gold_cases(
    input_tsv: Path,
    *,
    offset: int = 0,
    limit: Optional[int] = None,
    seqname_filter: Optional[set[str]] = None,
    case_metadata: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> List[GoldCase]:
    cases: List[GoldCase] = []
    gold_provenance = load_gold_provenance(input_tsv)
    with input_tsv.open("r", encoding="utf-8", newline="") as fin:
        reader = csv.reader(fin, delimiter="\t")
        header = next(reader, None)
        if not header or header[0] != "seqname":
            raise ValueError(f"Unexpected benchmark header in {input_tsv}")
        numbering = header[1:]

        for idx, row in enumerate(reader):
            if idx < max(0, offset):
                continue
            seqname = str(row[0] or "").strip()
            if not seqname:
                continue
            if seqname_filter is not None and seqname not in seqname_filter:
                continue
            bases = [sanitize_base(base) for base in row[1:]]
            gold_labels = [num for num, base in zip(numbering, bases) if base != "-"]
            gold_bases = [base for base in bases if base != "-"]
            input_seq = "".join(gold_bases)
            benchmark_meta = dict((case_metadata or {}).get(seqname) or {})
            benchmark_meta["terminal_reference_category"] = terminal_reference_category(
                dict(zip(numbering, bases))
            )
            stored = gold_provenance.get(seqname) or {}
            # The answer fingerprint makes a stored score attributable: it says
            # which version of the gold standard this case was graded against.
            benchmark_meta["gold_labels_sha256"] = str(
                stored.get("gold_sha256") or ""
            ) or gold_fingerprint(gold_labels, gold_bases)
            if stored:
                benchmark_meta["gold_source"] = str(stored.get("table") or "trna_gold")
                benchmark_meta["correction_decision"] = str(stored.get("gold_source") or "")
                if "evaluation_allowed" in stored:
                    benchmark_meta["gold_evaluation_allowed"] = bool(stored["evaluation_allowed"])
            cases.append(
                GoldCase(
                    seqname=seqname,
                    input_seq=input_seq,
                    gold_labels=tuple(gold_labels),
                    gold_bases=tuple(gold_bases),
                    benchmark_meta=benchmark_meta,
                )
            )
            if limit is not None and len(cases) >= limit:
                break
    return cases


def case_digest(seqname: str) -> str:
    return hashlib.sha1(seqname.encode("utf-8")).hexdigest()[:16]


def case_result_path(cases_dir: Path, seqname: str) -> Path:
    return cases_dir / f"{case_digest(seqname)}.json"


def read_case_result(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(raw_tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def stored_result_is_reusable(seqname: str, existing: Optional[Mapping[str, Any]]) -> bool:
    """A complete success with no transport failure is the only reusable checkpoint.

    Works for both a stored case trace and a case row of the execution table, so
    a resume applies the same rule whether it reads MySQL and MinIO or a local
    directory left over from an older run.
    """
    if existing is None:
        return False
    if str(existing.get("status") or "") != "ok":
        return False
    if str(existing.get("seqname") or "").strip() != str(seqname).strip():
        return False
    if "technical_issue_count" in existing:
        return int(existing.get("technical_issue_count") or 0) == 0
    return not llm_technical_issues(dict(existing))


def should_run_case(
    seqname: str,
    existing: Optional[Mapping[str, Any]],
    *,
    rerun_all: bool,
    rerun_failed: bool,
) -> bool:
    if rerun_all:
        return True
    if existing is None:
        return True
    if stored_result_is_reusable(seqname, existing):
        return False
    return True


def llm_technical_issues(result: Mapping[str, Any]) -> Dict[str, int]:
    """Detect handled API failures as well as explicit planner fallbacks.

    A failed attempt still counts when an internal retry later returns HTTP 200.
    Scientific tool rejections alone are not LLM transport failures.
    """
    failed_requests = 0
    for call in (result.get("llm_usage") or {}).get("calls", []):
        status = call.get("status_code")
        if str(status) != "200" or call.get("error_type"):
            failed_requests += 1
    fallback_decisions = 0
    for event in (result.get("agent_trace") or {}).get("trace_events", []):
        decision = event.get("agent_decision") or event
        if decision.get("planner_source") == "deterministic_fallback":
            fallback_decisions += 1
    decision_failures = sum(
        event.get("stage") == "llm_decision_failed"
        for event in result.get("llm_failure_events", [])
    )
    return {
        key: count for key, count in {
            "failed_requests": failed_requests,
            "planner_fallbacks": fallback_decisions,
            "decision_failures": decision_failures,
        }.items() if count
    }


def get_service(planner_policy: str = "llm") -> AgentAlignmentService:
    policy = str(planner_policy or "llm").strip().lower()
    service_key = f"service_{policy}"
    service = getattr(THREAD_LOCAL, service_key, None)
    if service is None:
        service = AgentAlignmentService(
            action_planner=(FixedActionPlanner() if policy == "fixed" else None)
        )
        setattr(THREAD_LOCAL, service_key, service)
    return service


def get_cm_service(args: argparse.Namespace) -> CmalignNumberingService:
    key = (str(args.cm_model_root), str(args.cmalign), int(args.cm_cpu))
    service = getattr(THREAD_LOCAL, "cm_service", None)
    if service is None or getattr(THREAD_LOCAL, "cm_service_key", None) != key:
        service = CmalignNumberingService(
            model_root=args.cm_model_root,
            executable=args.cmalign,
            cpu=args.cm_cpu,
        )
        THREAD_LOCAL.cm_service = service
        THREAD_LOCAL.cm_service_key = key
    return service


def cm_model_group_for_case(case: GoldCase) -> str:
    metadata = dict((case.benchmark_meta or {}).get("metadata") or {})
    return model_group_for_source(str(metadata.get("broad_source") or ""))


def biological_contract_for_case(case: GoldCase) -> Dict[str, Any]:
    metadata = dict((case.benchmark_meta or {}).get("metadata") or {})
    source = str(metadata.get("broad_source") or "unknown").strip().lower()
    allowed_sources = {
        "archaea",
        "bacteria",
        "cyanelle",
        "cytosolic_eukaryote",
        "chloroplast",
        "mitochondrial",
        "virus",
    }
    amino_acid = str(metadata.get("amino_acid") or "").strip().lower()
    return {
        "input_type": "mature",
        "domain_hint": source if source in allowed_sources else "unknown",
        "cca_status": "unknown",
        "intron_status": "unknown",
        "trna_class": (
            "selenocysteine"
            if amino_acid in {"sec", "selenocysteine"}
            else "canonical"
        ),
        "mitochondrial_subtype": "unknown",
    }


def build_runtime_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    runtime_values = dict(get_effective_runtime_settings() or {})
    runtime_config = getattr(args, "runtime_config", None)
    if runtime_config:
        private_values = json.loads(Path(runtime_config).read_text(encoding="utf-8"))
        if not isinstance(private_values, dict):
            raise ValueError("Runtime config must be a JSON object")
        runtime_values.update(private_values)
    local_reference_top_k = max(
        2,
        min(
            10,
            int(getattr(args, "agent_local_ambiguity_reference_top_k", 5)),
        ),
    )
    local_minimum_support = max(
        2,
        min(
            local_reference_top_k,
            int(getattr(args, "agent_local_ambiguity_min_reference_support", 2)),
        ),
    )
    overrides = dict(runtime_values)
    overrides.update(
        {
            "LLM_PROVIDER": args.provider,
            "AGENT_STRICT_LLM_FAILURES": bool(getattr(args, "strict_llm_failures", False)),
            "LLM_STREAM": True,
            "LLM_GLOBAL_MAX_WORKERS": int(args.llm_global_max_workers),
            "LLM_DISPATCHER_MAX_INFLIGHT": int(args.llm_max_inflight),
            "AGENT_CANONICAL_ROOT_STRATEGY": str(args.agent_canonical_root_strategy),
            "AGENT_LLM_TRIAGE_MODE": str(
                getattr(args, "agent_llm_triage_mode", "uncertain_only")
            ),
            "AGENT_SOFT_RISK_MIN_CM_POSTERIOR": max(
                0.0,
                min(
                    1.0,
                    float(
                        getattr(
                            args,
                            "agent_soft_risk_min_cm_posterior",
                            0.995,
                        )
                    ),
                ),
            ),
            "AGENT_SOFT_RISK_MIN_CROSS_BACKEND_SCORE_GAIN": max(
                0.1,
                min(
                    500.0,
                    float(
                        getattr(
                            args,
                            "agent_soft_risk_min_cross_backend_score_gain",
                            51.0,
                        )
                    ),
                ),
            ),
            "AGENT_SOFT_RISK_REVIEW_INSERTIONS": bool(
                getattr(args, "agent_soft_risk_review_insertions", True)
            ),
            "AGENT_SOFT_RISK_REVIEW_BROAD_CM": bool(
                getattr(args, "agent_soft_risk_review_broad_cm", True)
            ),
            "AGENT_SOFT_RISK_REVIEW_LOCAL_COORDINATE_AMBIGUITY": bool(
                getattr(args, "agent_soft_risk_review_local_coordinate_ambiguity", True)
            ),
            "AGENT_SOFT_RISK_REVIEW_ACCEPTOR_TAIL": bool(
                getattr(args, "agent_soft_risk_review_acceptor_tail", True)
            ),
            "AGENT_LOCAL_AMBIGUITY_REFERENCE_TOP_K": local_reference_top_k,
            "AGENT_LOCAL_AMBIGUITY_MIN_REFERENCE_SUPPORT": local_minimum_support,
            "AGENT_SOFT_RISK_MIN_LOCAL_RESOLUTION_SCORE_GAIN": max(
                0.1,
                min(
                    500.0,
                    float(
                        getattr(
                            args,
                            "agent_soft_risk_min_local_resolution_score_gain",
                            0.1,
                        )
                    ),
                ),
            ),
            "AGENT_MEMORY_MODE": str(getattr(args, "agent_memory_mode", "off")),
            "AGENT_MEMORY_PATH": str(
                getattr(args, "agent_memory_path", "data/agent_memory/patterns.v2.jsonl")
            ),
            "AGENT_MEMORY_REQUIRE_FROZEN": True,
            "AGENT_ENABLE_LEGACY_FALLBACK": not bool(
                args.agent_disable_legacy_fallback
            ),
            "AGENT_ENABLE_LLM_POSTCHECK": bool(
                getattr(args, "agent_enable_llm_postcheck", False)
            ),
            "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT": not bool(
                getattr(args, "agent_disable_deterministic_boundary_refinement", False)
            ),
            "AGENT_ENABLE_LITERATURE_TOOL": bool(
                getattr(args, "agent_literature_tool", True)
            ),
            "AGENT_NUMBERING_MANUAL": str(
                getattr(args, "agent_numbering_manual", "brief")
            ),
            "AGENT_LITERATURE_MAX_NOTES": max(
                1,
                min(
                    8,
                    int(getattr(args, "agent_literature_max_notes", 4) or 4),
                ),
            ),
            "AGENT_ENABLE_COORDINATE_PROPOSAL": bool(
                getattr(args, "agent_coordinate_proposal", False)
            ),
            "AGENT_PLANNER_MAX_TOKENS": max(
                128, int(getattr(args, "agent_planner_max_tokens", 2048) or 2048)
            ),
            "AGENT_PROPOSAL_MAX_TOKENS": max(
                128, int(getattr(args, "agent_proposal_max_tokens", 4096) or 4096)
            ),
            "AGENT_ESCALATION_REQUIRES_ATTEMPT": bool(
                getattr(args, "agent_escalation_requires_attempt", True)
            ),
        }
    )
    provider = str(args.provider or "").lower()
    model_key = {
        "deepseek": "DEEPSEEK_MODEL",
        "minimax": "MINIMAX_MODEL",
        "mimo": "MIMO_MODEL",
        "ollama": "OLLAMA_MODEL",
    }.get(provider)
    if model_key:
        overrides[model_key] = args.model
    if provider == "deepseek":
        deepseek_thinking = os.environ.get("DEEPSEEK_THINKING")
        if deepseek_thinking:
            overrides["DEEPSEEK_THINKING"] = deepseek_thinking
    if bool(args.agent_enable_planning):
        overrides["AGENT_ENABLE_PLANNING"] = True
    if bool(args.agent_enable_cm_tool):
        overrides["AGENT_ENABLE_CM_TOOL"] = True
    if bool(getattr(args, "agent_enable_cm_root_recovery", False)):
        overrides["AGENT_ENABLE_CM_ROOT_RECOVERY"] = True
    return overrides


def _sanitized_endpoint_url(raw_url: Any) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (
            parsed.scheme,
            host,
            parsed.path.rstrip("/"),
            "",
            "",
        )
    )


def build_llm_endpoint_provenance(runtime_overrides: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_provider_runtime_config(
        runtime_overrides,
        include_postcheck=False,
        include_max_tokens=True,
    )
    _, request_payload = StructuredJsonLLMClient._request_payload(
        cfg=cfg, messages=[], model_name=str(cfg.get("model_name") or ""),
        max_tokens=runtime_int(
            "AGENT_PLANNER_MAX_TOKENS",
            MAX_COMPLETION_TOKENS,
            128,
            MAX_COMPLETION_TOKENS,
            runtime_values=runtime_overrides,
        ),
    )
    base_url = _sanitized_endpoint_url(cfg.get("base_url"))
    completion_url = (
        _sanitized_endpoint_url(openai_chat_completions_url(base_url))
        if str(cfg.get("mode") or "") == "openai" and base_url
        else base_url
    )
    credential_endpoints = []
    for item in list(cfg.get("credentials") or []):
        endpoint = _sanitized_endpoint_url(dict(item).get("base_url"))
        if endpoint and endpoint not in credential_endpoints:
            credential_endpoints.append(endpoint)
    return {
        "provider": str(cfg.get("provider") or ""),
        "mode": str(cfg.get("mode") or ""),
        "model": str(cfg.get("model_name") or ""),
        "base_url": base_url,
        "completion_url": completion_url,
        "credential_endpoints": credential_endpoints,
        "api_key_configured": bool(str(cfg.get("api_key") or "")),
        "credential_pool_size": len(list(cfg.get("api_keys") or [])) or int(
            bool(str(cfg.get("api_key") or ""))
        ),
        "verify_ssl": bool(cfg.get("verify_ssl", True)),
        "request_parameters": {
            key: request_payload[key]
            for key in ("max_tokens", "max_completion_tokens", "stream", "thinking")
            if key in request_payload
        },
        "request_policy": {
            "retryable_http_statuses": sorted(RETRYABLE_HTTP_STATUSES),
            "credential_rotation_http_statuses": sorted(
                ROTATABLE_CREDENTIAL_HTTP_STATUSES
            ),
            "max_retries": int(cfg.get("max_retries") or 0),
            "backoff_base_s": float(cfg.get("backoff_base_s") or 0.0),
            "min_request_interval_s": float(
                cfg.get("min_request_interval_s") or 0.0
            ),
            "hard_budget_s": float(cfg.get("hard_budget_s") or 0.0),
            "connect_timeout_s": float(cfg.get("timeout_connect") or 0.0),
            "read_timeout_s": float(cfg.get("timeout_read") or 0.0),
        },
    }


def with_case_usage_context(
    runtime_overrides: Dict[str, Any],
    *,
    run_id: str,
    case: GoldCase,
) -> Tuple[str, Dict[str, Any]]:
    usage_case_id = case_digest(case.seqname)
    case_overrides = dict(runtime_overrides)
    case_overrides.update(
        {
            "LLM_USAGE_RUN_ID": str(run_id),
            "LLM_USAGE_CASE_ID": usage_case_id,
            "LLM_USAGE_SEQNAME": case.seqname,
        }
    )
    return usage_case_id, case_overrides


def subset_metrics(
    gold_labels: Sequence[str],
    pred_labels: Sequence[str],
    predicate,
) -> Tuple[int, int, Optional[bool]]:
    idxs = [idx for idx, label in enumerate(gold_labels) if predicate(label)]
    total = len(idxs)
    if total == 0:
        return 0, 0, None
    correct = 0
    for idx in idxs:
        pred = pred_labels[idx] if idx < len(pred_labels) else None
        if pred == gold_labels[idx]:
            correct += 1
    return total, correct, correct == total


def evaluate_case(
    case: GoldCase,
    *,
    template_name: str,
    csv_content: str,
    warnings: Sequence[str],
    meta: Dict[str, Any],
    use_llm: bool,
    llm_reasoning: bool,
    runtime_overrides: Dict[str, Any],
    elapsed_sec: float,
) -> Dict[str, Any]:
    numbering_row, template_row, sequence_row = parse_csv_alignment(csv_content)
    pred_labels, pred_seq = residue_label_sequence(numbering_row, sequence_row)
    gold_labels = list(case.gold_labels)
    gold_seq = case.input_seq
    gold_evaluation_allowed = bool(
        case.benchmark_meta.get("gold_evaluation_allowed", True)
    )

    residue_total = len(gold_labels) if gold_evaluation_allowed else 0
    label_correct = 0
    region_correct = 0
    if gold_evaluation_allowed:
        for idx, gold_label in enumerate(gold_labels):
            pred_label = pred_labels[idx] if idx < len(pred_labels) else None
            if pred_label == gold_label:
                label_correct += 1
            if pred_label is not None and safe_region(pred_label) == safe_region(gold_label):
                region_correct += 1

        anticodon_total, anticodon_correct, anticodon_exact = subset_metrics(
            gold_labels,
            pred_labels,
            lambda label: label in {"34", "35", "36"},
        )
        cca_total, cca_correct, cca_exact = subset_metrics(
            gold_labels,
            pred_labels,
            lambda label: label in {"73", "74", "75", "76"},
        )
        discriminator_total, discriminator_correct, discriminator_exact = subset_metrics(
            gold_labels,
            pred_labels,
            lambda label: label == "73",
        )
        terminal_total, terminal_correct, terminal_exact = subset_metrics(
            gold_labels,
            pred_labels,
            lambda label: label in {"74", "75", "76"},
        )
        variable_total, variable_correct, variable_exact = subset_metrics(
            gold_labels,
            pred_labels,
            lambda label: safe_region(label) == "Variable loop",
        )
    else:
        anticodon_total = anticodon_correct = 0
        cca_total = cca_correct = 0
        discriminator_total = discriminator_correct = 0
        terminal_total = terminal_correct = 0
        variable_total = variable_correct = 0
        anticodon_exact = cca_exact = discriminator_exact = terminal_exact = variable_exact = None

    snapshot = RootSnapshot(
        template_name=template_name,
        csv_content=csv_content,
        warnings=list(warnings or []),
        numbering_row=numbering_row,
        template_row=template_row,
        sequence_row=sequence_row,
        structure_view=dict(meta.get("structure_view") or {}),
        llm_trace_view=dict(meta.get("llm_trace_view") or {}),
        raw_meta=dict(meta or {}),
    )
    request = AlignmentRequest(
        target_seq=gold_seq,
        output_csv_path="/dev/null",
        anticode="",
        use_llm=bool(use_llm),
        llm_reasoning=bool(llm_reasoning),
        template_name=None,
        **biological_contract_for_case(case),
        return_meta=True,
        runtime_values_override=dict(runtime_overrides or {}),
    )
    guard = HARD_GUARDS.validate(request, snapshot)
    guard_challenge_audit = evaluate_guard_challenges(request, snapshot, guard)
    agent_meta = dict(meta.get("agent_v2") or {})
    work_state_summary = dict(agent_meta.get("work_state_summary") or {})
    trace_events = list(agent_meta.get("trace_events") or [])
    trace_events.append(
        {
            "stage": "candidate_disposition",
            "validator": "shared_hard_guards",
            "disposition": "accepted" if guard.valid else "rejected",
            "reason_count": len(guard.reasons),
            "reasons": list(guard.reasons),
        }
    )
    if str(meta.get("numbering_backend") or "") == "cmalign":
        work_state_summary["stop_reason"] = (
            "accepted_by_shared_hard_guards" if guard.valid else "rejected_by_shared_hard_guards"
        )
    disposition = str(work_state_summary.get("disposition") or "").strip().lower()
    if not disposition:
        disposition = (
            "accepted"
            if guard.valid
            else (
                "investigate"
                if numbering_row and sequence_row
                else "abstained"
            )
        )
    if disposition == "accepted" and not guard.valid:
        disposition = "investigate" if numbering_row and sequence_row else "abstained"
        work_state_summary["stop_reason"] = "rejected_by_shared_hard_guards"
    work_state_summary["disposition"] = disposition
    agent_trace = {
        "trace_events": trace_events,
        "branch_summaries": list(agent_meta.get("branch_summaries") or []),
        "work_state_summary": work_state_summary,
        "context_summary": dict(agent_meta.get("context_summary") or {}),
    }
    cm_numbering = dict(meta.get("cm_numbering") or {})

    annotation_meta = dict(
        meta.get("annotation_meta") or meta.get("agent_ann_meta") or {}
    )
    return {
        "status": "ok",
        "seqname": case.seqname,
        "input_len": len(gold_seq),
        "input_seq": gold_seq,
        "use_llm": bool(use_llm),
        "template_name": template_name,
        "warnings": list(warnings or []),
        "elapsed_sec": round(float(elapsed_sec), 4),
        "base_preserved": pred_seq == gold_seq,
        "pred_residue_count": len(pred_labels),
        "gold_residue_count": len(gold_labels) if gold_evaluation_allowed else None,
        "gold_evaluation_allowed": gold_evaluation_allowed,
        "metrics": {
            "label_correct": label_correct if gold_evaluation_allowed else None,
            "label_total": residue_total,
            "label_accuracy": (label_correct / residue_total) if residue_total else None,
            "exact_match": (pred_labels == gold_labels) if gold_evaluation_allowed else None,
            "region_correct": region_correct if gold_evaluation_allowed else None,
            "region_total": residue_total,
            "region_accuracy": (region_correct / residue_total) if residue_total else None,
            "anticodon_correct": (
                anticodon_correct if gold_evaluation_allowed else None
            ),
            "anticodon_total": anticodon_total,
            "anticodon_accuracy": (anticodon_correct / anticodon_total) if anticodon_total else None,
            "anticodon_exact": anticodon_exact,
            "cca_correct": cca_correct if gold_evaluation_allowed else None,
            "cca_total": cca_total,
            "cca_accuracy": (cca_correct / cca_total) if cca_total else None,
            "cca_exact": cca_exact,
            "discriminator_correct": (
                discriminator_correct if gold_evaluation_allowed else None
            ),
            "discriminator_total": discriminator_total,
            "discriminator_accuracy": (
                (discriminator_correct / discriminator_total)
                if discriminator_total
                else None
            ),
            "discriminator_exact": discriminator_exact,
            "terminal_coordinate_correct": (
                terminal_correct if gold_evaluation_allowed else None
            ),
            "terminal_coordinate_total": terminal_total,
            "terminal_coordinate_accuracy": (
                (terminal_correct / terminal_total) if terminal_total else None
            ),
            "terminal_coordinate_exact": terminal_exact,
            "variable_correct": variable_correct if gold_evaluation_allowed else None,
            "variable_total": variable_total,
            "variable_accuracy": (variable_correct / variable_total) if variable_total else None,
            "variable_exact": variable_exact,
            "legality_passed": bool(guard.valid),
        },
        "legality_reasons": list(guard.reasons),
        "guard_challenge_audit": guard_challenge_audit,
        "agent": {
            "route": agent_meta.get("route"),
            "route_confidence": agent_meta.get("route_confidence"),
            "out_of_scope": bool(agent_meta.get("out_of_scope")),
            "in_canonical_benchmark": bool(agent_meta.get("in_canonical_benchmark")),
            "expanded": bool(agent_meta.get("expanded")),
            "best_template": agent_meta.get("best_template"),
            "best_snapshot_id": agent_meta.get("best_snapshot_id"),
            "best_source_action": agent_meta.get("best_source_action"),
            "best_score": agent_meta.get("best_score"),
            "hard_guard_passed": bool(guard.valid),
            "internal_hard_guard_passed": agent_meta.get("hard_guard_passed"),
            "difficulty_tier": agent_meta.get("difficulty_tier"),
            "disposition": disposition,
        },
        "agent_trace": agent_trace,
        "scientific_provenance": {
            "numbering_backend": meta.get("numbering_backend"),
            "numbering_model": dict(meta.get("numbering_model") or {}),
            "cm_score": dict(meta.get("cm_score") or {}),
            "cm_portfolio": dict(meta.get("cm_portfolio") or {}),
            "cm_tool": dict(meta.get("cm_tool") or {}),
            "specialized_route": dict(meta.get("specialized_route") or {}),
            "structural_region_evidence": dict(
                meta.get("structural_region_evidence") or {}
            ),
            "consensus_structure": cm_numbering.get("consensus_structure"),
            "reference_annotation": cm_numbering.get("reference_annotation"),
            "position_provenance": list(cm_numbering.get("residues") or []),
            "trnascan": {
                "search_mode": str(annotation_meta.get("trnascan_search_mode") or ""),
                "domain_hint": str(annotation_meta.get("trnascan_domain_hint") or ""),
                "mitochondrial_subtype": str(
                    annotation_meta.get("trnascan_mitochondrial_subtype") or ""
                ),
                "command_options": list(
                    annotation_meta.get("trnascan_command_options") or []
                ),
                "executable": str(annotation_meta.get("trnascan_executable") or ""),
                "version": str(annotation_meta.get("trnascan_version") or ""),
            },
        },
        "benchmark_case": dict(case.benchmark_meta or {}),
        "csv_content": csv_content,
    }


def _challenge_snapshot(snapshot: RootSnapshot) -> RootSnapshot:
    return RootSnapshot(
        template_name=snapshot.template_name,
        csv_content=snapshot.csv_content,
        warnings=list(snapshot.warnings),
        numbering_row=list(snapshot.numbering_row),
        template_row=list(snapshot.template_row),
        sequence_row=list(snapshot.sequence_row),
        structure_view=copy.deepcopy(snapshot.structure_view),
        llm_trace_view=copy.deepcopy(snapshot.llm_trace_view),
        raw_meta=copy.deepcopy(snapshot.raw_meta),
        template_candidates=copy.deepcopy(snapshot.template_candidates),
        template_score=snapshot.template_score,
        template_margin=snapshot.template_margin,
        root_source=snapshot.root_source,
        snapshot_id=snapshot.snapshot_id,
        parent_snapshot_id=snapshot.parent_snapshot_id,
        source_action=snapshot.source_action,
    )


def _occupied_indices(snapshot: RootSnapshot) -> List[int]:
    return [
        idx
        for idx, base in enumerate(snapshot.sequence_row)
        if sanitize_base(base) != "-"
    ]


def evaluate_guard_challenges(
    request: AlignmentRequest,
    snapshot: RootSnapshot,
    baseline: Optional[Any] = None,
) -> Dict[str, Any]:
    """Challenge accepted candidates with deterministic integrity violations."""

    baseline_result = baseline or HARD_GUARDS.validate(request, snapshot)
    audit: Dict[str, Any] = {
        "schema_version": "guard_challenge_v1",
        "baseline_valid": bool(baseline_result.valid),
        "baseline_reasons": list(baseline_result.reasons),
        "challenges": [],
    }
    if not baseline_result.valid:
        audit["skipped_reason"] = "baseline_candidate_not_accepted_by_hard_guards"
        return audit

    def record(
        challenge_name: str,
        challenged_snapshot: RootSnapshot,
        challenged_request: Optional[AlignmentRequest] = None,
    ) -> None:
        result = HARD_GUARDS.validate(
            challenged_request or request,
            challenged_snapshot,
        )
        audit["challenges"].append(
            {
                "challenge": challenge_name,
                "rejected": not result.valid,
                "reasons": list(result.reasons),
            }
        )

    if snapshot.sequence_row:
        challenged = _challenge_snapshot(snapshot)
        challenged.sequence_row.pop()
        record("row_length_inconsistency", challenged)

    occupied = _occupied_indices(snapshot)
    if occupied:
        challenged = _challenge_snapshot(snapshot)
        idx = occupied[0]
        original = sanitize_base(challenged.sequence_row[idx])
        challenged.sequence_row[idx] = "A" if original != "A" else "C"
        record("base_substitution", challenged)

        challenged = _challenge_snapshot(snapshot)
        challenged.numbering_row[idx] = "999"
        record("illegal_sprinzl_label", challenged)

        invalid_request = copy.copy(request)
        input_sequence = request.normalized_target_seq()
        invalid_request.target_seq = (
            "X" + input_sequence[1:] if input_sequence else "X"
        )
        record("invalid_input_symbol", _challenge_snapshot(snapshot), invalid_request)

    if len(occupied) >= 2:
        first, second = occupied[:2]
        challenged = _challenge_snapshot(snapshot)
        challenged.numbering_row[second] = challenged.numbering_row[first]
        record("duplicate_sprinzl_label", challenged)

        challenged = _challenge_snapshot(snapshot)
        challenged.numbering_row[first], challenged.numbering_row[second] = (
            challenged.numbering_row[second],
            challenged.numbering_row[first],
        )
        record("sprinzl_order_violation", challenged)

    label_idx = {
        str(label or "").strip(): idx
        for idx, label in enumerate(snapshot.numbering_row)
    }
    if all(label in label_idx for label in ("74", "75", "76")):
        motif = "".join(
            sanitize_base(snapshot.sequence_row[label_idx[label]])
            for label in ("74", "75", "76")
        )
        if motif == "CCA" and request.cca_status == "unknown":
            challenged = _challenge_snapshot(snapshot)
            challenged.raw_meta.pop("input_transform", None)
            challenged.raw_meta.pop("agent_ann_meta", None)
            challenged.raw_meta.pop("annotation_meta", None)
            challenged.raw_meta.pop("cm_portfolio", None)
            challenged.raw_meta["numbering_backend"] = ""
            record("unsupported_cca_assignment", challenged)

    challenges = list(audit["challenges"])
    audit["challenge_count"] = len(challenges)
    audit["rejected_count"] = sum(bool(item["rejected"]) for item in challenges)
    audit["escaped_count"] = audit["challenge_count"] - audit["rejected_count"]
    return audit


def run_one_case(case: GoldCase, args: argparse.Namespace, runtime_overrides: Dict[str, Any]) -> Dict[str, Any]:
    started_at = now_iso()
    t0 = time.perf_counter()
    usage_case_id, case_runtime_overrides = with_case_usage_context(
        runtime_overrides,
        run_id=args.run_id,
        case=case,
    )
    reset_usage_context(args.run_id, usage_case_id)
    llm_failure_events: List[Dict[str, Any]] = []

    def capture_llm_failure(event: Dict[str, Any]) -> None:
        if event.get("stage") in {"llm_request_failed", "llm_decision_failed"}:
            details = event.get("process_event") or event
            llm_failure_events.append({
                "stage": event["stage"], "error_type": details.get("error_type", ""),
            })

    case_runtime_overrides["_status_callback"] = capture_llm_failure
    method_backend = str(args.method_backend)
    use_llm = method_backend == "agent" and not bool(args.no_llm)
    try:
        if method_backend == "cm":
            cm_result = get_cm_service(args).align(
                case.input_seq,
                model_group=cm_model_group_for_case(case),
                sequence_id=case.seqname,
            )
            template_name = cm_result.model.name
            csv_content = cm_result.csv_content()
            warnings = cm_result.warnings
            meta = cm_result.metadata()
        else:
            biological_contract = biological_contract_for_case(case)
            response = get_service(
                str(getattr(args, "agent_planner_policy", "llm"))
            ).align_with_meta(
                AlignmentRequest(
                    target_seq=case.input_seq,
                    output_csv_path="/dev/null",
                    anticode="",
                    use_llm=use_llm,
                    llm_reasoning=bool(args.llm_reasoning),
                    template_name=None,
                    **biological_contract,
                    enable_root_recovery=bool(
                        getattr(args, "agent_enable_cm_root_recovery", False)
                    ),
                    return_meta=True,
                    status_callback=capture_llm_failure,
                    runtime_values_override=dict(case_runtime_overrides),
                )
            )
            template_name = response.template_name
            csv_content = response.csv_content
            warnings = response.warnings
            meta = response.meta
        result = evaluate_case(
            case,
            template_name=template_name,
            csv_content=csv_content,
            warnings=warnings,
            meta=meta,
            use_llm=use_llm,
            llm_reasoning=bool(args.llm_reasoning),
            runtime_overrides=case_runtime_overrides,
            elapsed_sec=(time.perf_counter() - t0),
        )
    except Exception as exc:
        result = {
            "status": "error",
            "seqname": case.seqname,
            "input_len": len(case.input_seq),
            "input_seq": case.input_seq,
            "use_llm": use_llm,
            "benchmark_case": dict(case.benchmark_meta or {}),
            "elapsed_sec": round(float(time.perf_counter() - t0), 4),
            "error": f"{type(exc).__name__}: {exc}",
        }
    result["started_at"] = started_at
    result["finished_at"] = now_iso()
    result["method_backend"] = method_backend
    result["provider"] = args.provider
    result["model"] = args.model
    result["agent_planner_policy"] = str(
        getattr(args, "agent_planner_policy", "llm")
    )
    result["agent_llm_postcheck_enabled"] = bool(
        getattr(args, "agent_enable_llm_postcheck", False)
    )
    result["agent_deterministic_boundary_refinement_enabled"] = not bool(
        getattr(args, "agent_disable_deterministic_boundary_refinement", False)
    )
    result["use_llm"] = use_llm
    result["llm_usage"] = get_usage_summary(args.run_id, usage_case_id, include_calls=True)
    result["llm_failure_events"] = llm_failure_events
    enforce_llm_failure_policy(result, strict=bool(getattr(args, "strict_llm_failures", False)))
    return result


def enforce_llm_failure_policy(result: Dict[str, Any], *, strict: bool) -> None:
    issues = llm_technical_issues(result)
    result["llm_technical_issues"] = issues
    if issues and strict:
        result["status"] = "error"
        result["error_category"] = "llm_technical_failure"
        result.setdefault("error", f"LLM technical failure: {issues}")


def aggregate_llm_usage(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    calls: List[Dict[str, Any]] = []
    for row in rows:
        usage = row.get("llm_usage")
        if not isinstance(usage, dict):
            continue
        row_calls = usage.get("calls")
        if isinstance(row_calls, list):
            calls.extend([call for call in row_calls if isinstance(call, dict)])
    if calls:
        summary = summarize_usage_calls(calls)
        by_source: Dict[str, Dict[str, Any]] = {}
        for call in calls:
            source = str(call.get("source") or "unknown")
            source_summary = by_source.setdefault(
                source,
                {"call_count": 0, "calls_with_usage": 0, "tokens": {}},
            )
            source_summary["call_count"] += 1
            usage_flat = dict(call.get("usage_flat") or {})
            if usage_flat:
                source_summary["calls_with_usage"] += 1
            tokens = dict(source_summary["tokens"])
            for key, value in usage_flat.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    tokens[key] = tokens.get(key, 0) + value
            source_summary["tokens"] = tokens
        summary["by_source"] = dict(sorted(by_source.items()))
        return summary

    tokens: Dict[str, int | float] = {}
    call_count = 0
    calls_with_usage = 0
    for row in rows:
        usage = row.get("llm_usage")
        if not isinstance(usage, dict):
            continue
        call_count += int(usage.get("call_count") or 0)
        calls_with_usage += int(usage.get("calls_with_usage") or 0)
        for key, value in dict(usage.get("tokens") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                tokens[key] = tokens.get(key, 0) + value
    return {
        "call_count": call_count,
        "calls_with_usage": calls_with_usage,
        "tokens": tokens,
        "by_model": {},
        "by_source": {},
    }


def summarize_results(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    def _gold_evaluation_allowed(row: Mapping[str, Any]) -> bool:
        if "gold_evaluation_allowed" in row:
            return bool(row.get("gold_evaluation_allowed"))
        benchmark_case = dict(row.get("benchmark_case") or {})
        return bool(benchmark_case.get("gold_evaluation_allowed", True))

    ok_rows = [row for row in rows if str(row.get("status") or "") == "ok"]
    error_rows = [row for row in rows if str(row.get("status") or "") != "ok"]
    scored_rows = [row for row in rows if _gold_evaluation_allowed(row)]
    scored_ok_rows = [row for row in ok_rows if _gold_evaluation_allowed(row)]
    unscored_rows = [row for row in rows if not _gold_evaluation_allowed(row)]
    disposition_counts = Counter(
        str(dict(row.get("agent") or {}).get("disposition") or "reported")
        for row in ok_rows
    )
    accepted_rows = [
        row
        for row in ok_rows
        if str(dict(row.get("agent") or {}).get("disposition") or "") == "accepted"
    ]
    accepted_scored_rows = [
        row for row in accepted_rows if _gold_evaluation_allowed(row)
    ]

    def _metric_sum(selected_rows: Sequence[Dict[str, Any]], name: str) -> int:
        total = 0
        for row in selected_rows:
            total += int(((row.get("metrics") or {}).get(name)) or 0)
        return total

    def _ratio(
        selected_rows: Sequence[Dict[str, Any]],
        correct_key: str,
        total_key: str,
    ) -> Optional[float]:
        total = _metric_sum(selected_rows, total_key)
        if total == 0:
            return None
        return _metric_sum(selected_rows, correct_key) / total

    def _exact_rate(selected_rows: Sequence[Dict[str, Any]], key: str) -> Optional[float]:
        values = [
            1 if (row.get("metrics") or {}).get(key) else 0
            for row in selected_rows
            if (row.get("metrics") or {}).get(key) is not None
        ]
        return (sum(values) / len(values)) if values else None

    elapsed_values = [float(row.get("elapsed_sec") or 0.0) for row in ok_rows]
    exact_values = [
        1 if (row.get("metrics") or {}).get("exact_match") else 0
        for row in scored_ok_rows
        if (row.get("metrics") or {}).get("exact_match") is not None
    ]
    legality_values = [1 if (row.get("metrics") or {}).get("legality_passed") else 0 for row in ok_rows]
    base_preserved_values = [1 if row.get("base_preserved") else 0 for row in ok_rows]
    out_of_scope_values = [1 if (row.get("agent") or {}).get("out_of_scope") else 0 for row in ok_rows]

    anticodon_exact_vals = [
        1 if (row.get("metrics") or {}).get("anticodon_exact") else 0
        for row in scored_ok_rows
        if (row.get("metrics") or {}).get("anticodon_exact") is not None
    ]
    cca_exact_vals = [
        1 if (row.get("metrics") or {}).get("cca_exact") else 0
        for row in scored_ok_rows
        if (row.get("metrics") or {}).get("cca_exact") is not None
    ]
    discriminator_exact_vals = [
        1 if (row.get("metrics") or {}).get("discriminator_exact") else 0
        for row in scored_ok_rows
        if (row.get("metrics") or {}).get("discriminator_exact") is not None
    ]
    terminal_exact_vals = [
        1 if (row.get("metrics") or {}).get("terminal_coordinate_exact") else 0
        for row in scored_ok_rows
        if (row.get("metrics") or {}).get("terminal_coordinate_exact") is not None
    ]
    variable_exact_vals = [
        1 if (row.get("metrics") or {}).get("variable_exact") else 0
        for row in scored_ok_rows
        if (row.get("metrics") or {}).get("variable_exact") is not None
    ]

    return {
        "counts": {
            "total_cases": len(rows),
            "ok_cases": len(ok_rows),
            "error_cases": len(error_rows),
            "accepted_cases": len(accepted_rows),
            "gold_scored_cases": len(scored_rows),
            "gold_scored_ok_cases": len(scored_ok_rows),
            "gold_unscored_cases": len(unscored_rows),
            "accepted_gold_scored_cases": len(accepted_scored_rows),
            "dispositions": dict(sorted(disposition_counts.items())),
        },
        "metrics": {
            "residue_label_accuracy": _ratio(scored_ok_rows, "label_correct", "label_total"),
            "exact_match_rate": (sum(exact_values) / len(exact_values)) if exact_values else None,
            "region_accuracy": _ratio(scored_ok_rows, "region_correct", "region_total"),
            "anticodon_accuracy": _ratio(scored_ok_rows, "anticodon_correct", "anticodon_total"),
            "anticodon_exact_rate": (sum(anticodon_exact_vals) / len(anticodon_exact_vals)) if anticodon_exact_vals else None,
            "cca_accuracy": _ratio(scored_ok_rows, "cca_correct", "cca_total"),
            "cca_exact_rate": (sum(cca_exact_vals) / len(cca_exact_vals)) if cca_exact_vals else None,
            "discriminator_accuracy": _ratio(
                scored_ok_rows,
                "discriminator_correct",
                "discriminator_total",
            ),
            "discriminator_exact_rate": (
                sum(discriminator_exact_vals) / len(discriminator_exact_vals)
                if discriminator_exact_vals
                else None
            ),
            "terminal_coordinate_accuracy": _ratio(
                scored_ok_rows,
                "terminal_coordinate_correct",
                "terminal_coordinate_total",
            ),
            "terminal_coordinate_exact_rate": (
                sum(terminal_exact_vals) / len(terminal_exact_vals)
                if terminal_exact_vals
                else None
            ),
            "variable_accuracy": _ratio(scored_ok_rows, "variable_correct", "variable_total"),
            "variable_exact_rate": (sum(variable_exact_vals) / len(variable_exact_vals)) if variable_exact_vals else None,
            "legality_pass_rate": (sum(legality_values) / len(legality_values)) if legality_values else None,
            "base_preservation_rate": (sum(base_preserved_values) / len(base_preserved_values)) if base_preserved_values else None,
            "out_of_scope_rate": (sum(out_of_scope_values) / len(out_of_scope_values)) if out_of_scope_values else None,
        },
        "selective_metrics": {
            "accepted_coverage": (len(accepted_rows) / len(rows)) if rows else None,
            "accepted_residue_label_accuracy": _ratio(
                accepted_scored_rows,
                "label_correct",
                "label_total",
            ),
            "accepted_exact_match_rate": _exact_rate(accepted_scored_rows, "exact_match"),
            "accepted_region_accuracy": _ratio(
                accepted_scored_rows,
                "region_correct",
                "region_total",
            ),
            "accepted_variable_accuracy": _ratio(
                accepted_scored_rows,
                "variable_correct",
                "variable_total",
            ),
            "investigate_rate": (
                disposition_counts.get("investigate", 0) / len(rows)
            ) if rows else None,
            "abstained_rate": (
                disposition_counts.get("abstained", 0) / len(rows)
            ) if rows else None,
            "error_rate": (len(error_rows) / len(rows)) if rows else None,
        },
        "timing": {
            "elapsed_total_sec": round(sum(elapsed_values), 4) if elapsed_values else 0.0,
            "elapsed_mean_sec": round(statistics.mean(elapsed_values), 4) if elapsed_values else None,
            "elapsed_median_sec": round(statistics.median(elapsed_values), 4) if elapsed_values else None,
        },
        "llm_usage": aggregate_llm_usage(rows),
        "agent_activity": aggregate_agent_activity(rows),
        "guard_challenge_audit": aggregate_guard_challenges(rows),
    }


def aggregate_guard_challenges(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_challenge: Dict[str, Dict[str, Any]] = {}
    baseline_valid_count = 0
    baseline_invalid_count = 0
    challenge_count = 0
    rejected_count = 0
    for row in rows:
        if str(row.get("status") or "") != "ok":
            continue
        audit = dict(row.get("guard_challenge_audit") or {})
        if bool(audit.get("baseline_valid")):
            baseline_valid_count += 1
        else:
            baseline_invalid_count += 1
        for challenge in list(audit.get("challenges") or []):
            if not isinstance(challenge, dict):
                continue
            name = str(challenge.get("challenge") or "unknown")
            bucket = by_challenge.setdefault(
                name,
                {"eligible_count": 0, "rejected_count": 0, "escaped_count": 0},
            )
            bucket["eligible_count"] += 1
            challenge_count += 1
            if bool(challenge.get("rejected")):
                bucket["rejected_count"] += 1
                rejected_count += 1
            else:
                bucket["escaped_count"] += 1

    for bucket in by_challenge.values():
        eligible = int(bucket["eligible_count"])
        bucket["rejection_rate"] = (
            int(bucket["rejected_count"]) / eligible if eligible else None
        )
    return {
        "schema_version": "guard_challenge_v1",
        "baseline_valid_count": baseline_valid_count,
        "baseline_invalid_count": baseline_invalid_count,
        "challenge_count": challenge_count,
        "rejected_count": rejected_count,
        "escaped_count": challenge_count - rejected_count,
        "rejection_rate": rejected_count / challenge_count if challenge_count else None,
        "by_challenge": dict(sorted(by_challenge.items())),
    }


def aggregate_agent_activity(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    action_types: Counter[str] = Counter()
    dispositions: Counter[str] = Counter()
    stop_reasons: Counter[str] = Counter()
    planning_events = 0
    planned_actions = 0
    executed_actions = 0
    failed_actions = 0
    cm_tool_calls = 0
    cases_with_actions = 0
    planner_sources: Counter[str] = Counter()
    planner_decisions: Counter[str] = Counter()
    planner_llm_calls = 0

    for row in rows:
        trace = dict(row.get("agent_trace") or {})
        events = list(trace.get("trace_events") or [])
        cm_tool_calls += count_cm_tool_calls(events)
        row_action_count = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            stage = str(event.get("stage") or "")
            if stage == "planning":
                planning_events += 1
                planned_actions += len(list(event.get("planned_actions") or []))
            decision_event = None
            if stage == "agent_decision":
                decision_event = event
            elif stage == "planning" and isinstance(event.get("agent_decision"), dict):
                decision_event = dict(event["agent_decision"])
            if decision_event is not None:
                planner_sources[str(decision_event.get("planner_source") or "unknown")] += 1
                planner_decisions[str(decision_event.get("decision") or "unknown")] += 1
                planner_llm_calls += int(bool(decision_event.get("llm_called")))
            if stage not in {"action_executed", "action_failed"}:
                continue
            action_type = str(event.get("action_type") or "unknown")
            disposition = str(event.get("disposition") or "unknown")
            action_types[action_type] += 1
            dispositions[disposition] += 1
            executed_actions += 1
            row_action_count += 1
            failed_actions += stage == "action_failed"
        if row_action_count:
            cases_with_actions += 1
        stop_reason = str(dict(trace.get("work_state_summary") or {}).get("stop_reason") or "")
        if stop_reason:
            stop_reasons[stop_reason] += 1

    return {
        "planning_event_count": planning_events,
        "planned_action_count": planned_actions,
        "executed_action_count": executed_actions,
        "failed_action_count": failed_actions,
        "cm_tool_call_count": cm_tool_calls,
        "cases_with_actions": cases_with_actions,
        "action_types": dict(sorted(action_types.items())),
        "dispositions": dict(sorted(dispositions.items())),
        "stop_reasons": dict(sorted(stop_reasons.items())),
        "planner_sources": dict(sorted(planner_sources.items())),
        "planner_decisions": dict(sorted(planner_decisions.items())),
        "planner_llm_call_count": planner_llm_calls,
    }


def write_case_results_tsv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """Write the run index. MySQL receives exactly these fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(case_results_tsv_text(rows), encoding="utf-8")


def case_results_tsv_text(rows: Sequence[Dict[str, Any]]) -> str:
    """One index format, shared with the store that rebuilds a run directory."""
    return case_traces_tsv_text(rows)


def _load_existing_manifest(store: Any, manifest_path: Path) -> Optional[Dict[str, Any]]:
    """Previous attempt metadata comes from the store when a store is in use."""
    if store is not None:
        body = store.get_artifact("manifest.json")
        if body:
            try:
                return json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                raise SystemExit("stored manifest.json is not valid JSON")
        row = store.run_row()
        if row:
            try:
                config = json.loads(row.get("config_json") or "{}")
            except json.JSONDecodeError:
                config = {}
            return {
                "attempt": int(row.get("attempt") or 0),
                "run_contract": config,
                "run_contract_sha256": str(row.get("run_contract_sha256") or ""),
            }
    return read_case_result(manifest_path)


def _collect_case_rows(
    store: Any,
    cases: Sequence[GoldCase],
    cases_dir: Path,
    *,
    keep_local: bool,
    cached: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Load every case result needed by the summary, preferring the store."""
    if store is None:
        return load_all_case_results(cases, cases_dir)
    cached = cached or {}

    def fetch(pair: Tuple[int, GoldCase]) -> Tuple[int, Dict[str, Any]]:
        index, case = pair
        payload = cached.get(case.seqname)
        if payload is None:
            payload = store.case_json(case.seqname)
        if payload is None and keep_local:
            payload = read_case_result(case_result_path(cases_dir, case.seqname))
        if payload is None:
            payload = {
                "seqname": case.seqname,
                "status": "missing",
                "input_len": len(case.input_seq),
                "gold_evaluation_allowed": bool(
                    (case.benchmark_meta or {}).get("gold_evaluation_allowed", True)
                ),
                "benchmark_case": dict(case.benchmark_meta or {}),
                "error": "missing_case_result",
            }
        return index, payload

    rows: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=16, thread_name_prefix="collect") as pool:
        for index, payload in pool.map(fetch, list(enumerate(cases))):
            rows[index] = payload
    return [rows[index] for index in range(len(cases)) if index in rows]


def write_summary_txt(path: Path, summary: Dict[str, Any]) -> None:
    path.write_text(summary_txt_text(summary), encoding="utf-8")


def summary_txt_text(summary: Dict[str, Any]) -> str:
    counts = dict(summary.get("counts") or {})
    metrics = dict(summary.get("metrics") or {})
    selective = dict(summary.get("selective_metrics") or {})
    timing = dict(summary.get("timing") or {})
    llm_usage = dict(summary.get("llm_usage") or {})
    llm_tokens = dict(llm_usage.get("tokens") or {})
    agent_activity = dict(summary.get("agent_activity") or {})
    guard_audit = dict(summary.get("guard_challenge_audit") or {})
    lines = [
        "Single-sequence Sprinzl numbering benchmark",
        f"total_cases: {counts.get('total_cases')}",
        f"ok_cases: {counts.get('ok_cases')}",
        f"error_cases: {counts.get('error_cases')}",
        f"accepted_cases: {counts.get('accepted_cases')}",
        f"gold_scored_cases: {counts.get('gold_scored_cases')}",
        f"gold_scored_ok_cases: {counts.get('gold_scored_ok_cases')}",
        f"gold_unscored_cases: {counts.get('gold_unscored_cases')}",
        f"dispositions: {json.dumps(counts.get('dispositions') or {}, sort_keys=True)}",
        f"residue_label_accuracy: {metrics.get('residue_label_accuracy')}",
        f"exact_match_rate: {metrics.get('exact_match_rate')}",
        f"region_accuracy: {metrics.get('region_accuracy')}",
        f"anticodon_accuracy: {metrics.get('anticodon_accuracy')}",
        f"anticodon_exact_rate: {metrics.get('anticodon_exact_rate')}",
        f"cca_accuracy: {metrics.get('cca_accuracy')}",
        f"cca_exact_rate: {metrics.get('cca_exact_rate')}",
        f"variable_accuracy: {metrics.get('variable_accuracy')}",
        f"variable_exact_rate: {metrics.get('variable_exact_rate')}",
        f"legality_pass_rate: {metrics.get('legality_pass_rate')}",
        f"base_preservation_rate: {metrics.get('base_preservation_rate')}",
        f"out_of_scope_rate: {metrics.get('out_of_scope_rate')}",
        f"accepted_coverage: {selective.get('accepted_coverage')}",
        f"accepted_residue_label_accuracy: {selective.get('accepted_residue_label_accuracy')}",
        f"accepted_exact_match_rate: {selective.get('accepted_exact_match_rate')}",
        f"accepted_region_accuracy: {selective.get('accepted_region_accuracy')}",
        f"accepted_variable_accuracy: {selective.get('accepted_variable_accuracy')}",
        f"investigate_rate: {selective.get('investigate_rate')}",
        f"abstained_rate: {selective.get('abstained_rate')}",
        f"error_rate: {selective.get('error_rate')}",
        f"llm_api_call_count: {llm_usage.get('call_count')}",
        f"llm_calls_with_usage: {llm_usage.get('calls_with_usage')}",
        f"llm_prompt_tokens: {llm_tokens.get('prompt_tokens')}",
        f"llm_completion_tokens: {llm_tokens.get('completion_tokens')}",
        f"llm_total_tokens: {llm_tokens.get('total_tokens')}",
        f"llm_calls_by_source: {json.dumps(llm_usage.get('by_source') or {}, sort_keys=True)}",
        f"llm_prompt_cache_hit_tokens: {llm_tokens.get('prompt_cache_hit_tokens')}",
        f"llm_prompt_cache_miss_tokens: {llm_tokens.get('prompt_cache_miss_tokens')}",
        f"agent_executed_action_count: {agent_activity.get('executed_action_count')}",
        f"agent_failed_action_count: {agent_activity.get('failed_action_count')}",
        f"cm_tool_call_count: {agent_activity.get('cm_tool_call_count')}",
        f"planner_llm_call_count: {agent_activity.get('planner_llm_call_count')}",
        f"planner_sources: {json.dumps(agent_activity.get('planner_sources') or {}, sort_keys=True)}",
        f"guard_challenge_count: {guard_audit.get('challenge_count')}",
        f"guard_challenge_rejected_count: {guard_audit.get('rejected_count')}",
        f"guard_challenge_escaped_count: {guard_audit.get('escaped_count')}",
        f"guard_challenge_rejection_rate: {guard_audit.get('rejection_rate')}",
        f"elapsed_total_sec: {timing.get('elapsed_total_sec')}",
        f"elapsed_mean_sec: {timing.get('elapsed_mean_sec')}",
        f"elapsed_median_sec: {timing.get('elapsed_median_sec')}",
    ]
    return "\n".join(lines) + "\n"


def load_all_case_results(cases: Sequence[GoldCase], cases_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for case in cases:
        result = read_case_result(case_result_path(cases_dir, case.seqname))
        if result is None:
            rows.append(
                {
                    "seqname": case.seqname,
                    "status": "missing",
                    "input_len": len(case.input_seq),
                    "gold_evaluation_allowed": bool(
                        case.benchmark_meta.get("gold_evaluation_allowed", True)
                    ),
                    "benchmark_case": dict(case.benchmark_meta or {}),
                    "error": "missing_case_result",
                }
            )
        else:
            rows.append(result)
    return rows


def write_manifest(path: Path, payload: Dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def input_file_descriptor(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {"path": "", "sha256": ""}
    return {
        "path": str(path),
        "sha256": sha256_file(path),
    }


def resume_contract_compatible(
    existing_contract: Mapping[str, Any],
    current_contract: Mapping[str, Any],
) -> bool:
    """Allow checkpoint recovery across transport-only scheduling changes."""
    existing = copy.deepcopy(dict(existing_contract))
    current = copy.deepcopy(dict(current_contract))
    for contract in (existing, current):
        request_policy = (
            contract.get("method", {})
            .get("llm_endpoint", {})
            .get("request_policy", {})
        )
        if isinstance(request_policy, dict):
            # This changes throughput and retry timing, not the scientific
            # decision policy or the produced coordinate rules.
            request_policy.pop("min_request_interval_s", None)
        endpoint = contract.get("method", {}).get("llm_endpoint", {})
        if isinstance(endpoint, dict):
            # Adding or removing a fallback credential endpoint is also a
            # transport-only change. The primary base/completion URL, provider,
            # and model remain in the contract and continue to be checked.
            endpoint.pop("credential_pool_size", None)
            endpoint.pop("credential_endpoints", None)
    return existing == current


def main() -> None:
    args = parse_args()
    out_dir = args.out_dir or default_run_cache_dir() / args.run_id
    if args.gold_from_store:
        # The answer is read once, at the start, and frozen as this run's input.
        # Later curation rounds change the library answer, not this snapshot.
        staging_names = load_seqname_filter(args.seqnames_file) if args.seqnames_file else None
        if staging_names is None and args.submission_manifest is not None:
            staging_names = set(load_submission_case_metadata(args.submission_manifest))
        if not staging_names:
            raise ValueError("--gold-from-store needs --seqnames-file or --submission-manifest")
        import stage_gold_from_store  # noqa: PLC0415  (benchmark script helper)

        staged = stage_gold_from_store.stage_gold(sorted(staging_names), out_dir / "inputs")
        args.input_tsv = staged["reference_tsv"]
        LOG.info(
            "gold staged from trna_gold: %d cases, answers reviewed=%d sha256=%s",
            staged["sequences"],
            staged["decided"],
            staged["reference_sha256"][:16],
        )
    if args.input_tsv is None:
        raise ValueError("provide --input-tsv or --gold-from-store")
    cases_dir = out_dir / "cases"
    logs_dir = out_dir / "logs"
    manifest_path = out_dir / "manifest.json"
    summary_json_path = out_dir / "summary.json"
    summary_txt_path = out_dir / "summary.txt"
    case_results_path = out_dir / "case_results.tsv"
    log_path = logs_dir / "run.log"

    if args.no_store and not args.keep_local:
        raise SystemExit(
            "--no-store needs --keep-local: a run has to live in MySQL plus MinIO "
            "or on disk, never nowhere."
        )
    if args.keep_local:
        out_dir.mkdir(parents=True, exist_ok=True)
    log_handler = setup_logging(log_path if args.keep_local else None)

    store = None
    if not args.no_store:
        try:
            store = connect_store(args.run_id)
        except BenchmarkStoreError as exc:
            raise SystemExit(f"benchmark refused to start: {exc}")

    def persist_manifest(payload: Dict[str, Any]) -> None:
        if args.keep_local:
            write_manifest(manifest_path, payload)
        if store is not None:
            store.put_artifact("manifest.json", payload)

    def upload_inputs() -> None:
        """Archive the cohort files a run actually read, under ``inputs/``.

        A run is then self-describing in MinIO: the answer key, the selected
        sequence names and the curation manifest can be re-fetched and hash
        checked without any local benchmark directory.
        """
        if store is None:
            return
        logical = (
            ("inputs/reference.tsv", args.input_tsv, "text/tab-separated-values"),
            ("inputs/seqnames.txt", args.seqnames_file, "text/plain"),
            ("inputs/case_manifest.jsonl", args.submission_manifest, "application/x-ndjson"),
        )
        for name, source, content_type in logical:
            if source is None or not Path(source).is_file():
                continue
            store.put_artifact(name, Path(source).read_bytes(), content_type=content_type)
        roster = args.input_tsv.with_name("cohort_membership.tsv") if args.input_tsv else None
        if roster is not None and roster.is_file():
            store.put_artifact(
                "inputs/cohort_membership.tsv", roster.read_bytes(),
                content_type="text/tab-separated-values",
            )

    def local_case_result(seqname: str) -> Optional[Dict[str, Any]]:
        if not args.keep_local:
            return None
        return read_case_result(case_result_path(cases_dir, seqname))

    stored_rows_cache: Dict[str, Dict[str, Any]] = {}
    stored_rows_loaded = False

    def stored_result(seqname: str) -> Optional[Dict[str, Any]]:
        nonlocal stored_rows_loaded
        if store is not None:
            # One listing replaces a SELECT per case: a 2530-case resume used to
            # ask the database 5,060 times for rows that arrive in one query.
            if not stored_rows_loaded:
                for row in store.list_rows():
                    stored_rows_cache[str(row.get("seqname") or "")] = row
                stored_rows_loaded = True
            row = stored_rows_cache.get(str(seqname))
            if row is not None:
                return row
        return local_case_result(seqname)

    seqname_filter = load_seqname_filter(args.seqnames_file)
    case_metadata: Dict[str, Dict[str, Any]] = {}
    if args.case_manifest is not None and args.submission_manifest is not None:
        raise ValueError(
            "Use either --case-manifest or --submission-manifest, not both"
        )
    if args.case_manifest is not None:
        case_metadata = select_manifest_cases(
            load_case_manifest(args.case_manifest),
            protocol=str(args.protocol),
            split=str(args.split),
            template_overlap=str(args.template_overlap_view),
        )
        if not case_metadata:
            raise ValueError(
                "Case manifest selection is empty: "
                f"protocol={args.protocol} split={args.split} "
                f"template_overlap={args.template_overlap_view}"
            )
        for record in case_metadata.values():
            record["selected_protocol"] = str(args.protocol)
            record["selected_split"] = str(args.split)
            record["selected_template_overlap_view"] = str(args.template_overlap_view)
        manifest_names = set(case_metadata)
        seqname_filter = manifest_names if seqname_filter is None else seqname_filter & manifest_names
    elif args.submission_manifest is not None:
        case_metadata = load_submission_case_metadata(args.submission_manifest)
        manifest_names = set(case_metadata)
        if seqname_filter is not None:
            missing = sorted(seqname_filter - manifest_names)
            if missing:
                raise ValueError(
                    "Submission manifest does not cover every requested seqname: "
                    f"missing={len(missing)} first={missing[0]}"
                )
            seqname_filter = seqname_filter & manifest_names
        else:
            seqname_filter = manifest_names
    cases = load_gold_cases(
        args.input_tsv,
        offset=args.offset,
        limit=args.limit,
        seqname_filter=seqname_filter,
        case_metadata=case_metadata,
    )
    if not cases:
        raise ValueError("No benchmark cases selected")
    runtime_overrides = build_runtime_overrides(args)
    llm_endpoint_provenance = (
        build_llm_endpoint_provenance(runtime_overrides)
        if args.method_backend == "agent"
        else {}
    )
    cm_provenance: Dict[str, Any] = {}
    if args.method_backend == "cm":
        cm_provenance = CmalignNumberingService(
            model_root=args.cm_model_root,
            executable=args.cmalign,
            cpu=args.cm_cpu,
        ).provenance()
    alignment_backend_provenance = (
        required_alignment_backend_provenance()
        if args.method_backend == "agent"
        else {}
    )
    trnascan_provenance = {
        **trnascan_runtime_provenance(),
        "mode_policy": {
            "bacteria": "-B",
            "archaea": "-A",
            "cytosolic_eukaryote": "-E",
            "chloroplast_or_cyanelle": "-O",
            "mitochondrial": "-M <subtype>",
            "unknown": "-G",
        },
        "shared_options": [*TRNASCAN_SENSITIVE_OPTIONS, "<source-mode>", "-Q"],
    }

    input_files = {
        "gold": input_file_descriptor(args.input_tsv),
        "case_manifest": input_file_descriptor(args.case_manifest),
        "submission_manifest": input_file_descriptor(args.submission_manifest),
        "seqnames_file": input_file_descriptor(args.seqnames_file),
    }
    selected_seqnames_sha256 = digest_text("\n".join(sorted(case.seqname for case in cases)) + "\n")
    run_contract = {
        "schema_version": "1.0",
        "git_commit": git_commit(),
        "git_worktree_dirty": git_worktree_dirty(),
        "inputs": input_files,
        "selection": {
            "protocol": str(args.protocol) if args.case_manifest else "",
            "split": str(args.split) if args.case_manifest else "",
            "template_overlap_view": str(args.template_overlap_view) if args.case_manifest else "",
            "submission_roles": sorted(
                {
                    str(
                        case.benchmark_meta.get("benchmark_partition")
                        or case.benchmark_meta.get("retrospective_split")
                        or ""
                    )
                    for case in cases
                }
                - {""}
            ),
            "selected_seqnames_sha256": selected_seqnames_sha256,
            "target_case_count": len(cases),
            "offset": int(args.offset),
            "limit": args.limit,
        },
        "method": {
            "backend": str(args.method_backend),
            "provider": args.provider if args.method_backend == "agent" else "",
            "model": args.model if args.method_backend == "agent" else "",
            "llm_endpoint": llm_endpoint_provenance,
            "llm_reasoning": bool(args.llm_reasoning) if args.method_backend == "agent" else False,
            "use_llm": (not bool(args.no_llm)) if args.method_backend == "agent" else False,
            "strict_llm_failures": bool(args.strict_llm_failures),
            "agent_enable_planning": bool(args.agent_enable_planning),
            "agent_planner_policy": str(args.agent_planner_policy),
            "agent_llm_triage_mode": str(args.agent_llm_triage_mode),
            "agent_soft_risk_policy": {
                "minimum_cm_average_posterior": runtime_overrides.get(
                    "AGENT_SOFT_RISK_MIN_CM_POSTERIOR"
                ),
                "review_insertions": runtime_overrides.get(
                    "AGENT_SOFT_RISK_REVIEW_INSERTIONS"
                ),
                "review_broad_cm": runtime_overrides.get(
                    "AGENT_SOFT_RISK_REVIEW_BROAD_CM"
                ),
                "review_local_coordinate_ambiguity": runtime_overrides.get(
                    "AGENT_SOFT_RISK_REVIEW_LOCAL_COORDINATE_AMBIGUITY"
                ),
                "review_acceptor_tail": runtime_overrides.get(
                    "AGENT_SOFT_RISK_REVIEW_ACCEPTOR_TAIL"
                ),
                "local_ambiguity_reference_top_k": runtime_overrides.get(
                    "AGENT_LOCAL_AMBIGUITY_REFERENCE_TOP_K"
                ),
                "local_ambiguity_min_reference_support": runtime_overrides.get(
                    "AGENT_LOCAL_AMBIGUITY_MIN_REFERENCE_SUPPORT"
                ),
                "minimum_local_resolution_score_gain": runtime_overrides.get(
                    "AGENT_SOFT_RISK_MIN_LOCAL_RESOLUTION_SCORE_GAIN"
                ),
                "minimum_cross_backend_score_gain": runtime_overrides.get(
                    "AGENT_SOFT_RISK_MIN_CROSS_BACKEND_SCORE_GAIN"
                ),
            },
            "agent_memory_mode": str(args.agent_memory_mode),
            "agent_memory_path": (
                input_file_descriptor(args.agent_memory_path)
                if str(args.agent_memory_mode) == "read_only"
                else {"path": str(args.agent_memory_path), "sha256": ""}
            ),
            "agent_llm_postcheck_enabled": bool(
                getattr(args, "agent_enable_llm_postcheck", False)
            ),
            "agent_deterministic_boundary_refinement_enabled": not bool(
                getattr(args, "agent_disable_deterministic_boundary_refinement", False)
            ),
            "agent_enable_cm_tool": bool(args.agent_enable_cm_tool),
            "agent_enable_cm_root_recovery": bool(
                getattr(args, "agent_enable_cm_root_recovery", False)
            ),
            "agent_canonical_root_strategy": str(args.agent_canonical_root_strategy),
            "agent_enable_legacy_fallback": not bool(args.agent_disable_legacy_fallback),
            "agent_literature_tool": bool(getattr(args, "agent_literature_tool", True)),
            "agent_numbering_manual": str(
                getattr(args, "agent_numbering_manual", "brief")
            ),
            "agent_planner_max_tokens": runtime_overrides.get(
                "AGENT_PLANNER_MAX_TOKENS"
            ),
            "agent_proposal_max_tokens": runtime_overrides.get(
                "AGENT_PROPOSAL_MAX_TOKENS"
            ),
            "agent_escalation_requires_attempt": runtime_overrides.get(
                "AGENT_ESCALATION_REQUIRES_ATTEMPT"
            ),
            "agent_literature_max_notes": runtime_overrides.get(
                "AGENT_LITERATURE_MAX_NOTES"
            ),
            "agent_coordinate_proposal": bool(
                getattr(args, "agent_coordinate_proposal", False)
            ),
            "cm": cm_provenance,
            "alignment_backend": alignment_backend_provenance,
            "trnascan": trnascan_provenance,
        },
    }
    run_contract_sha256 = digest_text(json.dumps(run_contract, sort_keys=True, separators=(",", ":")))
    existing_manifest = _load_existing_manifest(store, manifest_path)
    # --resume is deliberately explicit when a command line still contains
    # --rerun-all from the original full run. It prevents an interruption from
    # turning a checkpointed run into another full evaluation.
    effective_rerun_all = bool(args.rerun_all and not args.resume)
    existing_contract = str((existing_manifest or {}).get("run_contract_sha256") or "")
    if existing_contract and existing_contract != run_contract_sha256 and not effective_rerun_all:
        existing_contract_payload = dict((existing_manifest or {}).get("run_contract") or {})
        if not args.resume or not resume_contract_compatible(
            existing_contract_payload, run_contract
        ):
            raise RuntimeError(
                "Run contract differs from the existing run directory. "
                "Use a new --run-id, pass --rerun-all explicitly, or use --resume "
                "only when the difference is transport scheduling."
            )
    previous_attempt = int((existing_manifest or {}).get("attempt") or 0)

    manifest = {
        "run_id": args.run_id,
        "command": " ".join(shlex.quote(part) for part in sys.argv),
        "input_tsv": str(args.input_tsv),
        "input_files": input_files,
        "case_manifest": str(args.case_manifest) if args.case_manifest else "",
        "submission_manifest": (
            str(args.submission_manifest) if args.submission_manifest else ""
        ),
        "protocol": str(args.protocol) if args.case_manifest else "",
        "split": str(args.split) if args.case_manifest else "",
        "template_overlap_view": str(args.template_overlap_view) if args.case_manifest else "",
        "selected_seqnames_sha256": selected_seqnames_sha256,
        "run_contract": run_contract,
        "run_contract_sha256": run_contract_sha256,
        "out_dir": str(out_dir),
        "method_backend": str(args.method_backend),
        "cm_provenance": cm_provenance,
        "alignment_backend": alignment_backend_provenance,
        "trnascan": trnascan_provenance,
        "provider": args.provider,
        "model": args.model,
        "llm_endpoint": llm_endpoint_provenance,
        "llm_reasoning": bool(args.llm_reasoning),
        "use_llm": (not bool(args.no_llm)) if args.method_backend == "agent" else False,
        "agent_enable_planning": bool(args.agent_enable_planning),
        "agent_planner_policy": str(args.agent_planner_policy),
        "agent_llm_triage_mode": str(args.agent_llm_triage_mode),
        "agent_soft_risk_policy": dict(
            run_contract["method"]["agent_soft_risk_policy"]
        ),
        "agent_memory_mode": str(args.agent_memory_mode),
        "agent_memory_path": str(args.agent_memory_path),
        "agent_llm_postcheck_enabled": bool(
            getattr(args, "agent_enable_llm_postcheck", False)
        ),
        "agent_deterministic_boundary_refinement_enabled": not bool(
            getattr(args, "agent_disable_deterministic_boundary_refinement", False)
        ),
        "agent_enable_cm_tool": bool(args.agent_enable_cm_tool),
        "agent_enable_cm_root_recovery": bool(
            getattr(args, "agent_enable_cm_root_recovery", False)
        ),
        "agent_canonical_root_strategy": str(args.agent_canonical_root_strategy),
        "agent_enable_legacy_fallback": not bool(args.agent_disable_legacy_fallback),
        "max_workers": int(args.max_workers),
        "llm_global_max_workers": int(args.llm_global_max_workers),
        "llm_max_inflight": int(args.llm_max_inflight),
        "offset": int(args.offset),
        "limit": args.limit,
        "seqnames_file": str(args.seqnames_file) if args.seqnames_file else "",
        "rerun_failed": bool(args.rerun_failed),
        "rerun_all": effective_rerun_all,
        "resume": bool(args.resume),
        "notes": str(args.notes or ""),
        "git_commit": git_commit(),
        "git_worktree_dirty": git_worktree_dirty(),
        "started_at": now_iso(),
        "runtime_overrides": {
            "LLM_PROVIDER": runtime_overrides.get("LLM_PROVIDER"),
            "DEEPSEEK_MODEL": runtime_overrides.get("DEEPSEEK_MODEL"),
            "MINIMAX_MODEL": runtime_overrides.get("MINIMAX_MODEL"),
            "MIMO_MODEL": runtime_overrides.get("MIMO_MODEL"),
            "OLLAMA_MODEL": runtime_overrides.get("OLLAMA_MODEL"),
            "LLM_STREAM": runtime_overrides.get("LLM_STREAM"),
            "LLM_GLOBAL_MAX_WORKERS": runtime_overrides.get("LLM_GLOBAL_MAX_WORKERS"),
            "LLM_DISPATCHER_MAX_INFLIGHT": runtime_overrides.get("LLM_DISPATCHER_MAX_INFLIGHT"),
            "DEEPSEEK_THINKING": runtime_overrides.get("DEEPSEEK_THINKING"),
            "AGENT_ENABLE_PLANNING": runtime_overrides.get("AGENT_ENABLE_PLANNING"),
            "AGENT_LLM_TRIAGE_MODE": runtime_overrides.get("AGENT_LLM_TRIAGE_MODE"),
            "AGENT_SOFT_RISK_MIN_CM_POSTERIOR": runtime_overrides.get(
                "AGENT_SOFT_RISK_MIN_CM_POSTERIOR"
            ),
            "AGENT_SOFT_RISK_REVIEW_INSERTIONS": runtime_overrides.get(
                "AGENT_SOFT_RISK_REVIEW_INSERTIONS"
            ),
            "AGENT_SOFT_RISK_REVIEW_BROAD_CM": runtime_overrides.get(
                "AGENT_SOFT_RISK_REVIEW_BROAD_CM"
            ),
            "AGENT_SOFT_RISK_REVIEW_LOCAL_COORDINATE_AMBIGUITY": runtime_overrides.get(
                "AGENT_SOFT_RISK_REVIEW_LOCAL_COORDINATE_AMBIGUITY"
            ),
            "AGENT_SOFT_RISK_REVIEW_ACCEPTOR_TAIL": runtime_overrides.get(
                "AGENT_SOFT_RISK_REVIEW_ACCEPTOR_TAIL"
            ),
            "AGENT_ACCEPTOR_TAIL_REFERENCE_TOP_K": runtime_overrides.get(
                "AGENT_ACCEPTOR_TAIL_REFERENCE_TOP_K"
            ),
            "AGENT_ACCEPTOR_TAIL_MIN_REFERENCE_SUPPORT": runtime_overrides.get(
                "AGENT_ACCEPTOR_TAIL_MIN_REFERENCE_SUPPORT"
            ),
            "AGENT_LOCAL_AMBIGUITY_REFERENCE_TOP_K": runtime_overrides.get(
                "AGENT_LOCAL_AMBIGUITY_REFERENCE_TOP_K"
            ),
            "AGENT_LOCAL_AMBIGUITY_MIN_REFERENCE_SUPPORT": runtime_overrides.get(
                "AGENT_LOCAL_AMBIGUITY_MIN_REFERENCE_SUPPORT"
            ),
            "AGENT_SOFT_RISK_MIN_LOCAL_RESOLUTION_SCORE_GAIN": runtime_overrides.get(
                "AGENT_SOFT_RISK_MIN_LOCAL_RESOLUTION_SCORE_GAIN"
            ),
            "AGENT_SOFT_RISK_MIN_CROSS_BACKEND_SCORE_GAIN": runtime_overrides.get(
                "AGENT_SOFT_RISK_MIN_CROSS_BACKEND_SCORE_GAIN"
            ),
            "AGENT_MEMORY_MODE": runtime_overrides.get("AGENT_MEMORY_MODE"),
            "AGENT_MEMORY_PATH": runtime_overrides.get("AGENT_MEMORY_PATH"),
            "AGENT_ENABLE_CM_TOOL": runtime_overrides.get("AGENT_ENABLE_CM_TOOL"),
            "AGENT_ENABLE_LLM_POSTCHECK": runtime_overrides.get(
                "AGENT_ENABLE_LLM_POSTCHECK"
            ),
            "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT": runtime_overrides.get(
                "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT"
            ),
            "AGENT_CANONICAL_ROOT_STRATEGY": runtime_overrides.get(
                "AGENT_CANONICAL_ROOT_STRATEGY"
            ),
            "AGENT_ENABLE_LEGACY_FALLBACK": runtime_overrides.get(
                "AGENT_ENABLE_LEGACY_FALLBACK"
            ),
        },
        "target_case_count": len(cases),
        "status": "running",
        "attempt": previous_attempt + 1,
        "completed_case_count": 0,
        "pending_case_count": 0,
        "last_checkpoint_at": now_iso(),
    }
    if store is not None:
        store.register_run(manifest)
        upload_inputs()
    persist_manifest(manifest)

    pending_cases = [
        case
        for case in cases
        if should_run_case(
            case.seqname,
            stored_result(case.seqname),
            rerun_all=effective_rerun_all,
            rerun_failed=bool(args.rerun_failed),
        )
    ]
    manifest["pending_case_count"] = len(pending_cases)
    manifest["completed_case_count"] = len(cases) - len(pending_cases)
    manifest["last_checkpoint_at"] = now_iso()
    persist_manifest(manifest)
    LOG.info(
        "loaded_cases=%d pending_cases=%d storage=%s",
        len(cases),
        len(pending_cases),
        "mysql+minio" if store is not None else "local",
    )

    completed = 0
    store_failure = ""
    store_failure_lock = threading.Lock()
    # Every finished case is written to MySQL and MinIO as it completes, but the
    # upload is not the case worker's job: a 2530-case run otherwise spends more
    # time waiting on the artifact store than computing coordinates.
    writer_count = max(1, min(12, int(args.max_workers) // 4))
    store_writer = ThreadPoolExecutor(max_workers=writer_count, thread_name_prefix="bench-store")

    def persist_case(
        seqname: str,
        result: Dict[str, Any],
        previous_row: Optional[Mapping[str, Any]],
        attempt_no: int,
    ) -> None:
        nonlocal store_failure
        if store is None:
            return
        if previous_row is not None:
            store.put_attempt(
                seqname,
                int(previous_row.get("attempt") or attempt_no or 1),
                outcome=str(previous_row.get("status") or ""),
                error=str(previous_row.get("error") or ""),
                latency_sec=previous_row.get("elapsed_sec"),
                usage={
                    key: previous_row.get(key)
                    for key in (
                        "llm_api_call_count",
                        "llm_prompt_tokens",
                        "llm_completion_tokens",
                        "llm_total_tokens",
                        "technical_issue_count",
                    )
                },
                object_key=str(previous_row.get("trace_key") or ""),
            )
        try:
            store.put_case(result, attempt=attempt_no + 1)
        except BenchmarkStoreError as exc:
            with store_failure_lock:
                first_failure = not store_failure
                store_failure = str(exc)
            if first_failure:
                LOG.error("storage write failed for %s; stopping the run: %s", seqname, exc)
            else:
                LOG.error("storage write failed for %s: %s", seqname, exc)

    # Keep finished results in memory so the summary does not have to read every
    # trace back from MinIO right after it was written.
    done_results: Dict[str, Dict[str, Any]] = {}
    last_checkpoint_monotonic = time.monotonic()
    checkpoint_interval_s = 20.0
    if pending_cases:
        worker_count = min(max(1, int(args.max_workers)), len(pending_cases))
        LOG.info("starting benchmark with worker_count=%d", worker_count)
        pool = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="numbering-bench",
        )
        future_to_case: Dict[Any, GoldCase] = {}
        pending_iter = iter(pending_cases)

        def submit_until_full() -> None:
            while len(future_to_case) < worker_count:
                try:
                    case = next(pending_iter)
                except StopIteration:
                    return
                future_to_case[pool.submit(run_one_case, case, args, runtime_overrides)] = case

        submit_until_full()
        try:
            while future_to_case and not store_failure:
                done, _ = wait(future_to_case, return_when=FIRST_COMPLETED)
                for future in done:
                    case = future_to_case.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {
                            "status": "error",
                            "seqname": case.seqname,
                            "input_len": len(case.input_seq),
                            "input_seq": case.input_seq,
                            "benchmark_case": dict(case.benchmark_meta or {}),
                            "elapsed_sec": 0.0,
                            "error": f"{type(exc).__name__}: {exc}",
                            "method_backend": str(args.method_backend),
                            "provider": args.provider,
                            "model": args.model,
                            "started_at": now_iso(),
                            "finished_at": now_iso(),
                        }
                    case_path = case_result_path(cases_dir, case.seqname)
                    previous_row = stored_result(case.seqname)
                    # The A/B difference summary is measured here, against the same
                    # gold record the review page renders, so the stored index never
                    # has to re-derive it from accuracy columns.
                    attach_case_comparison(
                        result,
                        gold_labels=case.gold_labels,
                        gold_bases=case.gold_bases,
                    )
                    if store is not None:
                        store_writer.submit(
                            persist_case,
                            case.seqname,
                            result,
                            previous_row,
                            int(previous_attempt),
                        )
                    done_results[case.seqname] = result
                    if args.keep_local:
                        previous_result = read_case_result(case_path)
                        if previous_result is not None:
                            atomic_write_json(
                                out_dir / "case_attempts" / case_digest(case.seqname)
                                / f"attempt-{previous_attempt:03d}.json",
                                previous_result,
                            )
                        atomic_write_json(case_path, result)
                    completed += 1
                    manifest["completed_case_count"] = len(cases) - len(pending_cases) + completed
                    manifest["last_checkpoint_at"] = now_iso()
                    if time.monotonic() - last_checkpoint_monotonic >= checkpoint_interval_s:
                        persist_manifest(manifest)
                        if store is not None:
                            store.checkpoint(
                                completed=manifest["completed_case_count"],
                                pending=max(0, len(pending_cases) - completed),
                            )
                        last_checkpoint_monotonic = time.monotonic()
                    if store_failure:
                        for pending_future in future_to_case:
                            pending_future.cancel()
                        break
                    submit_until_full()
                    if args.progress_every > 0 and (
                        completed % args.progress_every == 0
                        or completed == len(pending_cases)
                    ):
                        LOG.info("progress completed=%d/%d", completed, len(pending_cases))
        finally:
            pool.shutdown(wait=True)
        store_writer.shutdown(wait=True)
        persist_manifest(manifest)
        if store is not None:
            store.checkpoint(
                completed=manifest["completed_case_count"],
                pending=max(0, len(pending_cases) - completed),
            )

    rows = _collect_case_rows(
        store,
        cases,
        cases_dir,
        keep_local=args.keep_local,
        cached=done_results,
    )
    summary = summarize_results(rows)
    summary["run_id"] = args.run_id
    summary["method_backend"] = str(args.method_backend)
    summary["provider"] = args.provider
    summary["model"] = args.model
    summary["agent_planner_policy"] = str(args.agent_planner_policy)
    summary["agent_llm_postcheck_enabled"] = bool(
        getattr(args, "agent_enable_llm_postcheck", False)
    )
    summary["agent_deterministic_boundary_refinement_enabled"] = not bool(
        getattr(args, "agent_disable_deterministic_boundary_refinement", False)
    )
    summary["input_tsv"] = str(args.input_tsv)
    summary["out_dir"] = str(out_dir)
    summary["benchmark_selection"] = dict(run_contract["selection"])
    summary["input_files"] = input_files
    summary["run_contract_sha256"] = run_contract_sha256

    if args.keep_local:
        atomic_write_json(summary_json_path, summary)
        write_summary_txt(summary_txt_path, summary)
        write_case_results_tsv(case_results_path, rows)

    manifest["finished_at"] = now_iso()
    manifest["status"] = "failed" if store_failure else "completed"
    manifest["completed_case_count"] = len(cases)
    manifest["pending_case_count"] = 0
    manifest["last_checkpoint_at"] = now_iso()
    manifest["summary_json"] = str(summary_json_path)
    manifest["summary_txt"] = str(summary_txt_path)
    manifest["case_results_tsv"] = str(case_results_path)
    manifest["counts"] = dict(summary.get("counts") or {})
    if store is not None:
        store.put_artifact("summary.json", summary)
        store.put_artifact("summary.txt", summary_txt_text(summary), content_type="text/plain")
        store.put_artifact(
            "case_results.tsv",
            case_results_tsv_text(rows),
            content_type="text/tab-separated-values",
        )
        if log_handler is not None:
            store.put_artifact("run.log", log_handler.text(), content_type="text/plain")
        persist_manifest(manifest)
        store.seal_run(
            summary=summary,
            counts=dict(summary.get("counts") or {}),
            manifest_key=store.object_key("manifest.json"),
            finished_at=manifest["finished_at"],
        )
        store.write_pointer(out_dir / "RUN.json")
    else:
        persist_manifest(manifest)

    LOG.info(
        "benchmark finished status=%s ok_cases=%s error_cases=%s",
        manifest["status"],
        summary["counts"]["ok_cases"],
        summary["counts"]["error_cases"],
    )
    if store_failure:
        raise SystemExit(f"benchmark stopped by storage failure: {store_failure}")


if __name__ == "__main__":
    main()
