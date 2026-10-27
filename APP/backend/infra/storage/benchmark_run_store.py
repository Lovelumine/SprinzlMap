"""Durable benchmark run storage: MySQL index rows plus MinIO trace objects.

A finished case is written to both stores immediately, so a run is visible from
any machine that can reach MySQL and MinIO and the repository never has to keep
per-case JSON on disk.  Connection problems are fatal by design: a partial run
that lives on one laptop is worse than a run that never started.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import contextlib
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import boto3
import botocore.exceptions
from botocore.config import Config
import pymysql

from backend.domain.benchmarking.case_index import (
    BOOL_FIELDS,
    FLOAT_FIELDS,
    INDEX_FIELDNAMES,
    INT_FIELDS,
    apply_active_answer_to_trace,
    flatten_case_result,
    build_diffs,
    pred_residue_map,
    priority_for_diffs,
    score_labels,
    unscored_metrics,
    case_results_tsv_text,
    summarize_case_rows,
)
from backend.infra.config.mysql_settings import load_mysql_config

LOG = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
# Run output never lives in the repository: MySQL holds the index, MinIO holds
# the traces, and a script that still wants a directory gets one under the
# scratch cache (``.runtime/runs``, git-ignored).  ``LEGACY_RUNS_DIR`` is read
# only, so an older checkout that already has run directories keeps working.
LEGACY_RUNS_DIR = PROJECT_ROOT / "benchmark" / "runs"

EXECUTIONS_TABLE = "benchmark_executions"
# Names retired when run, case and attempt rows moved into one table.  A view
# with one of these names is dropped on startup; a base table means data that
# was never merged, which is a stop condition rather than something to delete.
RETIRED_TABLE_NAMES = ("benchmark_runs", "benchmark_case_results", "benchmark_case_attempts")
DEFAULT_OBJECT_PREFIX = "reviewer/LLM-tRNAAlign/benchmarks"
LISTING_RUN_ID = "__listing__"  # read-only handle: MySQL queries across runs

# Physical column -> name used by the case index and the review page.  Keeping
# the mapping in one place is what lets every reader hit one table and still
# speak the canonical field names.
CASE_EXTRA_COLUMNS = (
    "attempt",
    "technical_issue_count",
    "trace_key",
    "trace_sha256",
    "trace_bytes",
    "prediction_csv",
    "stored_at",
    "updated_at",
)


# Score columns retired from the physical table.  Every one of them is derived
# from the answer table, so keeping a copy next to the predictions created a
# second set of numbers that could lag behind the answers: the application and
# the figures re-grade on read, while a hand-written SQL query picked up the
# stored values.  A score now has one source, ``trna_gold`` joined at read time
# by ``_apply_active_answer``.  ``legality_passed`` stays, because it follows
# from the prediction alone.  The names are listed so a database that still
# carries the columns has them dropped when the schema is checked.
ANSWER_DERIVED_CASE_FIELDS = (
    "correction_decision",
    "gold_source",
    "gold_labels_sha256",
    "gold_evaluation_allowed",
    "base_preserved",
    "label_correct",
    "label_total",
    "label_incorrect",
    "label_accuracy",
    "exact_match",
    "region_correct",
    "region_total",
    "region_accuracy",
    "anticodon_correct",
    "anticodon_total",
    "anticodon_accuracy",
    "anticodon_exact",
    "cca_correct",
    "cca_total",
    "cca_accuracy",
    "cca_exact",
    "discriminator_correct",
    "discriminator_total",
    "discriminator_accuracy",
    "discriminator_exact",
    "terminal_coordinate_correct",
    "terminal_coordinate_total",
    "terminal_coordinate_accuracy",
    "terminal_coordinate_exact",
    "variable_correct",
    "variable_total",
    "variable_accuracy",
    "variable_exact",
    "diff_count",
    "diff_regions",
    "priority",
)


PERSISTED_CASE_FIELDS = tuple(
    name
    for name in INDEX_FIELDNAMES
    if name != "seqname" and name not in ANSWER_DERIVED_CASE_FIELDS
)


def case_column_pairs() -> Tuple[Tuple[str, str], ...]:
    """``(physical column, canonical name)`` for one stored case."""
    pairs: List[Tuple[str, str]] = [("run_id", "run_id"), ("seqname", "seqname")]
    pairs += [(f"case_{name}", name) for name in PERSISTED_CASE_FIELDS]
    pairs += [(f"case_{name}", name) for name in CASE_EXTRA_COLUMNS]
    return tuple(pairs)


CASE_COLUMN_PAIRS = case_column_pairs()
CASE_CANONICAL_COLUMNS = tuple(name for _, name in CASE_COLUMN_PAIRS)


def _project(pairs: Sequence[Tuple[str, str]], table_alias: str = "") -> str:
    prefix = f"{table_alias}." if table_alias else ""
    return ", ".join(f"{prefix}`{physical}` AS `{name}`" for physical, name in pairs)


def run_column_pairs() -> Tuple[Tuple[str, str], ...]:
    """``(physical column, canonical name)`` for one stored run."""
    return (
        ("run_id", "run_id"),
        ("run_state", "state"),
        ("model_id", "model_id"),
        ("provider", "provider"),
        ("run_method_backend", "method_backend"),
        ("code_commit", "code_commit"),
        ("git_worktree_dirty", "git_worktree_dirty"),
        ("run_gold_source", "gold_source"),
        ("gold_sha256", "gold_sha256"),
        ("selected_seqnames_sha256", "selected_seqnames_sha256"),
        ("run_contract_sha256", "run_contract_sha256"),
        ("run_attempt", "attempt"),
        ("n_targets", "n_targets"),
        ("n_completed", "n_completed"),
        ("n_pending", "n_pending"),
        ("config_json", "config_json"),
        ("summary_json", "summary_json"),
        ("counts_json", "counts_json"),
        ("command", "command"),
        ("archive_key", "archive_key"),
        ("archive_sha256", "archive_sha256"),
        ("archive_bytes", "archive_bytes"),
        ("manifest_key", "manifest_key"),
        ("run_started_at", "started_at"),
        ("run_finished_at", "finished_at"),
        ("run_updated_at", "updated_at"),
    )


def attempt_column_pairs() -> Tuple[Tuple[str, str], ...]:
    """``(physical column, canonical name)`` for one stored retry attempt."""
    return (
        ("run_id", "run_id"),
        ("seqname", "seqname"),
        ("attempt_no", "attempt_no"),
        ("attempt_outcome", "outcome"),
        ("attempt_error", "error"),
        ("latency_ms", "latency_ms"),
        ("usage_json", "usage_json"),
        ("object_key", "object_key"),
        ("attempt_recorded_at", "recorded_at"),
    )


RUN_SELECT = _project(run_column_pairs(), "e")
CASE_SELECT = _project(case_column_pairs(), "e")
ATTEMPT_SELECT = _project(attempt_column_pairs(), "e")
# The answer columns are joined, never copied: a run's own row keeps only the
# fingerprint of the answer it was graded against at seal time.
GOLD_SELECT = (
    "g.gold_labels AS active_gold_labels, g.gold_bases AS active_gold_bases, "
    "g.gold_sha256 AS active_gold_sha256, g.gold_source AS active_gold_source, "
    "g.decided_at AS active_decided_at, g.evaluation_allowed AS active_evaluation_allowed"
)


class BenchmarkStoreError(RuntimeError):
    """MySQL or MinIO is unusable; the caller must stop the run."""


# botocore >= 1.42 raised ClientError out of the BotoCoreError hierarchy, so
# catching only BotoCoreError lets a plain S3 500 escape unclassified.
S3_ERRORS = (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError, OSError)


def case_digest(seqname: str) -> str:
    return hashlib.sha1(str(seqname).encode("utf-8")).hexdigest()[:16]


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def minio_config() -> Dict[str, Any]:
    endpoint = _env("MINIO_ENDPOINT")
    bucket = _env("MINIO_BUCKET")
    access_key = _env("AWS_ACCESS_KEY_ID")
    secret_key = _env("AWS_SECRET_ACCESS_KEY")
    missing = [
        name
        for name, value in (
            ("MINIO_ENDPOINT", endpoint),
            ("MINIO_BUCKET", bucket),
            ("AWS_ACCESS_KEY_ID", access_key),
            ("AWS_SECRET_ACCESS_KEY", secret_key),
        )
        if not value
    ]
    if missing:
        raise BenchmarkStoreError(f"MinIO configuration missing: {', '.join(missing)}")
    return {
        "endpoint_url": endpoint,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": _env("AWS_DEFAULT_REGION", "us-east-1"),
        "bucket": bucket,
        "prefix": _env("MINIO_PREFIX", "reviewer").rstrip("/"),
        "project": _env("MINIO_PROJECT", "LLM-tRNAAlign"),
    }


def sql_type_for(field: str) -> str:
    if field == "seqname":
        return "VARCHAR(255) NOT NULL"
    if field == "error":
        return "VARCHAR(1024) NULL"
    if field == "diff_regions":
        return "VARCHAR(512) NULL"
    if field in ("trace_key", "manifest_key", "archive_key"):
        return "VARCHAR(512) NULL"
    if field in ("template_name", "planner_sources", "run_contract_sha256"):
        return "VARCHAR(255) NULL"
    if field in BOOL_FIELDS:
        return "TINYINT(1) NULL"
    if field in INT_FIELDS:
        return "BIGINT NULL"
    if field in FLOAT_FIELDS:
        return "DOUBLE NULL"
    return "VARCHAR(128) NULL"


RUN_COLUMN_DEFINITIONS = """
    run_state VARCHAR(16) NULL,
    model_id VARCHAR(128) NULL, provider VARCHAR(64) NULL,
    run_method_backend VARCHAR(32) NULL, code_commit VARCHAR(64) NULL,
    git_worktree_dirty TINYINT(1) NULL,
    run_gold_source VARCHAR(255) NULL, gold_sha256 CHAR(64) NULL,
    selected_seqnames_sha256 CHAR(64) NULL, run_contract_sha256 VARCHAR(255) NULL,
    run_attempt INT NULL, n_targets INT NULL, n_completed INT NULL, n_pending INT NULL,
    config_json LONGTEXT NULL, summary_json LONGTEXT NULL, counts_json LONGTEXT NULL,
    command TEXT NULL,
    archive_key VARCHAR(512) NULL, archive_sha256 CHAR(64) NULL, archive_bytes BIGINT NULL,
    manifest_key VARCHAR(512) NULL,
    run_started_at DATETIME(6) NULL, run_finished_at DATETIME(6) NULL,
    run_updated_at DATETIME(6) NULL
""".strip()

CASE_COLUMN_DEFINITIONS = ",\n    ".join(
    f"`case_{name}` {sql_type_for(name).replace(' NOT NULL', '')}"
    for name in PERSISTED_CASE_FIELDS
)

ATTEMPT_COLUMN_DEFINITIONS = """
    attempt_outcome VARCHAR(32) NULL, attempt_error VARCHAR(1024) NULL,
    latency_ms BIGINT NULL, usage_json LONGTEXT NULL, object_key VARCHAR(512) NULL,
    attempt_recorded_at DATETIME(6) NULL
""".strip()

EXECUTION_DDL = f"""CREATE TABLE IF NOT EXISTS {EXECUTIONS_TABLE} (
    run_id VARCHAR(128) NOT NULL,
    record_type ENUM('run','case','attempt') NOT NULL,
    seqname VARCHAR(255) NOT NULL DEFAULT '',
    attempt_no INT NOT NULL DEFAULT 0,
    {RUN_COLUMN_DEFINITIONS},
    {CASE_COLUMN_DEFINITIONS},
    case_attempt INT NULL, case_technical_issue_count INT NULL,
    case_trace_key VARCHAR(512) NULL, case_trace_sha256 CHAR(64) NULL,
    case_trace_bytes BIGINT NULL, case_prediction_csv LONGTEXT NULL,
    case_stored_at DATETIME(6) NULL, case_updated_at DATETIME(6) NULL,
    {ATTEMPT_COLUMN_DEFINITIONS},
    PRIMARY KEY(run_id, record_type, seqname, attempt_no),
    INDEX idx_execution_run_state(record_type, run_state, run_finished_at),
    INDEX idx_execution_case_disposition(run_id, record_type, `case_disposition`),
    INDEX idx_execution_case_status(run_id, record_type, `case_status`),
    INDEX idx_execution_seqname(seqname)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"""

DDL_STATEMENTS: Tuple[str, ...] = (EXECUTION_DDL,)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_utc(raw: Any) -> Optional[datetime]:
    """Parse a run timestamp into naive UTC; unparseable values are dropped."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _to_bool(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return 1
        if lowered in {"false", "0", "no"}:
            return 0
        return None
    return 1 if bool(value) else 0


def _to_number(value: Any, *, integer: bool) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return int(number) if integer else number


def _to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


class BenchmarkRunStore:
    """Write-through store for one benchmark run."""

    def __init__(self, run_id: str, *, object_prefix: Optional[str] = None) -> None:
        self.run_id = str(run_id).strip()
        if not self.run_id:
            raise BenchmarkStoreError("run_id is required")
        self.mysql_config = load_mysql_config(include_timeouts=True)
        if not self.mysql_config:
            raise BenchmarkStoreError("MySQL configuration missing (MYSQL_* in .env)")
        self.s3_config = minio_config()
        self.bucket = self.s3_config["bucket"]
        base = self.s3_config["prefix"]
        project = self.s3_config["project"]
        self.object_prefix = (
            object_prefix.strip("/")
            if object_prefix
            else "/".join(part for part in (base, project, "benchmarks", self.run_id) if part)
        )
        self._s3 = boto3.client(
            "s3",
            endpoint_url=self.s3_config["endpoint_url"],
            aws_access_key_id=self.s3_config["aws_access_key_id"],
            aws_secret_access_key=self.s3_config["aws_secret_access_key"],
            region_name=self.s3_config["region_name"],
            # A run writes one object per case from several writer threads; the
            # botocore default of ten pooled connections drops connections and
            # forces a fresh TLS/TCP handshake for every extra writer.
            config=Config(max_pool_connections=48, retries={"max_attempts": 4}),
        )
        self._local = threading.local()

    # ---------------------------------------------------------------- plumbing
    @classmethod
    def readonly(cls) -> "BenchmarkRunStore":
        """Handle for cross-run MySQL queries; its object prefix is never used."""
        return cls(LISTING_RUN_ID)

    def db(self) -> pymysql.connections.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            try:
                conn = pymysql.connect(**self.mysql_config)
            except pymysql.Error as exc:
                raise BenchmarkStoreError(f"MySQL connect failed: {exc}") from exc
            self._local.conn = conn
        try:
            conn.ping(reconnect=True)
        except pymysql.Error as exc:
            raise BenchmarkStoreError(f"MySQL ping failed: {exc}") from exc
        return conn

    def ensure_schema(self) -> None:
        existing = {
            str(row["TABLE_NAME"]): str(row["TABLE_TYPE"])
            for row in self.query(
                "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name IN (%s,%s,%s,%s)",
                (EXECUTIONS_TABLE, *RETIRED_TABLE_NAMES),
            )
        }
        if EXECUTIONS_TABLE not in existing:
            legacy_tables = [
                name for name in RETIRED_TABLE_NAMES
                if existing.get(name) == "BASE TABLE"
            ]
            if legacy_tables:
                raise BenchmarkStoreError(
                    "benchmark rows still live in "
                    + ", ".join(legacy_tables)
                    + "; they have to be merged into "
                    + EXECUTIONS_TABLE
                    + " before a run can be stored"
                )
        with self.tx() as cursor:
            cursor.execute(EXECUTION_DDL)
        # Readers now go straight to the physical table, so any compatibility
        # view left from the rollout is dropped rather than maintained.  Only
        # views are touched: a base table with a retired name holds data.
        retired_views = [
            name for name in RETIRED_TABLE_NAMES if existing.get(name) == "VIEW"
        ]
        if retired_views:
            with self.tx() as cursor:
                cursor.execute(
                    "DROP VIEW " + ", ".join(f"`{name}`" for name in retired_views)
                )
            LOG.info("dropped retired benchmark views: %s", ", ".join(retired_views))
        self._sync_case_columns()

    def _sync_case_columns(self) -> None:
        """Add index columns introduced since a table was created, drop retired ones.

        Dropping happens only for ``ANSWER_DERIVED_CASE_FIELDS``: those values
        are recomputed from the answer table, so a database that still carries
        them is carrying numbers that can disagree with the application.  Any
        other field is only ever added, and a renamed field stays a deliberate
        migration with its own script.
        """
        existing = {
            str(row["COLUMN_NAME"])
            for row in self.query(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (EXECUTIONS_TABLE,),
            )
        }
        missing = [
            f"case_{name}" for name in PERSISTED_CASE_FIELDS
            if f"case_{name}" not in existing
        ]
        if missing:
            additions = ", ".join(
                f"ADD COLUMN `{name}` {sql_type_for(name.removeprefix('case_')).replace(' NOT NULL', '')}"
                for name in missing
            )
            with self.tx() as cursor:
                cursor.execute(f"ALTER TABLE {EXECUTIONS_TABLE} {additions}")
            LOG.info("benchmark_executions: added %d case columns", len(missing))
        retired = [
            f"case_{name}" for name in ANSWER_DERIVED_CASE_FIELDS
            if f"case_{name}" in existing
        ]
        if retired:
            drops = ", ".join(f"DROP COLUMN `{name}`" for name in retired)
            with self.tx() as cursor:
                cursor.execute(f"ALTER TABLE {EXECUTIONS_TABLE} {drops}")
            LOG.info(
                "benchmark_executions: dropped %d answer-derived score columns; "
                "scores are graded from trna_gold on read",
                len(retired),
            )

    @contextlib.contextmanager
    def tx(self):
        """One transactional cursor; the connection is dropped on failure."""
        conn = self.db()
        try:
            with conn.cursor() as cursor:
                yield cursor
            conn.commit()
        except pymysql.Error as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
            raise BenchmarkStoreError(f"MySQL statement failed: {exc}") from exc

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self.tx() as cursor:
            cursor.execute(sql, list(params))
            return list(cursor.fetchall() or [])

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def ping(self) -> Dict[str, Any]:
        """Fail fast: both backends must answer before a case is computed."""
        with self.tx() as cursor:
            cursor.execute("SELECT 1 AS ok")
            cursor.fetchone()
        try:
            self._s3.list_objects_v2(Bucket=self.bucket, Prefix=self.object_prefix, MaxKeys=1)
        except S3_ERRORS as exc:
            raise BenchmarkStoreError(f"MinIO unreachable: {exc}") from exc
        return {
            "mysql_host": self.mysql_config["host"],
            "mysql_database": self.mysql_config["database"],
            "minio_endpoint": self.s3_config["endpoint_url"],
            "bucket": self.bucket,
            "object_prefix": self.object_prefix,
        }

    def object_key(self, *parts: str) -> str:
        return "/".join([self.object_prefix, *[part.strip("/") for part in parts]])

    def _upload(self, key: str, body: bytes, *, content_type: str = "application/json") -> Dict[str, Any]:
        digest = hashlib.sha256(body).hexdigest()
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                self._s3.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    Metadata={"sha256": digest},
                )
                return {"key": key, "sha256": digest, "bytes": len(body)}
            except S3_ERRORS as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        raise BenchmarkStoreError(f"MinIO upload failed for {key}: {last_error}")

    def _download(self, key: str) -> Optional[bytes]:
        try:
            return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except botocore.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code"))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise BenchmarkStoreError(f"MinIO download failed for {key}: {exc}") from exc
        except S3_ERRORS as exc:
            raise BenchmarkStoreError(f"MinIO download failed for {key}: {exc}") from exc

    # ------------------------------------------------------------------- runs
    def register_run(self, meta: Mapping[str, Any]) -> None:
        columns = {
            "run_id": self.run_id,
            "record_type": "run",
            "seqname": "",
            "attempt_no": 0,
            "run_state": _to_text(meta.get("state")) or "running",
            "model_id": _to_text(meta.get("model")),
            "provider": _to_text(meta.get("provider")),
            "run_method_backend": _to_text(meta.get("method_backend")),
            "code_commit": _to_text(meta.get("git_commit")),
            "git_worktree_dirty": _to_bool(meta.get("git_worktree_dirty")),
            "run_gold_source": _to_text(meta.get("input_tsv") or meta.get("gold_source")),
            "gold_sha256": _to_text(
                meta.get("gold_sha256")
                or dict(meta.get("input_files") or {}).get("gold", {}).get("sha256")
            ),
            "selected_seqnames_sha256": _to_text(meta.get("selected_seqnames_sha256")),
            "run_contract_sha256": _to_text(meta.get("run_contract_sha256")),
            "run_attempt": int(meta.get("attempt") or 1),
            "n_targets": int(meta.get("target_case_count") or 0),
            "n_completed": int(meta.get("completed_case_count") or 0),
            "n_pending": int(meta.get("pending_case_count") or 0),
            "config_json": json.dumps(meta.get("run_contract") or {}, ensure_ascii=False, sort_keys=True),
            "command": _to_text(meta.get("command")),
            "run_started_at": _to_utc(meta.get("started_at")) or _now(),
            "run_updated_at": _now(),
        }
        names = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        updates = ", ".join(
            f"{name} = VALUES({name})"
            for name in columns
            if name not in {"run_id", "record_type", "seqname", "attempt_no"}
        )
        with self.tx() as cursor:
            cursor.execute(
                f"INSERT INTO {EXECUTIONS_TABLE} ({names}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE {updates}",
                list(columns.values()),
            )

    def checkpoint(self, *, completed: int, pending: int, state: str = "running") -> None:
        with self.tx() as cursor:
            cursor.execute(
                f"UPDATE {EXECUTIONS_TABLE} SET run_state=%s, n_completed=%s, "
                f"n_pending=%s, run_updated_at=CURRENT_TIMESTAMP(6) "
                f"WHERE run_id=%s AND record_type='run'",
                (state, int(completed), int(pending), self.run_id),
            )

    def seal_run(
        self,
        *,
        summary: Mapping[str, Any],
        counts: Mapping[str, Any],
        archive: Optional[Mapping[str, Any]] = None,
        manifest_key: Optional[str] = None,
        finished_at: Any = None,
    ) -> None:
        with self.tx() as cursor:
            cursor.execute(
                f"UPDATE {EXECUTIONS_TABLE} SET run_state='sealed', run_finished_at=%s, "
                f"summary_json=%s, counts_json=%s, archive_key=%s, archive_sha256=%s, "
                f"archive_bytes=%s, manifest_key=%s, n_completed=%s, n_pending=0, "
                f"run_updated_at=CURRENT_TIMESTAMP(6) "
                f"WHERE run_id=%s AND record_type='run'",
                (
                    _to_utc(finished_at) or _now(),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    json.dumps(counts, ensure_ascii=False, sort_keys=True),
                    _to_text((archive or {}).get("key")),
                    _to_text((archive or {}).get("sha256")),
                    _to_number((archive or {}).get("bytes"), integer=True),
                    _to_text(manifest_key),
                    int(summary.get("counts", {}).get("total_cases") or 0),
                    self.run_id,
                ),
            )

    def run_row(self, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Run-level metadata read from the physical execution table."""
        return self.query_one(
            f"SELECT {RUN_SELECT} FROM {EXECUTIONS_TABLE} e "
            "WHERE e.record_type='run' AND e.run_id=%s",
            (str(run_id if run_id is not None else self.run_id),),
        )

    def run_rows(
        self,
        *,
        run_id: str = "",
        like: str = "",
        model: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Runs recorded in MySQL, newest first."""
        where: List[str] = ["e.record_type='run'"]
        params: List[Any] = []
        if run_id:
            where.append("e.run_id=%s")
            params.append(str(run_id))
        if like:
            where.append("e.run_id LIKE %s")
            params.append(str(like))
        if model:
            where.append("e.model_id=%s")
            params.append(str(model))
        return self.query(
            f"SELECT {RUN_SELECT} FROM {EXECUTIONS_TABLE} e "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY COALESCE(e.run_finished_at, e.run_started_at) DESC, e.run_id DESC LIMIT %s",
            (*params, max(1, min(int(limit), 500))),
        )

    def attempt_rows(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retry attempts recorded for one run."""
        return self.query(
            f"SELECT {ATTEMPT_SELECT} FROM {EXECUTIONS_TABLE} e "
            "WHERE e.record_type='attempt' AND e.run_id=%s ORDER BY e.seqname, e.attempt_no",
            (str(run_id if run_id is not None else self.run_id),),
        )

    def record_counts(self, run_id: Optional[str] = None) -> Dict[str, int]:
        """How many run, case and attempt records one run id holds."""
        rows = self.query(
            f"SELECT record_type, COUNT(*) AS n FROM {EXECUTIONS_TABLE} "
            "WHERE run_id=%s GROUP BY record_type",
            (str(run_id if run_id is not None else self.run_id),),
        )
        return {str(row["record_type"]): int(row["n"]) for row in rows}

    def answer_table_version(self) -> str:
        """Short identity of the current answer table.

        A rebuilt run directory is only current while the answers are current,
        so this stamp is what decides whether a cached copy may be reused.
        """
        row = self.query_one(
            "SELECT COUNT(*) AS n, MAX(updated_at) AS changed, "
            "SUM(CRC32(gold_sha256)) AS checksum FROM trna_gold"
        ) or {}
        changed = row.get("changed")
        return (
            f"{int(row.get('n') or 0)}:"
            f"{changed.isoformat(timespec='seconds') if changed else '-'}:"
            f"{row.get('checksum') or 0}"
        )

    # ------------------------------------------------------------------ cases
    def put_case(self, result: Mapping[str, Any], *, attempt: int = 1) -> Dict[str, Any]:
        seqname = str(result.get("seqname") or "").strip()
        if not seqname:
            raise BenchmarkStoreError("case result has no seqname")
        body = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        trace = self._upload(self.object_key("cases", f"{case_digest(seqname)}.json"), body)
        self.upsert_case_row(result, trace=trace, attempt=attempt)
        return trace

    def upsert_case_row(
        self,
        result: Mapping[str, Any],
        *,
        trace: Mapping[str, Any],
        attempt: int = 1,
    ) -> None:
        flat = flatten_case_result(result)
        columns: Dict[str, Any] = {
            "run_id": self.run_id,
            "record_type": "case",
            "seqname": str(flat["seqname"]),
            "attempt_no": 0,
        }
        for name, value in flat.items():
            if name == "seqname" or name in ANSWER_DERIVED_CASE_FIELDS:
                continue
            if name in BOOL_FIELDS:
                columns[f"case_{name}"] = _to_bool(value)
            elif name in INT_FIELDS:
                columns[f"case_{name}"] = _to_number(value, integer=True)
            elif name in FLOAT_FIELDS:
                columns[f"case_{name}"] = _to_number(value, integer=False)
            else:
                columns[f"case_{name}"] = _to_text(value)
        columns["case_attempt"] = int(attempt)
        columns["case_technical_issue_count"] = _technical_issues(result)
        columns["case_trace_key"] = _to_text(trace.get("key"))
        columns["case_trace_sha256"] = _to_text(trace.get("sha256"))
        columns["case_trace_bytes"] = _to_number(trace.get("bytes"), integer=True)
        columns["case_prediction_csv"] = _to_text(result.get("csv_content"))
        columns["case_stored_at"] = _now()
        columns["case_updated_at"] = _now()
        names = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        updates = ", ".join(
            f"{name} = VALUES({name})"
            for name in columns
            if name not in {"run_id", "record_type", "seqname", "attempt_no", "case_stored_at"}
        )
        last_error: Optional[Exception] = None
        for _ in range(3):
            try:
                with self.tx() as cursor:
                    cursor.execute(
                        f"INSERT INTO {EXECUTIONS_TABLE} ({names}) VALUES ({placeholders}) "
                        f"ON DUPLICATE KEY UPDATE {updates}",
                        list(columns.values()),
                    )
                return
            except pymysql.Error as exc:
                last_error = exc
                time.sleep(1.0)
        raise BenchmarkStoreError(f"MySQL case upsert failed for {columns.get('seqname')}: {last_error}")

    def put_attempt(
        self,
        seqname: str,
        attempt_no: int,
        *,
        outcome: str,
        error: str = "",
        latency_sec: Any = None,
        usage: Optional[Mapping[str, Any]] = None,
        object_key: Optional[str] = None,
    ) -> None:
        with self.tx() as cursor:
            cursor.execute(
                f"INSERT INTO {EXECUTIONS_TABLE} (run_id, record_type, seqname, attempt_no, "
                f"attempt_outcome, attempt_error, latency_ms, usage_json, object_key, attempt_recorded_at) "
                f"VALUES (%s, 'attempt', %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP(6)) "
                f"ON DUPLICATE KEY UPDATE attempt_outcome=VALUES(attempt_outcome), "
                f"attempt_error=VALUES(attempt_error), latency_ms=VALUES(latency_ms), "
                f"usage_json=VALUES(usage_json), object_key=VALUES(object_key), "
                f"attempt_recorded_at=CURRENT_TIMESTAMP(6)",
                (
                    self.run_id,
                    str(seqname),
                    int(attempt_no),
                    _to_text(outcome),
                    _to_text(error),
                    _to_number(
                        float(latency_sec) * 1000 if latency_sec not in (None, "") else None,
                        integer=True,
                    ),
                    json.dumps(dict(usage or {}), ensure_ascii=False, sort_keys=True),
                    _to_text(object_key),
                ),
            )

    def revised_answers(self, run_id: Optional[str] = None) -> Dict[str, int]:
        """Answers that moved after one run was sealed.  See ``revised_answers_for``."""
        target = str(run_id if run_id is not None else self.run_id)
        return self.revised_answers_for([target]).get(target, {})

    def revised_answers_for(self, run_ids: Sequence[str]) -> Dict[str, Dict[str, int]]:
        """How many answers a batch now sits on that were decided after it finished.

        Nothing here is stale: a run stores predictions, and every reader grades
        them against the current ``trna_gold``.  This count only says how far the
        answers have moved since the batch ran, which is worth showing because
        the numbers on screen differ from the ones the run was sealed with.
        """
        names = [str(run_id) for run_id in run_ids]
        if not names:
            return {}
        rows = self.query(
            "SELECT e.run_id, COUNT(*) AS total, "
            "  SUM(g.seqname IS NULL) AS missing, "
            "  SUM(g.decided_at IS NOT NULL AND r.run_finished_at IS NOT NULL "
            "      AND g.decided_at > r.run_finished_at) AS revised "
            f"FROM {EXECUTIONS_TABLE} e "
            f"LEFT JOIN {EXECUTIONS_TABLE} r ON r.record_type='run' AND r.run_id=e.run_id "
            "LEFT JOIN trna_gold g ON g.seqname = e.seqname "
            "WHERE e.record_type='case' AND e.run_id IN "
            f"({', '.join(['%s'] * len(names))}) GROUP BY e.run_id",
            names,
        )
        return {
            str(row["run_id"]): {
                "total": int(row.get("total") or 0),
                "missing": int(row.get("missing") or 0),
                "revised": int(row.get("revised") or 0),
            }
            for row in rows
        }

    @staticmethod
    def _json_list(value: Any) -> List[str]:
        """Decode a JSON list stored in ``trna_gold`` without trusting SQL types."""
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, ValueError):
            parsed = []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    @staticmethod
    def _apply_active_answer(row: Dict[str, Any]) -> Dict[str, Any]:
        """Grade one stored prediction against the current answer table.

        The score fields on the returned row come from ``trna_gold``, never from
        a column written at run time: the answer table is their only home.
        """
        answer = {
            name: row.pop(f"active_{name}", None)
            for name in (
                "gold_labels",
                "gold_bases",
                "gold_sha256",
                "gold_source",
                "decided_at",
                "evaluation_allowed",
            )
        }
        # A case whose sequence has no answer carries no score at all; an answer
        # row that says "do not score" is handled below.
        if answer["gold_labels"] is None:
            return row
        labels = BenchmarkRunStore._json_list(answer["gold_labels"])
        bases = BenchmarkRunStore._json_list(answer["gold_bases"])
        allowed = bool(answer["evaluation_allowed"]) and bool(labels)
        prediction_csv = row.get("prediction_csv") or ""
        predicted_labels, predicted_bases = pred_residue_map(prediction_csv)
        # Carried on the graded row so a rebuilt run directory can show the
        # question next to the frozen answer; never written to a column.
        row["active_gold_labels"] = labels
        row["active_gold_bases"] = bases
        legality = bool(row.get("legality_passed"))
        metrics = score_labels(labels, predicted_labels, legality_passed=legality) if allowed else unscored_metrics(legality_passed=legality)
        if allowed:
            row["base_preserved"] = int(
                "".join(predicted_bases).upper().replace("T", "U")
                == "".join(bases).upper().replace("T", "U")
            )
        for key, value in metrics.items():
            row[key] = value
        row["label_incorrect"] = (
            metrics["label_total"] - metrics["label_correct"]
            if metrics["label_correct"] is not None
            else None
        )
        row["gold_evaluation_allowed"] = allowed
        row["gold_labels_sha256"] = answer["gold_sha256"] or row.get("gold_labels_sha256")
        row["gold_source"] = answer["gold_source"] or row.get("gold_source")
        row["correction_decision"] = str(answer["decided_at"] or "") or row.get("correction_decision")
        if allowed:
            diffs = build_diffs(labels, bases, predicted_labels, predicted_bases)
            row["diff_count"] = len(diffs)
            row["diff_regions"] = ",".join(sorted({str(item.get("region") or "") for item in diffs if item.get("region")}))
            row["priority"] = priority_for_diffs(diffs, legality_passed=legality)
        else:
            row["diff_count"] = None
            row["diff_regions"] = ""
        return row

    def refresh_run_summary(
        self,
        summary: Mapping[str, Any],
        *,
        run_id: Optional[str] = None,
    ) -> None:
        """Replace the aggregate a sealed run carries, without touching traces."""
        target = str(run_id if run_id is not None else self.run_id)
        with self.tx() as cursor:
            cursor.execute(
                f"UPDATE {EXECUTIONS_TABLE} SET summary_json=%s, counts_json=%s, "
                "run_updated_at=CURRENT_TIMESTAMP(6) "
                "WHERE record_type='run' AND run_id=%s",
                (
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(summary.get("counts") or {}), ensure_ascii=False, sort_keys=True),
                    target,
                ),
            )

    def case_rows(
        self,
        *,
        run_id: Optional[str] = None,
        seqnames: Optional[Sequence[str]] = None,
        status: str = "",
        active_answer: bool = True,
        columns: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Stored cases for one run, with canonical field names.

        ``active_answer`` controls whether each row is re-graded against the
        current ``trna_gold`` row.  Readers that report a score leave it on;
        progress polling turns it off because it only needs run-time facts.

        ``columns`` narrows the projection to the named canonical fields, which
        keeps progress polling from hauling every stored prediction off the
        server.  Grading needs the whole row, so a narrowed read is always
        ungraded.
        """
        target = str(run_id if run_id is not None else self.run_id)
        if columns is not None:
            if active_answer:
                raise BenchmarkStoreError("a narrowed case read cannot be re-graded; pass active_answer=False")
            wanted = {str(name) for name in columns}
            unknown = sorted(wanted - set(CASE_CANONICAL_COLUMNS))
            if unknown:
                raise BenchmarkStoreError(f"unknown case fields: {', '.join(unknown)}")
            wanted.update({"run_id", "seqname"})
            projection = _project(
                [pair for pair in CASE_COLUMN_PAIRS if pair[1] in wanted], "e"
            )
        else:
            projection = CASE_SELECT + (f", {GOLD_SELECT}" if active_answer else "")
        query = (
            f"SELECT {projection} FROM {EXECUTIONS_TABLE} e "
            "LEFT JOIN trna_gold g ON g.seqname = e.seqname "
            "WHERE e.record_type='case' AND e.run_id=%s"
        )
        params: List[Any] = [target]
        if seqnames is not None:
            names = [str(name) for name in seqnames]
            if not names:
                return []
            query += f" AND e.seqname IN ({', '.join(['%s'] * len(names))})"
            params.extend(names)
        if status:
            query += " AND e.case_status=%s"
            params.append(status)
        query += " ORDER BY e.seqname"
        rows = self.query(query, params)
        if not active_answer:
            return rows
        return [self._apply_active_answer(dict(row)) for row in rows]

    def case_row(self, seqname: str, *, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rows = self.case_rows(run_id=run_id, seqnames=[seqname])
        return rows[0] if rows else None

    def list_rows(self, *, status: str = "", run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every scored case of a run; the name kept for existing callers."""
        return self.case_rows(run_id=run_id, status=status)

    def case_json(
        self, seqname: str, *, run_id: Optional[str] = None, active_answer: bool = True
    ) -> Optional[Dict[str, Any]]:
        row = self.case_row(seqname, run_id=run_id)
        key = str((row or {}).get("trace_key") or "") or self.object_key(
            "cases", f"{case_digest(seqname)}.json"
        )
        body = self._download(key)
        if body is None:
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                return None
            # The stored trace is the model's own record and never changes; the
            # numbers shown with it are re-graded against the current answer.
            if active_answer:
                payload = apply_active_answer_to_trace(payload, row)
            return payload
        except json.JSONDecodeError as exc:
            raise BenchmarkStoreError(f"stored case trace is not valid JSON: {key}") from exc

    # -------------------------------------------------------------- artifacts
    def put_artifact(self, name: str, payload: Any, *, content_type: str = "application/json") -> Dict[str, Any]:
        if isinstance(payload, (bytes, bytearray)):
            body = bytes(payload)
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        return self._upload(self.object_key(name), body, content_type=content_type)

    def get_artifact(self, name: str) -> Optional[bytes]:
        return self._download(self.object_key(name))

    def get_object(self, key: str) -> Optional[bytes]:
        """Download any object this store wrote, by its full key."""
        return self._download(str(key))

    def list_object_keys(self, sub_prefix: str = "") -> List[str]:
        prefix = self.object_key(sub_prefix) if sub_prefix else f"{self.object_prefix}/"
        keys: List[str] = []
        token: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                params["ContinuationToken"] = token
            page = self._s3.list_objects_v2(**params)
            keys.extend(str(item["Key"]) for item in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return keys
            token = page.get("NextContinuationToken")

    def build_pointer(self) -> Dict[str, Any]:
        """The tiny record a repository keeps: where this run actually lives."""
        row = self.run_row()
        if not row:
            raise BenchmarkStoreError(f"no such run in MySQL: {self.run_id}")
        counts = self.query_one(
            f"SELECT COUNT(*) AS cases, SUM(case_status='ok') AS ok_cases "
            f"FROM {EXECUTIONS_TABLE} WHERE record_type='case' AND run_id=%s",
            (self.run_id,),
        ) or {}
        attempts = self.query_one(
            f"SELECT COUNT(*) AS attempts FROM {EXECUTIONS_TABLE} "
            f"WHERE record_type='attempt' AND run_id=%s",
            (self.run_id,),
        ) or {}
        info = self.ping()
        return {
            "run_id": self.run_id,
            "state": row.get("state"),
            "model": row.get("model_id"),
            "provider": row.get("provider"),
            "method_backend": row.get("method_backend"),
            "git_commit": row.get("code_commit"),
            "started_at": str(row.get("started_at") or ""),
            "finished_at": str(row.get("finished_at") or ""),
            "cases": int(counts.get("cases") or 0),
            "ok_cases": int(counts.get("ok_cases") or 0),
            "attempts": int(attempts.get("attempts") or 0),
            "gold_source": row.get("gold_source"),
            "gold_sha256": row.get("gold_sha256"),
            "run_contract_sha256": row.get("run_contract_sha256"),
            "storage": {
                "mysql": {
                    "host": info["mysql_host"],
                    "database": info["mysql_database"],
                    "executions_table": EXECUTIONS_TABLE,
                },
                "minio": {
                    "endpoint": info["minio_endpoint"],
                    "bucket": info["bucket"],
                    "prefix": info["object_prefix"],
                    "archive_key": row.get("archive_key"),
                    "archive_sha256": row.get("archive_sha256"),
                    "archive_bytes": row.get("archive_bytes"),
                },
            },
            "materialize": f"python benchmark/scripts/export_run_from_store.py --run-id {self.run_id}",
        }

    def write_pointer(self, path: Path) -> Dict[str, Any]:
        payload = self.build_pointer()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return payload


def _technical_issues(result: Mapping[str, Any]) -> int:
    """Count transport-level failures recorded inside an otherwise saved case."""
    failed_requests = 0
    for call in (result.get("llm_usage") or {}).get("calls", []):
        status = call.get("status_code")
        if str(status) != "200" or call.get("error_type"):
            failed_requests += 1
    fallbacks = 0
    for event in (result.get("agent_trace") or {}).get("trace_events", []):
        decision = event.get("agent_decision") or event
        if decision.get("planner_source") == "deterministic_fallback":
            fallbacks += 1
    return failed_requests + fallbacks


def list_run_rows(*, model: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """Runs recorded in MySQL, newest first, without needing a run id."""
    return BenchmarkRunStore.readonly().run_rows(model=model, limit=limit)


def default_run_cache_dir() -> Path:
    """Where materialized copies of stored runs are kept (never in git)."""
    return Path(_env("BENCHMARK_RUN_CACHE", str(PROJECT_ROOT / ".runtime" / "runs")))


def resolve_run_dir(value: Any, *, allow_materialize: bool = True) -> Path:
    """Turn a run id or a path into a directory a script can read.

    Runs live in MySQL and MinIO, so a missing directory is materialized from
    the store instead of failing.
    """
    path = Path(str(value))
    if path.is_dir():
        return path
    run_id = path.name if str(value) else ""
    for root in (default_run_cache_dir(), LEGACY_RUNS_DIR):
        candidate = root / run_id
        if run_id and candidate.is_dir():
            if root is LEGACY_RUNS_DIR or _cache_is_current(candidate, run_id):
                return candidate
            LOG.info("run cache %s is built on an older answer table; rebuilding", run_id)
    if not run_id:
        raise BenchmarkStoreError("run id or run directory is required")
    if not allow_materialize:
        raise BenchmarkStoreError(f"run directory is missing: {path}")
    return materialize_run(run_id)


MATERIALIZED_STAMP = ".materialized.json"


def _cache_is_current(target: Path, run_id: str) -> bool:
    """Whether a cached run directory was built from today's answers."""
    stamp_path = target / MATERIALIZED_STAMP
    if not stamp_path.exists():
        return False
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if str(stamp.get("run_id") or "") != run_id or not stamp.get("live_answer"):
        return False
    try:
        current = BenchmarkRunStore.readonly().answer_table_version()
    except BenchmarkStoreError:
        # MySQL is unreachable: a cached copy is better than no copy, and the
        # stamp says which answers it was built on.
        return True
    return str(stamp.get("answer_table_version") or "") == current


def _merge_summary(
    base: Mapping[str, Any],
    live: Mapping[str, Any],
) -> Dict[str, Any]:
    """Overlay freshly graded sections on the summary the run stored.

    Sections are merged key by key so facts that only a trace can carry, such as
    which model answered which call, survive a re-grade while every accuracy
    number comes from the current answer table.
    """
    merged: Dict[str, Any] = dict(base)
    for section, values in live.items():
        current = merged.get(section)
        if isinstance(values, dict) and isinstance(current, dict):
            merged[section] = {**current, **values}
        else:
            merged[section] = values
    return merged


def materialize_run(
    run_id: str,
    dest: Optional[Path] = None,
    *,
    workers: int = 12,
    live_answer: bool = True,
) -> Path:
    """Rebuild a run directory on disk from MinIO objects.

    Without ``dest`` the copy lands in the git-ignored scratch cache.  Report,
    figure and comparison scripts still think in terms of a run directory, so
    this turns a stored run back into one.  The archive blob is skipped: its
    unpacked contents are already the per-case objects.

    With ``live_answer`` (the default) the traces, the run index and the run
    summary are re-graded against the current answer table while they are being
    written, and ``provenance.json``-style notes record that the model output is
    frozen while the scores follow the answers.  Pass ``live_answer=False`` to
    fetch exactly what the run itself stored.
    """
    store = BenchmarkRunStore(run_id)
    target = Path(dest) if dest else default_run_cache_dir() / run_id
    target.mkdir(parents=True, exist_ok=True)
    prefix = store.object_prefix.rstrip("/") + "/"
    keys = [key for key in store.list_object_keys() if not key.endswith(".tar.zst")]

    graded: Dict[str, Dict[str, Any]] = {}
    by_trace: Dict[str, Dict[str, Any]] = {}
    if live_answer:
        for row in store.case_rows():
            graded[str(row.get("seqname") or "")] = row
            trace_key = str(row.get("trace_key") or "")
            if trace_key:
                by_trace[trace_key] = row

    def fetch(key: str) -> bool:
        body = store.get_object(key)
        if body is None:
            return False
        out = target / key[len(prefix):]
        out.parent.mkdir(parents=True, exist_ok=True)
        row = by_trace.get(key)
        if row is not None:
            try:
                payload = apply_active_answer_to_trace(
                    json.loads(body.decode("utf-8")), row
                )
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError):
                LOG.warning("materialize %s: trace %s is not JSON", run_id, key)
        out.write_bytes(body)
        return True

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        written = sum(1 for ok in pool.map(fetch, keys) if ok)
    if written != len(keys):
        raise BenchmarkStoreError(
            f"materialize {run_id}: {len(keys) - written} of {len(keys)} objects missing from MinIO"
        )

    if graded:
        rows = [graded[name] for name in sorted(graded)]
        (target / "case_results.tsv").write_text(
            case_results_tsv_text(rows), encoding="utf-8"
        )
        # ``summary.txt`` stays as the run wrote it: it is that run's own
        # seal-time rendering.  ``summary.json`` and ``case_results.tsv`` carry
        # the scores the current answer table produces.
        summary_path = target / "summary.json"
        summary: Dict[str, Any] = {}
        if summary_path.exists():
            try:
                loaded = json.loads(summary_path.read_text(encoding="utf-8"))
                summary = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                summary = {}
        summary = _merge_summary(summary, summarize_case_rows(rows))
        summary["score_basis"] = {
            "answers": "mysql://trna_gold (current answer table)",
            "predictions": "frozen MinIO traces; no model call was replayed",
            "generated_at": _now().isoformat(timespec="seconds"),
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (target / MATERIALIZED_STAMP).write_text(
        json.dumps(
            {
                "run_id": run_id,
                "live_answer": bool(live_answer),
                "answer_table_version": store.answer_table_version(),
                "materialized_at": _now().isoformat(timespec="seconds"),
                "objects": written,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    store.write_pointer(target / "RUN.json")
    LOG.info("materialized run %s: %d objects -> %s", run_id, written, target)
    return target


def load_case_jsons(
    run_id: str,
    seqnames: Optional[Iterable[str]] = None,
    *,
    workers: int = 12,
    active_answer: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Every stored trace for a run (or for the named subset), read in parallel."""
    store = BenchmarkRunStore(run_id)
    rows: List[Dict[str, Any]] = []
    wanted = {str(name) for name in seqnames} if seqnames is not None else None
    for row in store.case_rows(active_answer=active_answer):
        name = str(row.get("seqname") or "")
        if wanted is None or name in wanted:
            rows.append(row)

    def fetch(row: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        key = str(row.get("trace_key") or "") or store.object_key(
            "cases", f"{case_digest(str(row.get('seqname') or ''))}.json"
        )
        body = store.get_object(key)
        if body is None:
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if active_answer and isinstance(payload, dict):
            payload = apply_active_answer_to_trace(payload, row)
        return str(row.get("seqname")), payload

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        return {name: payload for name, payload in pool.map(fetch, rows) if payload is not None}


def connect_store(run_id: str, *, create_schema: bool = True) -> BenchmarkRunStore:
    """Build a store and verify both backends, raising on any failure."""
    store = BenchmarkRunStore(run_id)
    info = store.ping()
    if create_schema:
        store.ensure_schema()
    LOG.info(
        "run_store ready mysql=%s/%s minio=%s/%s prefix=%s",
        info["mysql_host"],
        info["mysql_database"],
        info["minio_endpoint"],
        info["bucket"],
        info["object_prefix"],
    )
    return store
