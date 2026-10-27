from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import math
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.infra.storage import sequence_repository as sequence_db
from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.sprinzl_coordinates import order_key as sprinzl_order_key
from backend.domain.alignment.preparation.annotation import annotate_trna_sequences
from backend.domain.alignment.preparation.segmentation import compare_input_template
from backend.domain.benchmarking import case_index
from backend.application.alignment.scientific_interpretation import (
    ScientificInterpretationError,
    generate_scientific_interpretation,
)


ROOT = Path(__file__).resolve().parents[4]
# The MySQL answer table is the gold standard: it holds one active answer per
# sequence, and every score is graded against it on read.  These paths are the
# offline fallback used when MySQL is not configured, and a scratch export
# target; they are staged from the database, never committed.
GOLD_SCRATCH_DIR = ROOT / ".runtime" / "gold"
DEFAULT_GOLD_TSV = GOLD_SCRATCH_DIR / "mature_benchmark.tsv"
DEFAULT_CURATED_GOLD_TSV = GOLD_SCRATCH_DIR / "mature_benchmark.curated.tsv"
DEFAULT_EXPORT_TSV = GOLD_SCRATCH_DIR / "export.tsv"
DEFAULT_RUNS_DIR = ROOT / ".runtime" / "runs"
# The curation page opens the sealed cohort the manuscript reports; any other
# registered run is reachable with ?run_id=.
DEFAULT_RUN_ID = "20261020-164533-deepseekv4-flash-guangdong"
DEFAULT_CASE_RESULTS_NAME = "case_results.tsv"
DEFAULT_CORRECTIONS_JSONL = GOLD_SCRATCH_DIR / "manual_corrections.jsonl"
DEFAULT_DRAFTS_JSONL = GOLD_SCRATCH_DIR / "manual_drafts.jsonl"
DEFAULT_STRUCTURE_CACHE_JSONL = GOLD_SCRATCH_DIR / "secondary_structures.jsonl"

INSERTION_LABEL_RE = re.compile(r"^(?P<anchor>-?\d+[A-Za-z]?|V\d+)i\d+$")
STRUCTURE_MODEL_ORDER = ("tRNAscan-SE", "BPfold", "RNAfold", "AYLM-2D")
# A committed decision of any kind.  The queue filter exposes these three
# statuses as one group, so the grouping lives here rather than in the page.
COMPLETED_STATUSES = ("accepted_gold", "accepted_model", "manual_corrected")
NEEDS_REVIEW_STATUS = "needs_review"
AUTO_AGREE_STATUS = "auto_agree"
CLASSIC_REREVIEW_SCOPE = "classic_three_domain_mismatch"
CLASSIC_REREVIEW_ROUND = "classic-three-domain-mismatch-v1"
CLASSIC_BROAD_SOURCES = frozenset({"archaea", "bacteria", "cytosolic_eukaryote"})
ALL_MISMATCH_REREVIEW_SCOPE = "all_evaluation_mismatch"
ALL_MISMATCH_REREVIEW_ROUND = "all-evaluation-mismatch-v1"
KINGDOM_LABELS_ZH = {
    "ARCHAE": "古菌",
    "Archaea": "古菌",
    "Bacteria": "细菌",
    "EUBACT": "细菌",
    "Eukaryota": "真核生物",
    "VIRUS": "病毒",
    "CHLORO": "叶绿体",
    "CYANEL": "蓝小体",
    "MI ANI": "动物线粒体",
    "MI PLA": "植物线粒体",
    "MI SIN": "单细胞真核线粒体",
    "CY ANI": "动物细胞质",
    "CY PLA": "植物细胞质",
    "CY SIN": "单细胞真核细胞质",
}


@dataclass(frozen=True)
class ResidueMap:
    labels: list[str]
    bases: list[str]

    @property
    def sequence(self) -> str:
        return "".join(self.bases)


def _now_iso() -> str:
    """Timestamps order a draft against the decision that supersedes it, and a
    draft autosaved in the same second as the submit would tie at whole-second
    resolution, so the stamp keeps microseconds."""
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _normalize_review_scope(scope: str) -> str:
    clean = str(scope or "").strip().lower()
    if clean == CLASSIC_REREVIEW_SCOPE:
        return CLASSIC_REREVIEW_SCOPE
    if clean == ALL_MISMATCH_REREVIEW_SCOPE:
        return ALL_MISMATCH_REREVIEW_SCOPE
    return ""


def _review_round_for_scope(scope: str) -> str:
    normalized = _normalize_review_scope(scope)
    if normalized == CLASSIC_REREVIEW_SCOPE:
        return CLASSIC_REREVIEW_ROUND
    if normalized == ALL_MISMATCH_REREVIEW_SCOPE:
        return ALL_MISMATCH_REREVIEW_ROUND
    return ""


def _gold_table_for_scope(scope: str) -> tuple[tuple[str, ...], dict[str, dict[str, str]]]:
    """The answer table: one active gold standard per sequence.

    Reads the materialized ``trna_gold`` rows, so a submitted curation decision
    is visible to scoring and to the review page without re-deriving anything
    from the audit log.  ``scope`` no longer selects a different reference: the
    library keeps a single active answer, and the per-round reference a reviewer
    actually saw is recorded inside ``trna_annotations``.  TSV stays only as the
    offline fallback when MySQL is not configured.
    """
    fieldnames, matrix = _store_gold_matrix("gold")
    if fieldnames:
        return fieldnames, matrix
    fieldnames, rows = _table_by_seqname(DEFAULT_GOLD_TSV)
    return fieldnames, rows


def _raw_reference_table() -> tuple[tuple[str, ...], dict[str, dict[str, str]]]:
    """The untouched tRNAdb numbering, rendered as a table for panel A."""
    fieldnames, matrix = _store_gold_matrix("raw")
    if fieldnames:
        return fieldnames, matrix
    if sequence_db.enabled():
        rows = sequence_db.baseline_rows()
        if rows:
            fields = set()
            for row in rows.values():
                fields.update(str(k) for k in row if k != "seqname")
            numbering = sorted(fields, key=_numbering_sort_key)
            fieldnames = ("seqname", *numbering)
            normalized = {
                name: {str(k): str(v or "") for k, v in row.items()}
                for name, row in rows.items()
            }
            return fieldnames, normalized
    fieldnames, rows = _table_by_seqname(DEFAULT_GOLD_TSV)
    return fieldnames, rows


@lru_cache(maxsize=2)
def _store_gold_matrix(which: str) -> tuple[tuple[str, ...], dict[str, dict[str, str]]]:
    """Render ``trna_gold`` residue arrays as a seqname-keyed position matrix."""
    if not sequence_db.enabled() or not sequence_db.gold_is_populated():
        return (), {}
    rows = sequence_db.gold_rows()
    fieldnames, matrix = _gold_table_from_residue_maps(
        ((str(row["seqname"]), list(row[f"{which}_labels"]), list(row[f"{which}_bases"]))
         for row in rows.values()),
    )
    return fieldnames, matrix


@lru_cache(maxsize=1)
def _base_numbering() -> list[str]:
    """Canonical Sprinzl column order used as the seed for insertion columns."""
    if sequence_db.enabled():
        rows = sequence_db.baseline_rows()
        if rows:
            fields = set()
            for row in rows.values():
                fields.update(str(k) for k in row if k != "seqname")
            return sorted(fields, key=_numbering_sort_key)
    fieldnames, _ = _table_by_seqname(DEFAULT_GOLD_TSV)
    return list(fieldnames[1:])


def _gold_table_from_residue_maps(
    entries: Iterable[tuple[str, list[str], list[str]]],
) -> tuple[tuple[str, ...], dict[str, dict[str, str]]]:
    numbering = _base_numbering()
    collected: list[tuple[str, ResidueMap]] = []
    for seqname, labels, bases in entries:
        residue_map = ResidueMap(
            labels=[str(v) for v in labels],
            bases=[_safe_base(v) for v in bases],
        )
        collected.append((seqname, residue_map))
    expanded = _expanded_numbering(numbering, [label for _, rm in collected for label in rm.labels])
    matrix = {
        seqname: dict(zip(expanded, _row_from_residue_map(residue_map, expanded)))
        for seqname, residue_map in collected
    }
    return ("seqname", *expanded), matrix


def active_answer_rows() -> dict[str, ResidueMap]:
    """Every sequence's active answer, read straight from ``trna_gold``.

    Any offline job that used to parse a gold TSV takes its sequences and its
    numbering from here instead, so the library stays the single answer source.
    """
    if not sequence_db.enabled():
        return {}
    answers: dict[str, ResidueMap] = {}
    for seqname, row in sequence_db.gold_rows().items():
        answers[str(seqname)] = ResidueMap(
            labels=[str(v) for v in (row.get("gold_labels") or [])],
            bases=[_safe_base(v) for v in (row.get("gold_bases") or [])],
        )
    return answers


def table_from_gold_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], dict[str, dict[str, str]]]:
    """Render stored answer rows as a seqname-keyed position matrix."""
    return _gold_table_from_residue_maps(
        (str(row["seqname"]), list(row["gold_labels"]), list(row["gold_bases"])) for row in rows
    )


def clear_gold_caches() -> None:
    """Drop cached answer tables so a new decision is visible immediately."""
    _store_gold_matrix.cache_clear()
    _base_numbering.cache_clear()
    _case_index_cached.cache_clear()
    _latest_corrections.cache_clear()
    _latest_round_corrections.cache_clear()
    _latest_drafts.cache_clear()


def _iso_or_none(value: Any) -> str | None:
    """Render a stored DATETIME as a plain ISO string for the UI."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------- answer table
# ``trna_gold`` holds exactly one active answer per sequence.  The status words
# below say where that answer came from, which is what lets a score be read
# back as "graded against a human decision" or "graded against tRNAdb".
GOLD_SOURCE_BY_DECISION = {
    "accepted_gold": "adopted_gold",
    "accepted_model": "adopted_model",
    "manual_corrected": "manual",
    "unsure": "unsure",
    "excluded": "excluded",
}


def _gold_row_for(
    seqname: str,
    raw_map: ResidueMap,
    record: Mapping[str, Any] | None,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine the raw tRNAdb record with the latest committed decision.

    ``existing`` carries the answer row being refreshed, which keeps the
    eligibility axis independent of the numbering: a sequence can be dropped
    from scoring without replacing its answer, and a rebuild does not undo that.
    """
    row: dict[str, Any] = {
        "seqname": seqname,
        "raw_labels": list(raw_map.labels),
        "raw_bases": list(raw_map.bases),
        "gold_labels": list(raw_map.labels),
        "gold_bases": list(raw_map.bases),
        "gold_source": "raw",
        "decided_at": None,
        "decision_hash": None,
        "decision_run_id": None,
        "evaluation_allowed": True,
        "exclusion_reason": None,
        "excluded_at": None,
    }
    if existing is not None and existing.get("evaluation_allowed") is False:
        row["evaluation_allowed"] = False
        row["exclusion_reason"] = existing.get("exclusion_reason") or "non_canonical_trna"
        row["excluded_at"] = existing.get("excluded_at")
    if not record:
        return row
    payload = dict(record.get("payload") or {})
    decision = str(payload.get("status") or payload.get("decision") or record.get("status") or "").strip()
    labels = [str(v).strip() for v in (payload.get("curated_labels") or [])]
    bases = [_safe_base(v) for v in (payload.get("bases_by_residue") or [])]
    if decision == "unsure" and payload.get("review_round"):
        labels = [str(v).strip() for v in (payload.get("baseline_labels") or [])]
    usable = bool(labels) and len(labels) == len(bases)
    source = GOLD_SOURCE_BY_DECISION.get(decision, "raw")

    if source == "excluded":
        row["gold_source"] = "excluded"
        row["evaluation_allowed"] = False
        row["exclusion_reason"] = row["exclusion_reason"] or str(
            payload.get("exclusion_reason") or "non_canonical_trna"
        )
        row["excluded_at"] = row["excluded_at"] or sequence_db.timestamp(record)
    elif usable and source != "raw":
        row["gold_labels"] = labels
        row["gold_bases"] = bases
        row["gold_source"] = source
    if decision in GOLD_SOURCE_BY_DECISION:
        row["decided_at"] = sequence_db.timestamp(payload)
        row["decision_hash"] = record.get("record_hash")
        # An adopted model answer stays tied to the run that produced it, so the
        # provenance survives even after that run is no longer the default one.
        row["decision_run_id"] = str(payload.get("run_id") or record.get("run_id") or "") or None
    return row


def rebuild_gold_table(*, write: bool = True, seqnames: Iterable[str] | None = None) -> dict[str, Any]:
    """Materialize the single active answer for every stored sequence.

    Idempotent: it re-derives from the immutable tRNAdb record plus the newest
    committed decision, so it can be run after any curation round to check that
    ``trna_gold`` and the audit log still agree.
    """
    if not sequence_db.enabled():
        raise RuntimeError("rebuild_gold_table requires the sequence repository")
    fieldnames, raw_rows = _raw_reference_table()
    if not fieldnames:
        raise RuntimeError("no raw tRNAdb reference rows are available")
    numbering = list(fieldnames[1:])
    wanted = {str(v) for v in seqnames} if seqnames is not None else None
    records = sequence_db.latest_formal_records()
    current = sequence_db.gold_rows(sorted(wanted) if wanted is not None else sorted(raw_rows))
    rows: list[dict[str, Any]] = []
    for seqname, raw_row in raw_rows.items():
        if wanted is not None and seqname not in wanted:
            continue
        raw_map = _gold_residue_map(raw_row, numbering)
        rows.append(
            _gold_row_for(seqname, raw_map, records.get(seqname), current.get(seqname))
        )
    stats: dict[str, int] = {}
    changed = 0
    if write:
        with sequence_db.connection() as conn:
            with conn.cursor() as cursor:
                for start in range(0, len(rows), 1000):
                    sequence_db.put_gold_many(cursor, rows[start:start + 1000])
                    conn.commit()
    for row in rows:
        stats[row["gold_source"]] = stats.get(row["gold_source"], 0) + 1
        if row["gold_labels"] != row["raw_labels"]:
            changed += 1
    clear_gold_caches()
    return {
        "rows": len(rows),
        "answer_differs_from_tRNAdb": changed,
        "decisions_available": len(records),
        "by_source": stats,
        "written": write,
    }


def refresh_gold_row(seqname: str) -> dict[str, Any] | None:
    """Refresh one answer row right after its decision is committed."""
    if not sequence_db.enabled():
        return None
    clean = str(seqname or "").strip()
    if not clean:
        return None
    fieldnames, raw_rows = _raw_reference_table()
    raw_row = raw_rows.get(clean)
    if raw_row is None:
        return None
    row = _gold_row_for(
        clean,
        _gold_residue_map(raw_row, list(fieldnames[1:])),
        sequence_db.latest_formal_record(clean),
        sequence_db.gold_rows([clean]).get(clean),
    )
    with sequence_db.connection() as conn:
        with conn.cursor() as cursor:
            sequence_db.put_gold(cursor, row)
        conn.commit()
    clear_gold_caches()
    return row


def set_case_evaluation(seqname: str, *, allowed: bool, reason: str = "") -> dict[str, Any]:
    """Decide whether one sequence counts toward a benchmark score.

    The numbering is untouched: only ``evaluation_allowed`` flips, so a run's
    scores recompute around the decision while the answer stays reviewable.
    """
    if not sequence_db.enabled():
        raise RuntimeError("Scoring eligibility is stored in the MySQL answer table.")
    record = sequence_db.set_evaluation_allowed(seqname, allowed, reason)
    clear_gold_caches()
    return record


def case_digest(seqname: str) -> str:
    return hashlib.sha1(seqname.encode("utf-8")).hexdigest()[:16]


# The canonical base/region/diff helpers live in the benchmarking layer, so the
# run index written by the benchmark and the A/B view served here cannot drift.
_safe_base = case_index.safe_base
_safe_region = case_index.safe_region


def _structure_tokens(raw: str) -> list[str]:
    out: list[str] = []
    for ch in str(raw or ""):
        if ch in ">({[":
            out.append(">")
        elif ch in "<)}]":
            out.append("<")
        elif ch == ".":
            out.append(".")
    return out


def _norm_sequence(raw: str) -> str:
    return "".join(ch for ch in str(raw or "").upper().replace("T", "U") if ch in "ACGUN")


def _seq_sha1(sequence: str) -> str:
    return hashlib.sha1(_norm_sequence(sequence).encode("utf-8")).hexdigest()


def _fit_structure_tokens(sequence: str, struct_seq: str, raw_structure: str) -> list[str]:
    tokens = _structure_tokens(raw_structure)
    target = _norm_sequence(sequence)
    if not target or not tokens:
        return []
    if len(tokens) == len(target):
        return tokens
    source_seq = _norm_sequence(struct_seq)
    if source_seq and len(tokens) == len(source_seq):
        idx = target.find(source_seq)
        if idx >= 0:
            return ["."] * idx + tokens + ["."] * (len(target) - idx - len(tokens))
    return []


def _structure_payload(
    *,
    sequence: str,
    source: str,
    raw_structure: str,
    struct_seq: str = "",
    status: str = "",
    errors: str = "",
    template_name: str = "",
) -> dict[str, Any]:
    tokens = _fit_structure_tokens(sequence, struct_seq, raw_structure)
    if not tokens:
        return {}
    return {
        "annotation_source": source,
        "secondary_structure": "".join(tokens),
        "structure_by_residue": tokens,
        "structure_status": status,
        "structure_errors": errors,
        "template_name": template_name,
    }


def _structure_from_annotation_entry(entry: dict[str, Any], sequence: str) -> dict[str, Any]:
    source = str(entry.get("UsedModel") or "").strip()
    payload = _structure_payload(
        sequence=sequence,
        source=source,
        raw_structure=str(entry.get("SecondaryStructure") or ""),
        struct_seq=str(entry.get("StructFileSequence") or entry.get("StructSeq") or ""),
        status=str(entry.get("SegmentationStatus") or ""),
        errors=str(entry.get("SegmentationErrors") or ""),
    )
    if payload and source:
        payload["structure_meta"] = _annotation_structure_meta(entry, source)
        return payload

    predictions = entry.get("ModelPredictions")
    if not isinstance(predictions, dict):
        return {}
    for model in STRUCTURE_MODEL_ORDER:
        pred = predictions.get(model)
        if not isinstance(pred, dict):
            continue
        payload = _structure_payload(
            sequence=sequence,
            source=f"{model}（原始预测）",
            raw_structure=str(pred.get("SecondaryStructure") or ""),
            struct_seq=str(pred.get("StructSeq") or pred.get("StructFileSequence") or ""),
            status=str(entry.get("SegmentationStatus") or ""),
            errors=str(entry.get("SegmentationErrors") or ""),
        )
        if payload:
            payload["structure_meta"] = _annotation_structure_meta(pred, model)
            return payload
    return {}


def _annotation_structure_meta(entry: dict[str, Any], source: str) -> dict[str, Any]:
    meta: dict[str, Any] = {"predictor": source}
    isotype = str(entry.get("Isotype") or "").strip()
    anticodon = str(entry.get("Anticodon") or "").strip().upper().replace("T", "U")
    if isotype:
        meta["trnascan_isotype"] = isotype
    if anticodon:
        meta["trnascan_anticodon"] = anticodon
    for source_key, target_key in (
        ("AnticodonStart", "trnascan_anticodon_start"),
        ("AnticodonEnd", "trnascan_anticodon_end"),
    ):
        value = entry.get(source_key)
        try:
            value_int = int(value)
        except Exception:
            continue
        if value_int:
            meta[target_key] = value_int
    return meta


@lru_cache(maxsize=1)
def _structure_cache(path: Path = DEFAULT_STRUCTURE_CACHE_JSONL) -> dict[str, dict[str, dict[str, Any]]]:
    by_seqname: dict[str, dict[str, Any]] = {}
    by_sha1: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return {"by_seqname": by_seqname, "by_sha1": by_sha1}
    with path.open("r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            seq_sha1 = str(payload.get("seq_sha1") or "").strip()
            seqname = str(payload.get("seqname") or "").strip()
            if seqname:
                by_seqname[seqname] = payload
            if seq_sha1:
                by_sha1[seq_sha1] = payload
    return {"by_seqname": by_seqname, "by_sha1": by_sha1}


def _structure_from_cache(seqname: str, sequence: str) -> dict[str, Any]:
    cache = _structure_cache()
    seq_sha1 = _seq_sha1(sequence)
    cached = cache["by_seqname"].get(seqname) if seqname else None
    if cached and str(cached.get("seq_sha1") or "") != seq_sha1:
        cached = None
    if cached is None:
        cached = cache["by_sha1"].get(seq_sha1)
    if not cached:
        return {}
    raw_tokens = cached.get("structure_by_residue")
    tokens = [str(v) for v in raw_tokens] if isinstance(raw_tokens, list) else []
    if len(tokens) != len(_norm_sequence(sequence)):
        tokens = _fit_structure_tokens(
            sequence,
            str(cached.get("struct_sequence") or ""),
            str(cached.get("secondary_structure") or ""),
        )
    source = str(cached.get("annotation_source") or cached.get("source") or "").strip()
    meta = dict(cached.get("structure_meta") or {}) if isinstance(cached.get("structure_meta"), dict) else {}
    return {
        "annotation_source": source,
        "secondary_structure": "".join(tokens),
        "structure_by_residue": tokens,
        "structure_status": str(cached.get("structure_status") or ""),
        "structure_errors": str(cached.get("structure_errors") or ""),
        "template_name": str(cached.get("template_name") or ""),
        "structure_meta": meta,
        "cache": str(DEFAULT_STRUCTURE_CACHE_JSONL.relative_to(ROOT)),
    }


def _numbering_sort_key(label: str) -> tuple[int, int, int, str]:
    clean = str(label or "").strip()
    try:
        # V11..V17, V1..V5, V27..V21 belong between positions 45 and 46.
        # Numeric/lexical ordering would change the input sequence itself.
        return (*sprinzl_order_key(clean), clean)
    except ValueError:
        return (300000, 0, 0, clean)


def _read_tsv_dicts(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as fin:
        reader = csv.DictReader(fin, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fieldnames, rows


@lru_cache(maxsize=8)
def _table_by_seqname(path: Path) -> tuple[tuple[str, ...], dict[str, dict[str, str]]]:
    """Cache the small reference tables used by every case-detail request."""
    fieldnames, rows = _read_tsv_dicts(path)
    if not fieldnames or fieldnames[0] != "seqname":
        raise ValueError(f"Unexpected gold TSV header: {path}")
    by_name = {
        str(row.get("seqname") or "").strip(): row
        for row in rows
        if str(row.get("seqname") or "").strip()
    }
    return tuple(fieldnames), by_name


def _local_run_directory(run_id: str) -> Path:
    """A run kept on this machine, used only when the store is unreachable."""
    root = DEFAULT_RUNS_DIR.resolve()
    current = root / str(run_id or "")
    if (not run_id or Path(run_id).name != run_id or run_id in {".", ".."}
            or current.resolve().parent != root or not current.is_dir()):
        raise FileNotFoundError(f"Benchmark run is not retained: {run_id}")
    return current


@lru_cache(maxsize=4)
def _store_for(run_id: str):
    """Durable run handle: MySQL index plus MinIO traces.

    ``None`` means the store could not be reached, not that the run is missing;
    callers then fall back to a locally retained copy of the same run.
    """
    clean = str(run_id or "").strip()
    if not clean or Path(clean).name != clean or clean in {".", ".."}:
        return None
    if (os.getenv("BENCHMARK_STORE_ENABLED") or "").strip().lower() in {"0", "false", "no"}:
        return None
    try:
        from backend.infra.storage.benchmark_run_store import BenchmarkRunStore

        store = BenchmarkRunStore(clean)
        store.ping()
    except Exception:
        return None
    return store


def ensure_run_available(run_id: str) -> None:
    """Raise FileNotFoundError when neither the store nor disk knows this run."""
    store = _store_for(run_id)
    if store is not None:
        if store.record_counts().get("case"):
            return
    _local_run_directory(run_id)


RUN_BATCH_RE = re.compile(r"^(?P<date>\d{8})-(?P<time>\d{4,6})-(?P<tail>.+)$")
CONTROL_BATCH_LABELS_ZH = {
    "cm-direct": "对照 · 直接 CM 比对",
    "harness-cm-root": "对照 · CM 根候选",
    "harness-retrieval-dp": "对照 · 无 CM 结构证据",
    "fixed-planner": "对照 · 确定性规划",
    "raw-llm": "对照 · 仅语言模型",
}


def _run_batch_label(run_id: str) -> str:
    """Readable batch stamp taken from the run id, e.g. 2026-10-20 16:45."""
    clean = str(run_id or "").strip()
    match = RUN_BATCH_RE.match(clean)
    if not match:
        return clean
    date = match.group("date")
    clock = match.group("time")
    return f"{date[:4]}-{date[4:6]}-{date[6:8]} {clock[:2]}:{clock[2:4]}"


def _run_batch_role(run_id: str) -> str:
    """What this batch is, so the selector cannot be confused with the cohort."""
    clean = str(run_id or "").strip()
    match = RUN_BATCH_RE.match(clean)
    tail = match.group("tail") if match else clean
    for key, label in CONTROL_BATCH_LABELS_ZH.items():
        if key in tail:
            return label
    if "control-" in tail:
        return f"对照 · {tail.split('control-', 1)[1]}"
    if clean == DEFAULT_RUN_ID:
        return "主队列"
    return "完整运行"


def list_runs() -> dict[str, Any]:
    """Every benchmark batch the curation page can open, newest first.

    MySQL is authoritative.  When the store is unreachable the page still lists
    locally retained runs, so an offline review session keeps working.
    """
    source = "mysql"
    try:
        from backend.infra.storage.benchmark_run_store import BenchmarkRunStore

        store = BenchmarkRunStore.readonly()
        rows = store.run_rows(limit=100)
    except Exception:
        source = "disk"
        rows = [
            {
                "run_id": path.name,
                "state": "local",
                "model_id": "",
                "method_backend": "",
                "gold_sha256": "",
                "n_targets": 0,
                "n_completed": 0,
                "started_at": "",
                "finished_at": "",
            }
            for path in sorted(
                (DEFAULT_RUNS_DIR.iterdir() if DEFAULT_RUNS_DIR.is_dir() else []),
                reverse=True,
            )
            if path.is_dir() and (path / DEFAULT_CASE_RESULTS_NAME).exists()
        ]

    moved: dict[str, dict[str, int]] = {}
    if source == "mysql" and rows:
        names = [str(row.get("run_id") or "") for row in rows]
        try:
            moved = store.revised_answers_for(names)
        except Exception:
            # An unreadable answer table only removes the note; the batch list
            # itself still opens.
            moved = {}

    batches: list[dict[str, Any]] = []
    for row in rows:
        run_id = str(row.get("run_id") or "").strip()
        if not run_id or run_id in {".", ".."}:
            continue
        batches.append(
            {
                "run_id": run_id,
                "label": _run_batch_label(run_id),
                "role": _run_batch_role(run_id),
                "state": str(row.get("state") or ""),
                "model_id": str(row.get("model_id") or ""),
                "method_backend": str(row.get("method_backend") or ""),
                "cases": int(row.get("n_completed") or 0),
                "case_total": int(row.get("n_targets") or 0),
                "gold_sha256": str(row.get("gold_sha256") or "")[:12],
                "answers_revised": int((moved.get(run_id) or {}).get("revised") or 0),
                "answers_missing": int((moved.get(run_id) or {}).get("missing") or 0),
                "started_at": str(row.get("started_at") or ""),
                "is_default": run_id == DEFAULT_RUN_ID,
            }
        )
    return {
        "source": source,
        "default_run_id": DEFAULT_RUN_ID,
        "runs": batches,
    }


def _rows_as_strings(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        name = str(row.get("seqname") or "").strip()
        if not name:
            continue
        out[name] = {
            key: ("" if value is None else str(value)) for key, value in row.items()
        }
    return out


@lru_cache(maxsize=8)
def _run_results_by_seqname(run_id: str) -> dict[str, dict[str, str]]:
    """One row per case from the MySQL index; the local TSV is the fallback."""
    store = _store_for(run_id)
    if store is not None:
        try:
            rows = _rows_as_strings(store.list_rows())
        except Exception:
            rows = {}
        if rows:
            return rows
    # Unknown to the store as well: a run that is not retained anywhere must
    # fail loudly rather than surface an empty queue.
    path = _local_run_directory(run_id) / DEFAULT_CASE_RESULTS_NAME
    if not path.exists():
        return {}
    try:
        _fieldnames, rows = _read_tsv_dicts(path)
    except (OSError, csv.Error):
        return {}
    return {
        str(row.get("seqname") or "").strip(): row
        for row in rows
        if str(row.get("seqname") or "").strip()
    }


@lru_cache(maxsize=1)
def _export_metadata(path: Path = DEFAULT_EXPORT_TSV) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        _fieldnames, rows = _read_tsv_dicts(path)
    except Exception:
        return {}
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        trna_id = str(row.get("tRNA ID") or "").strip()
        sprinzl_id = str(row.get("SprinzlID") or "").strip()
        meta = {
            "trnadb_id": trna_id,
            "sprinzl_id": sprinzl_id,
            "organism": str(row.get("Organism") or "").strip(),
            "kingdom": str(row.get("Kingdom") or "").strip(),
            "kingdom_label": KINGDOM_LABELS_ZH.get(str(row.get("Kingdom") or "").strip(), str(row.get("Kingdom") or "").strip()),
            "amino_acid": str(row.get("Amino Acid") or "").strip(),
            "anticodon": str(row.get("Anticodon") or "").strip().upper().replace("T", "U"),
            "embl_code": str(row.get("EMBL Code") or "").strip(),
        }
        for key in (trna_id, sprinzl_id, f"{trna_id}|{sprinzl_id}" if trna_id and sprinzl_id else ""):
            if key:
                out[key] = meta
    return out


def _metadata_for_seqname(seqname: str) -> dict[str, str]:
    meta = _export_metadata()
    clean = str(seqname or "").strip()
    if clean in meta:
        return dict(meta[clean])
    parts = clean.split("|")
    for part in parts:
        if part in meta:
            return dict(meta[part])
    return {}


def _gold_residue_map(row: dict[str, str], numbering: Iterable[str]) -> ResidueMap:
    labels: list[str] = []
    bases: list[str] = []
    for label in numbering:
        base = _safe_base(row.get(label, ""))
        if base == "-":
            continue
        labels.append(str(label))
        bases.append(base)
    return ResidueMap(labels=labels, bases=bases)


def _parse_alignment_csv(csv_content: str) -> tuple[list[str], list[str], list[str]]:
    return case_index.parse_alignment_csv(csv_content)


def _pred_residue_map(csv_content: str) -> ResidueMap:
    labels, bases = case_index.pred_residue_map(csv_content)
    return ResidueMap(labels=labels, bases=bases)


def _normalize_correction(payload: dict[str, Any]) -> dict[str, Any]:
    correction = dict(payload)
    status = str(correction.get("status") or correction.get("decision") or "").strip()
    if status:
        correction["status"] = status
        correction.setdefault("decision", status)

    labels = correction.get("curated_labels")
    has_labels = isinstance(labels, list) and bool(labels)
    if not has_labels and correction.get("curated_csv"):
        residue_map = _pred_residue_map(str(correction.get("curated_csv") or ""))
        if residue_map.labels:
            correction["curated_labels"] = residue_map.labels
            if not correction.get("bases_by_residue"):
                correction["bases_by_residue"] = residue_map.bases
    return correction


def _gold_csv_content(row: dict[str, str], numbering: list[str]) -> str:
    values = [_safe_base(row.get(label, "")) for label in numbering]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(numbering)
    writer.writerow(values)
    writer.writerow(values)
    return out.getvalue()


def _row_from_residue_map(residue_map: ResidueMap, numbering: list[str]) -> list[str]:
    values = ["-" for _ in numbering]
    for label, base in zip(residue_map.labels, residue_map.bases):
        if label in numbering:
            values[numbering.index(label)] = base
    return values


def _expanded_numbering(numbering: list[str], labels: Iterable[str]) -> list[str]:
    expanded = list(numbering)
    for raw in labels:
        label = str(raw or "").strip()
        if not label or label in expanded:
            continue
        insertion = INSERTION_LABEL_RE.match(label)
        anchor = insertion.group("anchor") if insertion else ""
        if not anchor or anchor not in expanded:
            continue
        insert_at = expanded.index(anchor) + 1
        while insert_at < len(expanded):
            neighbor = expanded[insert_at]
            neighbor_match = INSERTION_LABEL_RE.match(neighbor)
            if not neighbor_match or neighbor_match.group("anchor") != anchor:
                break
            insert_at += 1
        expanded.insert(insert_at, label)
    return sorted(expanded, key=_numbering_sort_key)


def csv_from_residue_labels(labels: list[str], bases: list[str], numbering: list[str]) -> str:
    residue_map = ResidueMap(labels=[str(v) for v in labels], bases=[_safe_base(v) for v in bases])
    output_numbering = _expanded_numbering(numbering, residue_map.labels)
    values = _row_from_residue_map(residue_map, output_numbering)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(output_numbering)
    writer.writerow(values)
    writer.writerow(values)
    return out.getvalue()


def _validate_curated_labels(labels: list[str], numbering: list[str]) -> None:
    allowed = set(numbering)
    seen: dict[str, int] = {}
    errors: list[str] = []
    previous_rank: tuple[float, int, str] | None = None
    for idx, raw in enumerate(labels, start=1):
        label = str(raw or "").strip()
        if not label:
            errors.append(f"residue {idx}: empty label")
            continue
        insertion = INSERTION_LABEL_RE.match(label)
        insertion_anchor = insertion.group("anchor") if insertion else ""
        is_insertion = bool(insertion and insertion_anchor in allowed)
        if label not in allowed and not is_insertion:
            errors.append(f"residue {idx}: non-standard label {label}")
        elif label not in allowed:
            try:
                number_to_region(label)
            except Exception:
                errors.append(f"residue {idx}: non-standard label {label}")
        if label in seen:
            errors.append(f"residue {idx}: duplicate label {label} also used by residue {seen[label]}")
        else:
            seen[label] = idx

        rank = _label_order_rank(label, numbering)
        if rank is None or (label in seen and seen[label] != idx):
            continue
        if previous_rank and rank <= previous_rank[0]:
            errors.append(
                f"residue {idx}: label order error, {label} should be after residue "
                f"{previous_rank[1]} label {previous_rank[2]}"
            )
        else:
            previous_rank = (rank, idx, label)
    if errors:
        preview = "; ".join(errors[:8])
        if len(errors) > 8:
            preview += f"; and {len(errors) - 8} more"
        raise ValueError(f"Invalid curated labels: {preview}")


def _label_order_rank(label: str, numbering: list[str]) -> float | None:
    clean = str(label or "").strip()
    if clean in numbering:
        return float(numbering.index(clean) * 100000)
    insertion = INSERTION_LABEL_RE.match(clean)
    if not insertion:
        return None
    anchor = insertion.group("anchor")
    if anchor not in numbering:
        return None
    suffix_match = re.search(r"i(\d+)$", clean)
    if not suffix_match:
        return None
    return float(numbering.index(anchor) * 100000 + int(suffix_match.group(1)))


def _load_case_json(run_id: str, seqname: str) -> dict[str, Any] | None:
    """Full trace for one case: MinIO object first, retained local file second."""
    store = _store_for(run_id)
    if store is not None:
        payload = store.case_json(seqname)
        if payload is not None:
            return payload
        if store.case_row(seqname) is not None:
            # Indexed but the trace object is missing: a storage gap, not an
            # unknown run, so the caller gets None instead of a 404.
            return None
    path = _local_run_directory(run_id) / "cases" / f"{case_digest(seqname)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


@lru_cache(maxsize=4)
def _latest_corrections(path: Path = DEFAULT_CORRECTIONS_JSONL) -> dict[str, dict[str, Any]]:
    if sequence_db.enabled() and path == DEFAULT_CORRECTIONS_JSONL:
        return {n: _normalize_correction(r) for n, r in sequence_db.latest_annotations().items()}
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            seqname = str(payload.get("seqname") or "").strip()
            if seqname:
                latest[seqname] = _normalize_correction(payload)
    return latest


@lru_cache(maxsize=16)
def _latest_round_corrections(
    review_round: str,
    path: Path = DEFAULT_CORRECTIONS_JSONL,
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    wanted_round = str(review_round or "").strip()
    if sequence_db.enabled() and path == DEFAULT_CORRECTIONS_JSONL:
        return {n: _normalize_correction(r) for n, r in sequence_db.latest_annotations(review_round=wanted_round).items()}
    if not wanted_round or not path.exists():
        return latest
    with path.open("r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if str(payload.get("review_round") or "").strip() != wanted_round:
                continue
            seqname = str(payload.get("seqname") or "").strip()
            if seqname:
                latest[seqname] = _normalize_correction(payload)
    return latest


@lru_cache(maxsize=32)
def _latest_drafts(
    run_id: str,
    review_scope: str,
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the newest server draft for each record in one review context."""
    latest: dict[str, dict[str, Any]] = {}
    drafts_path = path or DEFAULT_DRAFTS_JSONL
    if sequence_db.enabled() and drafts_path == DEFAULT_DRAFTS_JSONL:
        return sequence_db.latest_annotations(kind="draft", run_id=run_id, review_scope=_normalize_review_scope(review_scope))
    if not drafts_path.exists():
        return latest
    wanted_run = str(run_id or "").strip()
    wanted_scope = _normalize_review_scope(review_scope)
    with drafts_path.open("r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if str(payload.get("run_id") or "").strip() != wanted_run:
                continue
            if _normalize_review_scope(str(payload.get("review_scope") or "")) != wanted_scope:
                continue
            seqname = str(payload.get("seqname") or "").strip()
            if seqname:
                latest[seqname] = payload
    return latest


def _case_broad_source(case_json: dict[str, Any] | None) -> str:
    benchmark_case = dict((case_json or {}).get("benchmark_case") or {})
    metadata = dict(benchmark_case.get("metadata") or {})
    return str(metadata.get("broad_source") or "").strip().lower()


def _build_diffs(gold: ResidueMap, pred: ResidueMap) -> list[dict[str, Any]]:
    return case_index.build_diffs(gold.labels, gold.bases, pred.labels, pred.bases)


@lru_cache(maxsize=4096)
def _structure_for_sequence(seqname: str, sequence: str, template_name: str | None = None) -> dict[str, Any]:
    cached = _structure_from_cache(seqname, sequence)
    if cached:
        return cached

    try:
        cmp = compare_input_template(sequence, template_name=template_name)
    except Exception:
        cmp = None
    if isinstance(cmp, dict):
        ann_meta = dict(cmp.get("annotation_meta") or {})
        payload = _structure_payload(
            sequence=sequence,
            source=str(ann_meta.get("annotation_source") or ""),
            raw_structure=str(ann_meta.get("secondary_structure") or ""),
            struct_seq=str(ann_meta.get("struct_file_sequence") or ""),
            status=str(ann_meta.get("segmentation_status") or ""),
            errors=str(ann_meta.get("segmentation_errors") or ""),
            template_name=str(cmp.get("template_name") or ""),
        )
        if payload:
            payload["structure_meta"] = {
                "predictor": str(ann_meta.get("annotation_source") or ""),
                "trnascan_isotype": str(ann_meta.get("trnascan_isotype") or ""),
                "trnascan_anticodon": str(ann_meta.get("trnascan_anticodon") or ""),
                "trnascan_anticodon_start": ann_meta.get("trnascan_anticodon_start") or "",
                "trnascan_anticodon_end": ann_meta.get("trnascan_anticodon_end") or "",
            }
            return payload

    try:
        input_id = f"input_{case_digest(seqname or _seq_sha1(sequence))}"
        annotated = annotate_trna_sequences({input_id: _norm_sequence(sequence)})
    except Exception:
        annotated = []
    if annotated and isinstance(annotated[0], dict):
        payload = _structure_from_annotation_entry(annotated[0], sequence)
        if payload:
            return payload

    return {
        "annotation_source": "",
        "secondary_structure": "",
        "structure_by_residue": [],
    }


def _priority(diffs: list[dict[str, Any]], case_json: dict[str, Any] | None) -> int:
    metrics = dict((case_json or {}).get("metrics") or {})
    return case_index.priority_for_diffs(diffs, legality_passed=metrics.get("legality_passed"))


def _result_bool(value: Any) -> bool | None:
    clean = str(value or "").strip().lower()
    if clean in {"true", "1", "yes"}:
        return True
    if clean in {"false", "0", "no"}:
        return False
    return None


def _result_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stored_diff_count(result: Mapping[str, Any]) -> int | None:
    """Measured candidate-versus-gold difference count written when the case was stored."""
    raw = str((result or {}).get("diff_count") or "").strip()
    if not raw:
        return None
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return None


def _result_diff_count(result: dict[str, str]) -> int:
    """Fallback estimate for a run stored without a measured difference count."""
    exact = _result_bool(result.get("exact_match"))
    if exact is True:
        return 0
    correct = _result_int(result.get("label_correct"))
    total = _result_int(result.get("label_total"))
    if correct is not None and total is not None and total > correct:
        return max(1, total - correct)
    return 1


def _result_diff_count_estimated(result: dict[str, str]) -> bool:
    correct = _result_int(result.get("label_correct"))
    total = _result_int(result.get("label_total"))
    return correct is None or total is None or total <= correct


def _result_broad_source(result: Mapping[str, Any]) -> str:
    """Classic three-domain label; ``cohort_source`` is the run-local spelling."""
    for key in ("broad_source", "cohort_source"):
        value = str((result or {}).get(key) or "").strip().lower()
        if value and value != "unknown":
            return value
    return ""


def _split_regions(raw: Any) -> list[str]:
    return sorted({part.strip() for part in str(raw or "").split(",") if part.strip()})


def _case_item_from_result(
    seqname: str,
    gold_map: ResidueMap,
    result: dict[str, str] | None,
    *,
    case_available: bool | None = None,
) -> dict[str, Any]:
    result = result or {}
    measured = _stored_diff_count(result)
    diff_count = measured if measured is not None else _result_diff_count(result)
    prediction_count = _result_int(result.get("prediction_residue_count"))
    priority = _result_int(result.get("priority"))
    if priority is None:
        priority = min(50, diff_count) + (10 if _result_bool(result.get("legality_passed")) is True else 0)
    if case_available is None:
        case_available = bool(result)
    elif prediction_count is not None:
        case_available = prediction_count > 0
    return {
        "seqname": seqname,
        "diff_count": diff_count,
        "diff_count_measured": measured is not None,
        "diff_count_estimated": measured is None and _result_diff_count_estimated(result),
        "diff_regions": _split_regions(result.get("diff_regions")),
        "priority": priority,
        "label_accuracy": _optional_float(result.get("label_accuracy")),
        "region_accuracy": _optional_float(result.get("region_accuracy")),
        "legality_passed": _result_bool(result.get("legality_passed")),
        "template_name": str(result.get("template_name") or "").strip() or None,
        "broad_source": _result_broad_source(result),
        "prediction_available": case_available,
        "input_len": int(_result_int(result.get("input_len")) or len(gold_map.labels)),
        "evaluation_allowed": _result_bool(result.get("gold_evaluation_allowed")) is not False,
    }


def _item_matches_review_scope(scope: str, item: Mapping[str, Any]) -> bool:
    normalized = _normalize_review_scope(scope)
    if not normalized:
        return True
    if not item.get("prediction_available") or not int(item.get("diff_count") or 0):
        return False
    if normalized == CLASSIC_REREVIEW_SCOPE:
        return str(item.get("broad_source") or "") in CLASSIC_BROAD_SOURCES
    return True


def _item_from_case_json(
    seqname: str,
    gold_map: ResidueMap,
    result: Mapping[str, Any],
    case_json: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Recompute the queue entry from the stored trace for an unmeasured row."""
    pred_map = _pred_residue_map(str((case_json or {}).get("csv_content") or ""))
    diffs = _build_diffs(gold_map, pred_map)
    metrics = dict((case_json or {}).get("metrics") or {})
    item = _case_item_from_result(seqname, gold_map, dict(result), case_available=bool(pred_map.labels))
    item.update(
        {
            "diff_count": len(diffs),
            "diff_count_measured": True,
            "diff_count_estimated": False,
            "diff_regions": sorted({str(d.get("region") or "") for d in diffs if d.get("region")}),
            "priority": _priority(diffs, dict(case_json) if case_json else None),
            "label_accuracy": metrics.get("label_accuracy", item.get("label_accuracy")),
            "region_accuracy": metrics.get("region_accuracy", item.get("region_accuracy")),
            "legality_passed": metrics.get("legality_passed", item.get("legality_passed")),
            "template_name": (case_json or {}).get("template_name") or item.get("template_name"),
            "broad_source": _case_broad_source(dict(case_json) if case_json else None) or item.get("broad_source") or "",
        }
    )
    return item


@lru_cache(maxsize=8)
def _case_index_cached(
    run_id: str,
    review_scope: str = "",
    detailed: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Build one review queue straight from the stored per-case index.

    Queue membership is derived from the same A record the UI renders, and the
    difference counts are the ones measured when the case finished, so a page
    of 2,530 records needs one index read rather than one trace read.
    """
    fieldnames, rows_by_name = _gold_table_for_scope(review_scope)
    if not fieldnames or fieldnames[0] != "seqname":
        raise ValueError(f"Unexpected gold reference table for scope: {review_scope or 'default'}")
    numbering = list(fieldnames[1:])
    results = _run_results_by_seqname(run_id)
    indexed: list[dict[str, Any]] = []

    for seqname, row in rows_by_name.items():
        result = results.get(seqname)
        if result is None:
            continue
        gold_map = _gold_residue_map(row, numbering)
        item = _case_item_from_result(seqname, gold_map, result)
        if not item["diff_count_measured"] and (review_scope or detailed):
            item = _item_from_case_json(seqname, gold_map, result, _load_case_json(run_id, seqname))
        if not _item_matches_review_scope(review_scope, item):
            continue
        indexed.append(item)

    return tuple(indexed)


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _curation_evidence_summary(
    case_json: dict[str, Any] | None,
    structure: dict[str, Any],
) -> dict[str, Any]:
    payload = case_json or {}
    scientific = dict(payload.get("scientific_provenance") or {})
    cm_score = dict(scientific.get("cm_score") or {})
    portfolio = dict(scientific.get("cm_portfolio") or {})
    calibration = dict(portfolio.get("calibration") or {})
    agent_trace = dict(payload.get("agent_trace") or {})
    context = dict(agent_trace.get("context_summary") or {})

    structure_meta = dict(structure.get("structure_meta") or {})
    predictor = str(structure_meta.get("predictor") or "").strip()
    isotype = str(structure_meta.get("trnascan_isotype") or "").strip()
    anticodon = str(structure_meta.get("trnascan_anticodon") or "").strip().upper()
    structure_status = str(structure.get("structure_status") or "").strip()
    trnascan_undetermined = "trnascan" in predictor.lower() and (
        isotype.lower() in {"", "undet", "unknown"}
        or anticodon in {"", "NNN", "UNK", "UNKNOWN"}
    )
    has_structure = bool(structure.get("structure_by_residue"))
    # An unresolved isotype/anticodon lowers the evidential status of a raw
    # prediction, but it does not mean that the available structure is absent.
    # Keep the pair links visible and label their status in the UI instead.
    pair_links_allowed = has_structure

    average_posterior = _optional_float(cm_score.get("average_posterior"))
    bit_score = _optional_float(cm_score.get("bit_score"))
    route_confidence = _optional_float(context.get("route_confidence"))
    calibration_status = str(calibration.get("status") or "").strip().lower()
    calibration_disposition = str(calibration.get("disposition") or "").strip().lower()

    risk_codes: list[str] = []
    if trnascan_undetermined:
        risk_codes.append("trnascan_unresolved")
    if structure_status == "raw_prediction":
        risk_codes.append("raw_structure_prediction")
    if average_posterior is not None and average_posterior < 0.995:
        risk_codes.append("low_cm_posterior")
    if route_confidence is not None and route_confidence <= 0:
        risk_codes.append("zero_route_confidence")
    if calibration_status and calibration_status != "compatible":
        risk_codes.append("incompatible_cm_calibration")
    if calibration_disposition == "investigate":
        risk_codes.append("calibration_requires_investigation")

    high_risk_codes = set(risk_codes) - {"raw_structure_prediction"}
    return {
        "high_risk": bool(high_risk_codes),
        "risk_codes": risk_codes,
        "structure": {
            "available": has_structure,
            "pair_links_allowed": pair_links_allowed,
            "status": structure_status,
            "predictor": predictor,
            "trnascan_isotype": isotype,
            "trnascan_anticodon": anticodon,
        },
        "numbering": {
            "average_posterior": average_posterior,
            "bit_score": bit_score,
            "route_confidence": route_confidence,
            "calibration_status": calibration_status,
            "calibration_disposition": calibration_disposition,
            "calibration_reason": str(calibration.get("reason") or "").strip(),
        },
    }


def _status_for_case(seqname: str, diffs: list[dict[str, Any]], corrections: dict[str, dict[str, Any]]) -> str:
    correction = corrections.get(seqname)
    if correction:
        return str(correction.get("status") or correction.get("decision") or "reviewed")
    return "needs_review" if diffs else "auto_agree"


def load_case_bundle(
    seqname: str,
    *,
    run_id: str,
    scope: str = "",
    include_structure: bool = True,
) -> dict[str, Any]:
    review_scope = _normalize_review_scope(scope)
    review_round = _review_round_for_scope(review_scope)
    comparison_fieldnames, comparison_by_name = _gold_table_for_scope(review_scope)
    numbering = list(comparison_fieldnames[1:])
    comparison_row = comparison_by_name.get(seqname)
    if comparison_row is None:
        raise KeyError(seqname)
    gold_record = sequence_db.gold_rows([seqname]).get(seqname)

    original_fieldnames, original_by_name = _raw_reference_table()
    original_row = original_by_name.get(seqname)
    if original_row is None:
        raise KeyError(seqname)

    case_json = _load_case_json(run_id, seqname)
    pred_csv = str((case_json or {}).get("csv_content") or "")
    original_map = _gold_residue_map(original_row, original_fieldnames[1:])
    comparison_map = _gold_residue_map(comparison_row, numbering)
    pred_map = _pred_residue_map(pred_csv) if pred_csv else ResidueMap([], [])
    diffs = _build_diffs(comparison_map, pred_map)
    corrections = _latest_corrections.__wrapped__() if sequence_db.enabled() else _latest_corrections()
    previous = corrections.get(seqname)
    round_corrections = ((_latest_round_corrections.__wrapped__(review_round) if sequence_db.enabled() else _latest_round_corrections(review_round)) if review_round else corrections)
    latest = round_corrections.get(seqname)
    latest_draft = (_latest_drafts.__wrapped__(run_id, review_scope) if sequence_db.enabled() else _latest_drafts(run_id, review_scope)).get(seqname)
    trnadb_meta = _metadata_for_seqname(seqname)
    structure = (
        _structure_for_sequence(
            seqname,
            original_map.sequence,
            template_name=str((case_json or {}).get("template_name") or "") or None,
        )
        if include_structure
        else {"structure_by_residue": [], "annotation_source": "", "structure_meta": {}}
    )
    structure_meta = dict(structure.get("structure_meta") or {})
    if trnadb_meta:
        structure_meta["trnadb"] = trnadb_meta
    curated_labels = None
    curated_origin = ""
    # C is the scratch area: it holds only a draft that has not been committed.
    # Submitting a decision moves those labels into A, so C empties and any
    # older draft is superseded by the decision that followed it.
    if latest_draft and isinstance(latest_draft.get("curated_labels"), list):
        committed = None
        for record in (latest, previous):
            if record and (committed is None or sequence_db.timestamp(record) > sequence_db.timestamp(committed)):
                committed = record
        drafted_at = sequence_db.timestamp(latest_draft)
        if committed is None or drafted_at > sequence_db.timestamp(committed):
            draft_labels = [str(v) for v in latest_draft.get("curated_labels") or []]
            if len(draft_labels) == len(original_map.bases):
                curated_labels = draft_labels
                curated_origin = "server_draft"

    return {
        "seqname": seqname,
        "run_id": run_id,
        "review_scope": review_scope,
        "review_round": review_round,
        "numbering": numbering,
        "input_seq": original_map.sequence,
        "active_gold": {
            "source": str((gold_record or {}).get("gold_source") or "raw"),
            "decided_at": _iso_or_none((gold_record or {}).get("decided_at")),
            "decision_hash": (gold_record or {}).get("decision_hash"),
            "decision_run_id": (gold_record or {}).get("decision_run_id"),
            "evaluation_allowed": bool((gold_record or {}).get("evaluation_allowed", True)),
            "exclusion_reason": (gold_record or {}).get("exclusion_reason"),
            "excluded_at": _iso_or_none((gold_record or {}).get("excluded_at")),
            "labels_by_residue": comparison_map.labels,
            "bases_by_residue": comparison_map.bases,
        },
        # Panel A is the answer table, so it always agrees with the difference
        # counts shown in the queue.  The untouched tRNAdb numbering stays here
        # for provenance; the answer table itself keeps both columns.
        "raw": {
            "labels_by_residue": original_map.labels,
            "bases_by_residue": original_map.bases,
            "csv_content": _gold_csv_content(original_row, original_fieldnames[1:]),
        },
        "gold": {
            "labels_by_residue": comparison_map.labels,
            "bases_by_residue": comparison_map.bases,
            "structure_by_residue": list(structure.get("structure_by_residue") or []),
            "structure_source": structure.get("annotation_source") or "",
            "structure_meta": structure_meta,
            "csv_content": _gold_csv_content(comparison_row, numbering),
        },
        "comparison_reference": {
            "labels_by_residue": comparison_map.labels,
            "bases_by_residue": comparison_map.bases,
        },
        "prediction": {
            "available": bool(pred_map.labels),
            "status": (case_json or {}).get("status"),
            "error": (case_json or {}).get("error"),
            "template_name": (case_json or {}).get("template_name"),
            "warnings": list((case_json or {}).get("warnings") or []),
            "metrics": dict((case_json or {}).get("metrics") or {}),
            "labels_by_residue": pred_map.labels,
            "bases_by_residue": pred_map.bases,
            "structure_by_residue": list(structure.get("structure_by_residue") or []),
            "structure_source": structure.get("annotation_source") or "",
            "structure_meta": structure_meta,
            "csv_content": pred_csv,
        },
        "diffs": diffs,
        "diff_count": len(diffs),
        "diff_regions": sorted({str(d.get("region") or "") for d in diffs if d.get("region")}),
        "status": _status_for_case(seqname, diffs, round_corrections),
        "priority": _priority(diffs, case_json),
        "correction": latest,
        "historical_correction": previous if previous is not latest else None,
        "previous_correction": previous if review_round and latest is None else None,
        "curated_labels": curated_labels,
        "curated_origin": curated_origin,
        "draft": latest_draft,
        "evidence": _curation_evidence_summary(case_json, structure),
    }


# A stored trace carries the full prompt context of every step.  The reviewer
# needs the decisions and their numbers, not the payloads, so these blobs are
# dropped and long strings are truncated when the process view is built.
PROCESS_EVENT_OMITTED = {"input_contract", "context_summary", "work_state"}
PROCESS_MAX_STRING = 300
PROCESS_MAX_LIST = 12


def _compact_fact(value: Any, depth: int = 0) -> Any:
    """Reduce one trace field to what can be shown in a timeline step."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        # -inf marks "never scored", and JSON has no such literal.
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value if len(value) <= PROCESS_MAX_STRING else value[:PROCESS_MAX_STRING] + "…"
    if isinstance(value, (list, tuple)):
        items = []
        for item in list(value)[:PROCESS_MAX_LIST]:
            compact = _compact_fact(item, depth + 1)
            if compact is not None and compact != [] and compact != {}:
                items.append(compact)
        return items
    if isinstance(value, dict):
        if depth >= 2:
            return None
        facts: dict[str, Any] = {}
        for key, item in value.items():
            key = str(key)
            if key in PROCESS_EVENT_OMITTED:
                continue
            compact = _compact_fact(item, depth + 1)
            if compact is None or compact == [] or compact == {}:
                continue
            facts[key] = compact
        return facts or None
    return str(value)[:PROCESS_MAX_STRING]


def _step_state(stage: str, facts: Mapping[str, Any]) -> str:
    """Classify a step so the timeline can mark it without re-reading it."""
    text = json.dumps(facts, ensure_ascii=False, default=str).lower()
    if stage in {"action_failed", "tool_failed"} or '"error"' in text:
        return "failed"
    if any(token in stage for token in ("failed", "rejected")):
        return "failed"
    if "abstain" in text or "rejected" in text or '"valid": false' in text:
        return "warning"
    if facts.get("accepted") is True or facts.get("valid") is True:
        return "passed"
    return "info"


def _process_steps(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    events = (trace.get("agent_trace") or {}).get("trace_events")
    steps: list[dict[str, Any]] = []
    if not isinstance(events, list):
        return steps
    for position, event in enumerate(events, start=1):
        if not isinstance(event, Mapping):
            continue
        stage = str(event.get("stage") or "event")
        facts: dict[str, Any] = {}
        for key, value in event.items():
            if key == "stage" or key in PROCESS_EVENT_OMITTED:
                continue
            compact = _compact_fact(value)
            if compact is None or compact == [] or compact == {}:
                continue
            facts[key] = compact
        steps.append({"position": position, "stage": stage, "state": _step_state(stage, facts), "facts": facts})
    return steps


def _process_actions(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    history = ((trace.get("agent_trace") or {}).get("work_state_summary") or {}).get("action_history")
    actions: list[dict[str, Any]] = []
    if not isinstance(history, list):
        return actions
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        compact = _compact_fact(dict(entry), depth=1)
        if isinstance(compact, dict):
            actions.append(compact)
    return actions


def _process_llm_calls(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    calls = (trace.get("llm_usage") or {}).get("calls")
    summary: list[dict[str, Any]] = []
    if not isinstance(calls, list):
        return summary
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        usage = call.get("usage_flat") or call.get("usage") or {}
        summary.append(
            {
                "source": str(call.get("source") or ""),
                "model": str(call.get("model") or ""),
                "provider": str(call.get("provider") or ""),
                "status_code": call.get("status_code"),
                "elapsed_sec": call.get("elapsed_sec"),
                "prompt_tokens": (usage or {}).get("prompt_tokens"),
                "completion_tokens": (usage or {}).get("completion_tokens"),
                "total_tokens": (usage or {}).get("total_tokens"),
            }
        )
    return summary


def _process_provenance(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Calibration and CM observations, without the training metadata."""
    provenance = trace.get("scientific_provenance") or {}
    out: dict[str, Any] = {}
    calibration = ((provenance.get("cm_portfolio") or {}).get("calibration")) or {}
    if isinstance(calibration, Mapping):
        keep = {
            "status",
            "disposition",
            "predicted_group",
            "confidence",
            "probability_margin",
            "probabilities",
            "thresholds",
        }
        out["calibration"] = {key: value for key, value in calibration.items() if key in keep}
    for key in ("numbering_backend", "cm_score", "cm_tool", "specialized_route"):
        value = provenance.get(key)
        if isinstance(value, (Mapping, list, str, int, float, bool)):
            compact = _compact_fact(value, depth=1)
            if compact:
                out[key] = compact
    return out


def load_case_process(
    seqname: str,
    *,
    run_id: str = DEFAULT_RUN_ID,
) -> dict[str, Any]:
    """How this run arrived at its numbering: steps, decisions and cost.

    The scalar part comes from the MySQL index and the narrative part from the
    trace object in MinIO, so the page can explain a result without repeating
    any model call.
    """
    store = _store_for(run_id)
    row = store.case_row(seqname) if store is not None else None
    trace = _load_case_json(run_id, seqname)
    if row is None and trace is None:
        raise KeyError(seqname)
    steps = _process_steps(trace or {})
    agent = (trace or {}).get("agent") or {}
    difficulty = next((step for step in steps if step["stage"] == "difficulty_assessed"), None)
    route_step = next((step for step in steps if step["stage"] == "route_assessed"), None)
    guard = (trace or {}).get("guard_challenge_audit") or {}
    return {
        "available": bool(trace),
        "run_id": str((row or {}).get("run_id") or run_id),
        "seqname": str(seqname),
        "model": str((trace or {}).get("model") or ""),
        "provider": str((trace or {}).get("provider") or ""),
        "method_backend": str((trace or {}).get("method_backend") or (row or {}).get("method_backend") or ""),
        "planner_policy": str((trace or {}).get("agent_planner_policy") or (row or {}).get("agent_planner_policy") or ""),
        "planner_sources": _split_regions((row or {}).get("planner_sources")),
        "template_name": str((row or {}).get("template_name") or (trace or {}).get("template_name") or ""),
        "status": str((row or {}).get("status") or (trace or {}).get("status") or ""),
        "error": str((row or {}).get("error") or ""),
        "started_at": (row or {}).get("started_at"),
        "finished_at": (row or {}).get("finished_at"),
        "elapsed_sec": (row or {}).get("elapsed_sec"),
        "route": str((row or {}).get("route") or agent.get("route") or ""),
        "disposition": str((row or {}).get("disposition") or agent.get("disposition") or ""),
        "difficulty_tier": str(agent.get("difficulty_tier") or ""),
        "difficulty_score": (difficulty or {}).get("facts", {}).get("difficulty_score"),
        "route_confidence": (route_step or {}).get("facts", {}).get("confidence", agent.get("route_confidence")),
        "hard_guard_passed": agent.get("hard_guard_passed"),
        "legality_passed": (row or {}).get("legality_passed"),
        "base_preserved": (row or {}).get("base_preserved"),
        "out_of_scope": (row or {}).get("out_of_scope"),
        "in_canonical_benchmark": (row or {}).get("in_canonical_benchmark"),
        "legality_reasons": list((trace or {}).get("legality_reasons") or []),
        "warnings": [str(item) for item in ((trace or {}).get("warnings") or [])],
        "stop_reason": str((row or {}).get("agent_stop_reason") or ""),
        "counts": {
            "planner_decisions": (row or {}).get("planner_decision_count"),
            "planner_llm_calls": (row or {}).get("planner_llm_call_count"),
            "agent_actions": (row or {}).get("agent_action_count"),
            "cm_tool_calls": (row or {}).get("cm_tool_call_count"),
            "guard_challenges": (row or {}).get("guard_challenge_count"),
            "guard_rejected": (row or {}).get("guard_challenge_rejected_count"),
            "llm_calls": (row or {}).get("llm_api_call_count"),
            "technical_issues": (row or {}).get("technical_issue_count"),
            "steps": len(steps),
        },
        "tokens": {
            "prompt": (row or {}).get("llm_prompt_tokens"),
            "completion": (row or {}).get("llm_completion_tokens"),
            "total": (row or {}).get("llm_total_tokens"),
            "cache_hit": (row or {}).get("llm_prompt_cache_hit_tokens"),
            "cache_miss": (row or {}).get("llm_prompt_cache_miss_tokens"),
        },
        "guard": _compact_fact(guard, depth=1) or {},
        "provenance": _process_provenance(trace or {}),
        "steps": _process_steps(trace or {}),
        "actions": _process_actions(trace or {}),
        "llm_calls": _process_llm_calls(trace or {}),
    }


def generate_case_interpretation(
    seqname: str,
    *,
    run_id: str,
    scope: str = "",
    language: str = "en",
) -> dict[str, Any]:
    """Generate an interpretation for the stored SprinzlMap candidate.

    The curation reference and any manually edited C labels are intentionally
    excluded. The interpreter only sees the current candidate and its run evidence.
    """

    bundle = load_case_bundle(seqname, run_id=run_id, scope=scope, include_structure=True)
    prediction = dict(bundle.get("prediction") or {})
    labels = list(prediction.get("labels_by_residue") or [])
    bases = list(prediction.get("bases_by_residue") or [])
    if not labels or len(labels) != len(bases):
        raise ValueError("This run does not contain a usable SprinzlMap candidate.")
    case_json = _load_case_json(run_id, seqname) or {}
    agent = dict(case_json.get("agent") or {})
    agent_trace = dict(case_json.get("agent_trace") or {})
    if agent_trace:
        agent["trace_events"] = list(agent_trace.get("trace_events") or [])
        agent["work_state_summary"] = dict(agent_trace.get("work_state_summary") or {})
        agent.setdefault("route", str(agent.get("route") or ""))
    structure = {
        "annotation_source": prediction.get("structure_source") or "",
        "structure_by_residue": list(prediction.get("structure_by_residue") or []),
        "secondary_structure": "".join(prediction.get("structure_by_residue") or []),
        "structure_meta": dict(prediction.get("structure_meta") or {}),
    }
    benchmark_case = dict(case_json.get("benchmark_case") or {})
    # These fields choose which literature to show the reviewer.  They are not part
    # of the evidence contract, so they cannot change the written interpretation.
    curation_record = {
        "id": seqname,
        "diff_regions": [str(item) for item in (case_json.get("diff_regions") or []) if str(item or "").strip()],
        "cohort_source": str(benchmark_case.get("cohort_source") or ""),
        "terminal_reference_category": str(benchmark_case.get("terminal_reference_category") or ""),
    }
    return generate_scientific_interpretation(
        sequence=str(bundle.get("input_seq") or ""),
        labels=labels,
        bases=bases,
        structure=structure,
        agent=agent,
        scientific_provenance=dict(case_json.get("scientific_provenance") or {}),
        warnings=list(prediction.get("warnings") or []),
        record=curation_record,
        language=language,
    )


def list_cases(
    *,
    run_id: str,
    status: str = "needs_review",
    region: str = "",
    query: str = "",
    scope: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    wanted_status = status.strip() or "needs_review"
    review_scope = _normalize_review_scope(scope)
    wanted_region = region.strip()
    q = query.strip().lower()
    cached = (_list_cases_cached.__wrapped__(run_id, wanted_status, wanted_region, q, review_scope) if sequence_db.enabled() else _list_cases_cached(run_id, wanted_status, wanted_region, q, review_scope))
    items = list(cached.get("items") or [])
    total = len(items)
    safe_limit = max(1, min(int(limit or 200), 20000))
    safe_offset = max(0, int(offset or 0))
    return {
        "run_id": run_id,
        "status": wanted_status,
        "region": wanted_region,
        "query": query,
        "review_scope": review_scope,
        "review_round": _review_round_for_scope(review_scope),
        "counts": dict(cached.get("counts") or {}),
        "groups": dict(cached.get("groups") or {}),
        "total": total,
        "items": items[safe_offset : safe_offset + safe_limit],
        "limit": safe_limit,
        "offset": safe_offset,
    }


@lru_cache(maxsize=32)
def _list_cases_cached(
    run_id: str,
    wanted_status: str,
    wanted_region: str,
    q: str,
    review_scope: str = "",
) -> dict[str, Any]:
    review_round = _review_round_for_scope(review_scope)
    corrections = (sequence_db.latest_annotations(review_round=review_round or None) if sequence_db.enabled() else (_latest_round_corrections(review_round) if review_round else _latest_corrections()))
    indexed = _case_index_cached(run_id, review_scope, detailed=bool(wanted_region))

    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    reviewable_total = 0
    # Eligibility is its own axis: an answer can stay confirmed while the
    # sequence is dropped from scoring, so the tab counts cannot come from
    # ``counts``, which holds one entry per sequence.
    scored_total = 0
    excluded_total = 0
    for indexed_item in indexed:
        seqname = str(indexed_item.get("seqname") or "").strip()
        if q and q not in seqname.lower():
            continue
        diff_count = int(indexed_item.get("diff_count") or 0)
        correction = corrections.get(seqname) or {}
        item_status = str(
            correction.get("status")
            or correction.get("decision")
            or ("needs_review" if diff_count else "auto_agree")
        )
        allowed = bool(indexed_item.get("evaluation_allowed", True))
        excluded_total += 0 if allowed else 1
        scored_total += 1 if allowed else 0
        counts[item_status] = counts.get(item_status, 0) + 1
        if diff_count:
            reviewable_total += 1
        diff_regions = list(indexed_item.get("diff_regions") or [])
        if wanted_status == "reviewable":
            if not diff_count:
                continue
        elif wanted_status == "completed":
            if item_status not in COMPLETED_STATUSES:
                continue
        elif wanted_status == "excluded":
            if allowed:
                continue
        elif wanted_status != "all" and item_status != wanted_status:
            continue
        if wanted_region and wanted_region not in diff_regions:
            continue
        items.append(
            {
                "seqname": seqname,
                "status": item_status,
                "diff_count": diff_count,
                "diff_count_estimated": bool(indexed_item.get("diff_count_estimated")),
                "diff_regions": diff_regions,
                "priority": int(indexed_item.get("priority") or 0),
                "label_accuracy": indexed_item.get("label_accuracy"),
                "region_accuracy": indexed_item.get("region_accuracy"),
                "legality_passed": indexed_item.get("legality_passed"),
                "template_name": indexed_item.get("template_name"),
                "broad_source": indexed_item.get("broad_source") or "",
                "prediction_available": bool(indexed_item.get("prediction_available")),
                "input_len": int(indexed_item.get("input_len") or 0),
                "evaluation_allowed": allowed,
            }
        )

    items.sort(key=lambda item: (-int(item.get("priority") or 0), -int(item.get("diff_count") or 0), item.get("seqname") or ""))
    return {
        "counts": counts,
        "groups": _queue_group_counts(counts, reviewable_total, excluded=excluded_total, scored=scored_total),
        "items": items,
    }


def _queue_group_counts(
    counts: Mapping[str, int],
    reviewable_total: int,
    *,
    excluded: int = 0,
    scored: int = 0,
) -> dict[str, int]:
    """One number per queue tab, so that the page never re-derives a group."""
    group = {
        "all": sum(int(value or 0) for value in counts.values()),
        "reviewable": int(reviewable_total),
        "needs_review": int(counts.get(NEEDS_REVIEW_STATUS) or 0),
        "completed": sum(int(counts.get(status) or 0) for status in COMPLETED_STATUSES),
        "unsure": int(counts.get("unsure") or 0),
        "excluded": int(excluded),
        "scored": int(scored),
    }
    for status in COMPLETED_STATUSES:
        group[status] = int(counts.get(status) or 0)
    return group


def summary(*, run_id: str, scope: str = "") -> dict[str, Any]:
    review_scope = _normalize_review_scope(scope)
    data = list_cases(run_id=run_id, status="all", scope=review_scope, limit=1)
    counts = data.get("counts") or {}
    store = _store_for(run_id)
    if store is not None:
        info = store.ping()
        run_location = f"minio://{info['bucket']}/{info['object_prefix']}"
    else:
        run_location = str((DEFAULT_RUNS_DIR / run_id).relative_to(ROOT))
    if sequence_db.enabled():
        gold_location = "mysql://trna_gold"
    else:
        gold_location = str(DEFAULT_GOLD_TSV.relative_to(ROOT))
    return {
        "run_id": run_id,
        "label": _run_batch_label(run_id),
        "role": _run_batch_role(run_id),
        "gold_path": gold_location,
        "gold_table_version": sequence_db.gold_table_version(),
        "run_path": run_location,
        "corrections_path": (
            "mysql://trna_annotations" if sequence_db.enabled()
            else str(DEFAULT_CORRECTIONS_JSONL.relative_to(ROOT))
        ),
        "review_scope": review_scope,
        "review_round": _review_round_for_scope(review_scope),
        "counts": counts,
        "groups": data.get("groups") or {},
        "total": sum(int(v or 0) for v in counts.values()),
    }


def append_decision(
    seqname: str,
    payload: dict[str, Any],
    *,
    run_id: str,
    scope: str = "",
) -> dict[str, Any]:
    review_scope = _normalize_review_scope(scope)
    review_round = _review_round_for_scope(review_scope)
    bundle = load_case_bundle(
        seqname,
        run_id=run_id,
        scope=review_scope,
        include_structure=False,
    )
    status = str(payload.get("status") or payload.get("decision") or "").strip()
    if status not in {"accepted_gold", "accepted_model", "manual_corrected", "unsure", "excluded"}:
        raise ValueError("Invalid status.")

    curated_labels_raw = payload.get("curated_labels")
    curated_labels = [str(v).strip() for v in curated_labels_raw] if isinstance(curated_labels_raw, list) else []
    if status == "accepted_gold":
        curated_labels = list(bundle["gold"]["labels_by_residue"])
    elif status == "accepted_model":
        curated_labels = list(bundle["prediction"]["labels_by_residue"])
    elif status == "manual_corrected" and len(curated_labels) != len(bundle["gold"]["bases_by_residue"]):
        raise ValueError("manual_corrected requires one curated label per residue.")

    if curated_labels:
        _validate_curated_labels(curated_labels, list(bundle["numbering"]))

    curated_csv = ""
    if curated_labels:
        curated_csv = csv_from_residue_labels(
            curated_labels,
            list(bundle["gold"]["bases_by_residue"]),
            list(bundle["numbering"]),
        )

    record = {
        "seqname": seqname,
        "run_id": run_id,
        "review_scope": review_scope,
        "review_round": review_round,
        "status": status,
        "decision": status,
        "curated_at": _now_iso(),
        "curated_by": str(payload.get("curated_by") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
        "exclusion_reason": str(payload.get("exclusion_reason") or "").strip(),
        "original_gold_labels": list(bundle["raw"]["labels_by_residue"]),
        "active_gold_labels": list(bundle["gold"]["labels_by_residue"]),
        "baseline_labels": list(bundle["comparison_reference"]["labels_by_residue"]),
        "model_labels": list(bundle["prediction"]["labels_by_residue"]),
        "curated_labels": curated_labels,
        "bases_by_residue": list(bundle["gold"]["bases_by_residue"]),
        "diff_count": int(bundle["diff_count"]),
        "diff_regions": list(bundle["diff_regions"]),
        "curated_csv": curated_csv,
    }
    if sequence_db.enabled():
        sequence_db.put_annotation(record, kind="formal")
    else:
        DEFAULT_CORRECTIONS_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with DEFAULT_CORRECTIONS_JSONL.open("a", encoding="utf-8") as fout:
            fout.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    if sequence_db.enabled():
        # The answer table is the single read path for scoring, so a committed
        # decision updates it in the same call instead of waiting for a rebuild.
        refresh_gold_row(seqname)
    _latest_corrections.cache_clear()
    _latest_round_corrections.cache_clear()
    _list_cases_cached.cache_clear()
    return record


def append_draft(
    seqname: str,
    payload: dict[str, Any],
    *,
    run_id: str,
    scope: str = "",
) -> dict[str, Any]:
    review_scope = _normalize_review_scope(scope)
    review_round = _review_round_for_scope(review_scope)
    bundle = load_case_bundle(
        seqname,
        run_id=run_id,
        scope=review_scope,
        include_structure=False,
    )
    curated_labels_raw = payload.get("curated_labels")
    curated_labels = [str(v).strip() for v in curated_labels_raw] if isinstance(curated_labels_raw, list) else []
    status = str(payload.get("status") or payload.get("decision") or "draft").strip() or "draft"
    record = {
        "seqname": seqname,
        "run_id": run_id,
        "review_scope": review_scope,
        "review_round": review_round,
        "status": status,
        "drafted_at": _now_iso(),
        "notes": str(payload.get("notes") or "").strip(),
        "curated_labels": curated_labels,
        "bases_by_residue": list(bundle["gold"]["bases_by_residue"]),
        "diff_count": int(bundle["diff_count"]),
        "diff_regions": list(bundle["diff_regions"]),
        "valid_length": len(curated_labels) == len(bundle["gold"]["bases_by_residue"]),
    }
    if sequence_db.enabled():
        sequence_db.put_annotation(record, kind="draft")
    else:
        DEFAULT_DRAFTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with DEFAULT_DRAFTS_JSONL.open("a", encoding="utf-8") as fout:
            fout.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    _latest_drafts.cache_clear()
    return record


def export_curated_tsv(
    path: Path | None = None,
    *,
    include_excluded: bool = False,
) -> dict[str, Any]:
    """Write the active answer table out as a TSV snapshot.

    The TSV is a view, never a source of truth: it is what a benchmark run
    freezes as ``inputs/reference.tsv`` so a published number stays
    reproducible while the library answer keeps moving.
    """
    out_path = (path or DEFAULT_CURATED_GOLD_TSV).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if sequence_db.enabled():
        fieldnames, rows_by_name = _gold_table_for_scope("")
        if not fieldnames or fieldnames[0] != "seqname":
            raise ValueError("trna_gold returned no answer rows")
        answer_meta = sequence_db.gold_rows(list(rows_by_name))
        output_fieldnames = list(fieldnames)
        out_rows: list[dict[str, str]] = []
        applied = 0
        excluded = 0
        for seqname, row in rows_by_name.items():
            meta = answer_meta.get(seqname) or {}
            if (
                meta.get("evaluation_allowed") is False or meta.get("gold_source") == "excluded"
            ) and not include_excluded:
                excluded += 1
                continue
            if str(meta.get("gold_source") or "raw") != "raw":
                applied += 1
            out_rows.append({key: str(row.get(key) or "") for key in output_fieldnames})
        rows = list(rows_by_name)
    else:
        fieldnames, rows = _read_tsv_dicts(DEFAULT_GOLD_TSV)
        if not fieldnames or fieldnames[0] != "seqname":
            raise ValueError(f"Unexpected gold TSV header: {DEFAULT_GOLD_TSV}")
        numbering = fieldnames[1:]
        corrections = _latest_corrections()
        all_curated_labels: list[str] = []
        for correction in corrections.values():
            if correction.get("status") in {"accepted_gold", "accepted_model", "manual_corrected"}:
                all_curated_labels.extend(str(v) for v in correction.get("curated_labels") or [])
            elif correction.get("status") == "unsure" and correction.get("review_round"):
                all_curated_labels.extend(str(v) for v in correction.get("baseline_labels") or [])
        output_numbering = _expanded_numbering(numbering, all_curated_labels)
        output_fieldnames = [fieldnames[0], *output_numbering]
        applied = 0
        excluded = 0
        out_rows = []
        for row in rows:
            seqname = str(row.get("seqname") or "").strip()
            out_row = {key: str(row.get(key) or "") for key in output_fieldnames}
            correction = corrections.get(seqname)
            if correction and correction.get("status") == "excluded" and not include_excluded:
                excluded += 1
                continue
            if correction and correction.get("status") in {"accepted_gold", "accepted_model", "manual_corrected"}:
                labels = [str(v) for v in correction.get("curated_labels") or []]
                bases = [str(v) for v in correction.get("bases_by_residue") or []]
                if labels and len(labels) == len(bases):
                    values = _row_from_residue_map(ResidueMap(labels, bases), output_numbering)
                    for label, base in zip(output_numbering, values):
                        out_row[label] = base
                    applied += 1
            elif correction and correction.get("status") == "unsure" and correction.get("review_round"):
                labels = [str(v) for v in correction.get("baseline_labels") or []]
                bases = [str(v) for v in correction.get("bases_by_residue") or []]
                if labels and len(labels) == len(bases):
                    values = _row_from_residue_map(ResidueMap(labels, bases), output_numbering)
                    for label, base in zip(output_numbering, values):
                        out_row[label] = base
            out_rows.append(out_row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=out_path.name, suffix=".tmp", dir=str(out_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as fout:
            writer = csv.DictWriter(
                fout,
                fieldnames=output_fieldnames,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(out_rows)
        os.replace(tmp_path, out_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    try:
        reported_path = str(out_path.relative_to(ROOT))
    except ValueError:
        reported_path = str(out_path)
    return {
        "path": reported_path,
        "applied": applied,
        "excluded": excluded,
        "rows": len(out_rows),
        "source_rows": len(rows),
    }
