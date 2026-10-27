"""Persistent sequence, curation history and versioned computation cache.

Enabled explicitly on the web server, never implicitly in benchmark workers.
"""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
import pymysql
from backend.infra.config.mysql_settings import load_mysql_config

ROOT = Path(__file__).resolve().parents[4]

def enabled():
    return os.getenv('SEQUENCE_REPOSITORY_ENABLED', '').lower() in {'1', 'true', 'yes'}

def connection():
    cfg = load_mysql_config(include_timeouts=True)
    if not cfg:
        raise RuntimeError('Sequence repository MySQL configuration is missing')
    return pymysql.connect(**cfg)

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)

def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()

def normalize(sequence):
    return ''.join(str(sequence).split()).upper().replace('T', 'U')

def timestamp(record):
    raw = record.get('curated_at') or record.get('drafted_at') or record.get('updated_at')
    dt = datetime.fromisoformat(raw.replace('Z', '+00:00')) if raw else datetime(1970, 1, 1, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

def ensure_schema():
    definitions = [
        '''CREATE TABLE IF NOT EXISTS trna_sequences (
        sequence_hash CHAR(64) PRIMARY KEY, sequence LONGTEXT NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6))''',
        '''CREATE TABLE IF NOT EXISTS trna_sequence_aliases (
        seqname VARCHAR(255) PRIMARY KEY, sequence_hash CHAR(64) NOT NULL,
        baseline LONGTEXT NULL, INDEX(sequence_hash))''',
        '''CREATE TABLE IF NOT EXISTS trna_annotations (
        record_hash CHAR(64) PRIMARY KEY, seqname VARCHAR(255) NOT NULL,
        kind VARCHAR(16) NOT NULL, review_round VARCHAR(128) NOT NULL,
        run_id VARCHAR(255) NOT NULL, review_scope VARCHAR(128) NOT NULL,
        recorded_at DATETIME(6) NOT NULL, status VARCHAR(32) NOT NULL,
        payload LONGTEXT NOT NULL, imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX(seqname,kind,recorded_at), INDEX(kind,review_round), INDEX(kind,run_id,review_scope))''',
        '''CREATE TABLE IF NOT EXISTS trna_annotation_heads (
        seqname VARCHAR(255) PRIMARY KEY, record_hash CHAR(64) NOT NULL,
        recorded_at DATETIME(6) NOT NULL, status VARCHAR(32) NOT NULL)''',
        # One active answer per sequence.  ``raw_*`` is the untouched tRNAdb
        # numbering kept as reference, ``gold_*`` is what scoring reads, and the
        # source columns say who produced it and when.  ``trna_annotations``
        # stays the append-only audit trail behind it.
        '''CREATE TABLE IF NOT EXISTS trna_gold (
        seqname VARCHAR(255) PRIMARY KEY,
        raw_labels LONGTEXT NOT NULL, raw_bases LONGTEXT NOT NULL, raw_sha256 CHAR(64) NOT NULL,
        gold_labels LONGTEXT NOT NULL, gold_bases LONGTEXT NOT NULL, gold_sha256 CHAR(64) NOT NULL,
        gold_source VARCHAR(32) NOT NULL, decided_at DATETIME(6) NULL,
        decision_hash CHAR(64) NULL, decision_run_id VARCHAR(255) NULL,
        evaluation_allowed TINYINT(1) NOT NULL DEFAULT 1,
        exclusion_reason VARCHAR(64) NULL, excluded_at DATETIME(6) NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
        INDEX(gold_source), INDEX(gold_sha256), INDEX(decided_at))''',
        '''CREATE TABLE IF NOT EXISTS trna_computations (
        run_id CHAR(32) PRIMARY KEY, cache_key CHAR(64) NOT NULL,
        sequence_hash CHAR(64) NOT NULL, config LONGTEXT NOT NULL,
        state VARCHAR(16) NOT NULL, result LONGTEXT NULL, events LONGTEXT NOT NULL,
        error LONGTEXT NULL, started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        finished_at DATETIME(6) NULL, INDEX(cache_key,state,finished_at), INDEX(sequence_hash))''',
        '''CREATE TABLE IF NOT EXISTS trna_cache (
        cache_key CHAR(64) PRIMARY KEY, run_id CHAR(32) NOT NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6))''',
        '''CREATE TABLE IF NOT EXISTS trna_requests (
        request_id CHAR(32) PRIMARY KEY, sequence_hash CHAR(64) NOT NULL,
        cache_key CHAR(64) NOT NULL, run_id CHAR(32) NULL,
        cache_hit BOOLEAN NOT NULL, requested_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX(sequence_hash), INDEX(run_id))''',
    ]
    with connection() as c:
        with c.cursor() as x:
            for definition in definitions:
                x.execute(definition + ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
        c.commit()
    _add_missing_gold_columns()


GOLD_ADDED_COLUMNS = {'exclusion_reason': 'VARCHAR(64) NULL', 'excluded_at': 'DATETIME(6) NULL'}


def _add_missing_gold_columns():
    """Add the scoring-eligibility columns to an answer table that predates them."""
    with connection() as c:
        with c.cursor() as x:
            x.execute('SELECT COLUMN_NAME FROM information_schema.columns '
                      'WHERE table_schema=DATABASE() AND table_name=%s', ('trna_gold',))
            existing = {str(r['COLUMN_NAME']) for r in x.fetchall()}
            missing = [(name, ddl) for name, ddl in GOLD_ADDED_COLUMNS.items() if name not in existing]
            if not missing:
                return []
            x.execute('ALTER TABLE trna_gold ' + ', '.join(
                f'ADD COLUMN `{name}` {ddl}' for name, ddl in missing))
        c.commit()
    return [name for name, _ in missing]

def put_sequence(cursor, sequence, seqname=None, baseline=None):
    normalized = normalize(sequence)
    key = hashlib.sha256(normalized.encode()).hexdigest()
    cursor.execute('INSERT IGNORE INTO trna_sequences(sequence_hash,sequence) VALUES(%s,%s)', (key, normalized))
    if seqname:
        cursor.execute('INSERT INTO trna_sequence_aliases(seqname,sequence_hash,baseline) VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE sequence_hash=VALUES(sequence_hash),baseline=COALESCE(VALUES(baseline),baseline)', (seqname,key,encoded(baseline) if baseline is not None else None))
    return key

def baseline_rows(seqnames=None):
    """Return baseline gold rows stored in MySQL, keyed by sequence name."""
    if not enabled():
        return {}
    with connection() as c:
        with c.cursor() as x:
            if seqnames:
                names = [str(v) for v in seqnames]
                placeholders = ",".join(["%s"] * len(names))
                x.execute(
                    "SELECT seqname, baseline FROM trna_sequence_aliases "
                    f"WHERE seqname IN ({placeholders})",
                    names,
                )
            else:
                x.execute("SELECT seqname, baseline FROM trna_sequence_aliases")
            out = {}
            for row in x.fetchall():
                try:
                    value = json.loads(row["baseline"] or "{}")
                except (TypeError, ValueError):
                    value = {}
                if isinstance(value, dict):
                    out[str(row["seqname"])] = value
            return out

# ---------------------------------------------------------------- gold table
# One active answer per sequence.  ``raw_*`` is the untouched tRNAdb numbering,
# ``gold_*`` is what every benchmark and every score reads, and the source
# columns record who produced it.  ``trna_annotations`` keeps the full history.

def residue_digest(labels, bases):
    return hashlib.sha256(encoded([list(labels), list(bases)]).encode()).hexdigest()

def _gold_params(row):
    labels = [str(v) for v in (row.get("gold_labels") or [])]
    bases = [str(v) for v in (row.get("gold_bases") or [])]
    raw_labels = [str(v) for v in (row.get("raw_labels") or [])]
    raw_bases = [str(v) for v in (row.get("raw_bases") or [])]
    return (
        str(row["seqname"]), encoded(raw_labels), encoded(raw_bases),
        residue_digest(raw_labels, raw_bases), encoded(labels), encoded(bases),
        residue_digest(labels, bases), str(row.get("gold_source") or "raw"),
        row.get("decided_at"), row.get("decision_hash"), row.get("decision_run_id"),
        0 if row.get("evaluation_allowed") is False else 1,
        str(row.get("exclusion_reason") or "") or None,
        row.get("excluded_at"),
    )


GOLD_UPSERT = (
    'INSERT INTO trna_gold(seqname,raw_labels,raw_bases,raw_sha256,gold_labels,gold_bases,'
    'gold_sha256,gold_source,decided_at,decision_hash,decision_run_id,evaluation_allowed,'
    'exclusion_reason,excluded_at) '
    'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
    'ON DUPLICATE KEY UPDATE raw_labels=VALUES(raw_labels),raw_bases=VALUES(raw_bases),'
    'raw_sha256=VALUES(raw_sha256),gold_labels=VALUES(gold_labels),gold_bases=VALUES(gold_bases),'
    'gold_sha256=VALUES(gold_sha256),gold_source=VALUES(gold_source),decided_at=VALUES(decided_at),'
    'decision_hash=VALUES(decision_hash),decision_run_id=VALUES(decision_run_id),'
    'evaluation_allowed=VALUES(evaluation_allowed),'
    'exclusion_reason=VALUES(exclusion_reason),excluded_at=VALUES(excluded_at)'
)


def put_gold(cursor, row):
    """Insert or refresh the single active answer row for one sequence."""
    cursor.execute(GOLD_UPSERT, _gold_params(row))


def put_gold_many(cursor, rows):
    """Batched answer upsert; the table is rebuilt from scratch in rounds."""
    cursor.executemany(GOLD_UPSERT, [_gold_params(row) for row in rows])


def gold_rows(seqnames=None):
    """Return the active answer table keyed by sequence name."""
    if not enabled():
        return {}
    with connection() as c:
        with c.cursor() as x:
            if seqnames:
                names = [str(v) for v in seqnames]
                placeholders = ",".join(["%s"] * len(names))
                x.execute(
                    "SELECT seqname,raw_labels,raw_bases,gold_labels,gold_bases,gold_source,"
                    "decided_at,decision_hash,decision_run_id,evaluation_allowed,"
                    "exclusion_reason,excluded_at,gold_sha256 "
                    f"FROM trna_gold WHERE seqname IN ({placeholders})",
                    names,
                )
            else:
                x.execute(
                    "SELECT seqname,raw_labels,raw_bases,gold_labels,gold_bases,gold_source,"
                    "decided_at,decision_hash,decision_run_id,evaluation_allowed,"
                    "exclusion_reason,excluded_at,gold_sha256 FROM trna_gold"
                )
            out = {}
            for row in x.fetchall():
                out[str(row["seqname"])] = {
                    "seqname": str(row["seqname"]),
                    "raw_labels": json.loads(row["raw_labels"] or "[]"),
                    "raw_bases": json.loads(row["raw_bases"] or "[]"),
                    "gold_labels": json.loads(row["gold_labels"] or "[]"),
                    "gold_bases": json.loads(row["gold_bases"] or "[]"),
                    "gold_source": str(row["gold_source"] or "raw"),
                    "gold_sha256": str(row["gold_sha256"] or ""),
                    "decided_at": row["decided_at"],
                    "decision_hash": row["decision_hash"],
                    "decision_run_id": row["decision_run_id"],
                    "evaluation_allowed": bool(row["evaluation_allowed"]),
                    "exclusion_reason": str(row["exclusion_reason"] or "") or None,
                    "excluded_at": row["excluded_at"],
                }
            return out


EXCLUSION_REASONS = (
    'non_canonical_trna',
    'terminal_cca_undetermined',
    'variable_loop_undetermined',
    'incomplete_sequence',
)


def set_evaluation_allowed(seqname, allowed, reason=None, curated_at=None):
    """Flip whether one answer is scored, without touching the numbering itself.

    The decision axis (``trna_annotations``) says what the answer is; this axis
    only says whether the benchmark should use it.  Every flip is appended to
    ``trna_annotations`` with kind='inclusion' so the audit trail stays complete
    without overwriting the answer head.
    """
    clean = str(seqname or "").strip()
    if not clean:
        raise ValueError('seqname is required')
    allowed = bool(allowed)
    reason = str(reason or "").strip()
    if not allowed and reason not in EXCLUSION_REASONS:
        raise ValueError(f'unknown exclusion reason: {reason or "(empty)"}')
    if allowed:
        reason = None
    record = {'seqname': clean, 'allowed': allowed, 'exclusion_reason': reason,
              'curated_at': str(curated_at or '') or datetime.now().astimezone().isoformat(timespec='seconds')}
    with connection() as c:
        with c.cursor() as x:
            x.execute('SELECT 1 FROM trna_gold WHERE seqname=%s', (clean,))
            if x.fetchone() is None:
                raise LookupError(f'no answer row for {clean}')
            x.execute('UPDATE trna_gold SET evaluation_allowed=%s, exclusion_reason=%s, '
                      'excluded_at=%s WHERE seqname=%s',
                      (1 if allowed else 0, reason, None if allowed else timestamp(record), clean))
            x.execute('INSERT INTO trna_annotations(record_hash,seqname,kind,review_round,'
                      'run_id,review_scope,recorded_at,status,payload) '
                      'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                      'ON DUPLICATE KEY UPDATE recorded_at=VALUES(recorded_at),status=VALUES(status),'
                      'payload=VALUES(payload)',
                      (digest(record), clean, 'inclusion', '', '', '', timestamp(record),
                       'included' if allowed else 'excluded', encoded(record)))
        c.commit()
    return record

def gold_is_populated():
    if not enabled():
        return False
    with connection() as c:
        with c.cursor() as x:
            x.execute("SELECT COUNT(*) n FROM trna_gold")
            return int(x.fetchone()["n"] or 0) > 0

def gold_table_version():
    """Cheap identity of the current answer table: size, last change, checksum.

    An SQL aggregate, so a page can show which answer version its numbers sit on
    without reading 11,784 rows.
    """
    if not enabled():
        return None
    with connection() as c:
        with c.cursor() as x:
            x.execute(
                "SELECT COUNT(*) n, MAX(updated_at) changed, SUM(CRC32(gold_sha256)) checksum, "
                "SUM(gold_source<>'raw') decided, SUM(evaluation_allowed=0) excluded FROM trna_gold"
            )
            row = x.fetchone()
    if not row or not int(row["n"] or 0):
        return None
    return {
        "rows": int(row["n"]),
        "changed_at": row["changed"].isoformat(timespec="seconds") if row["changed"] else None,
        "checksum": str(row["checksum"] or ""),
        "decided": int(row["decided"] or 0),
        "excluded": int(row["excluded"] or 0),
    }

def gold_table_digest(seqnames=None) -> str:
    """Fingerprint of the whole answer table (or of one cohort inside it)."""
    rows = gold_rows(seqnames)
    payload = "\n".join(f"{name}\t{rows[name]['gold_sha256']}" for name in sorted(rows))
    return hashlib.sha256(payload.encode()).hexdigest()

def latest_formal_records():
    """Latest committed curation decision per sequence, with its record hash."""
    if not enabled():
        return {}
    with connection() as c:
        with c.cursor() as x:
            x.execute(
                "SELECT h.seqname,h.record_hash,h.recorded_at,h.status,a.run_id,a.review_round,a.payload "
                "FROM trna_annotation_heads h JOIN trna_annotations a ON a.record_hash=h.record_hash"
            )
            return {
                str(r["seqname"]): {
                    "record_hash": r["record_hash"],
                    "recorded_at": r["recorded_at"],
                    "run_id": r["run_id"],
                    "review_round": r["review_round"],
                    "status": r["status"],
                    "payload": json.loads(r["payload"] or "{}"),
                }
                for r in x.fetchall()
            }

def latest_formal_record(seqname):
    if not enabled():
        return None
    with connection() as c:
        with c.cursor() as x:
            x.execute(
                "SELECT h.seqname,h.record_hash,h.recorded_at,h.status,a.run_id,a.review_round,a.payload "
                "FROM trna_annotation_heads h JOIN trna_annotations a ON a.record_hash=h.record_hash "
                "WHERE h.seqname=%s",
                (str(seqname),),
            )
            r = x.fetchone()
    if not r:
        return None
    return {
        "record_hash": r["record_hash"],
        "recorded_at": r["recorded_at"],
        "run_id": r["run_id"],
        "review_round": r["review_round"],
        "status": r["status"],
        "payload": json.loads(r["payload"] or "{}"),
    }

def put_annotation(record, kind='formal', conn=None):
    if kind not in {'formal', 'draft'}:
        raise ValueError('Invalid annotation kind')
    owned = conn is None
    c = conn or connection()
    try:
        key = digest(record); when = timestamp(record)
        with c.cursor() as x:
            bases = record.get('bases_by_residue') or []
            if bases:
                put_sequence(x, ''.join(bases), record['seqname'])
            x.execute('INSERT IGNORE INTO trna_annotations(record_hash,seqname,kind,review_round,run_id,review_scope,recorded_at,status,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)', (key,record['seqname'],kind,record.get('review_round',''),record.get('run_id',''),record.get('review_scope',''),when,record.get('status') or record.get('decision') or 'draft',encoded(record)))
            if kind == 'formal':
                x.execute('SELECT recorded_at FROM trna_annotation_heads WHERE seqname=%s FOR UPDATE', (record['seqname'],))
                head=x.fetchone()
                if not head or when >= head['recorded_at']:
                    x.execute('INSERT INTO trna_annotation_heads(seqname,record_hash,recorded_at,status) VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE record_hash=VALUES(record_hash),recorded_at=VALUES(recorded_at),status=VALUES(status)', (record['seqname'],key,when,record.get('status') or record.get('decision') or ''))
        if owned:c.commit()
    finally:
        if owned:c.close()
    return key

def latest_annotations(kind='formal', review_round=None, run_id=None, review_scope=None):
    if kind == 'formal' and review_round is None and run_id is None and review_scope is None:
        with connection() as c:
            with c.cursor() as x:
                x.execute('SELECT a.seqname,a.payload FROM trna_annotation_heads h JOIN trna_annotations a ON a.record_hash=h.record_hash')
                return {r['seqname']:json.loads(r['payload']) for r in x.fetchall()}
    where=['kind=%s'];args=[kind]
    for column,value in [('review_round',review_round),('run_id',run_id),('review_scope',review_scope)]:
        if value is not None:
            where.append(column+'=%s');args.append(value)
    with connection() as c:
        with c.cursor() as x:
            x.execute('SELECT seqname,payload FROM trna_annotations WHERE '+' AND '.join(where)+' ORDER BY recorded_at,imported_at,record_hash', args)
            return {r['seqname']:json.loads(r['payload']) for r in x.fetchall()}

@lru_cache(maxsize=1)
def algorithm_version():
    h=hashlib.sha256()
    for directory in ['APP/backend/agent','APP/backend/domain/alignment','APP/backend/application/alignment','APP/backend/infra/llm']:
        for p in sorted((ROOT/directory).rglob('*.py')):
            h.update(str(p.relative_to(ROOT)).encode());h.update(p.read_bytes())
    for relative in ['data/tRNAs_flat.tsv','APP/backend/infra/storage/sequence_repository.py']:
        p=ROOT/relative
        if p.exists():h.update(p.read_bytes())
    # Covers external CM / structural models without storing their large files in MySQL.
    for directory in ['data','research/third_party','research/model_predict']:
        for p in sorted((ROOT/directory).rglob('*')):
            if p.is_file() and p.suffix in {'.cm','.pkl','.pt','.pth','.onnx'}:
                h.update(str(p.relative_to(ROOT)).encode());h.update(str(p.stat().st_size).encode());h.update(str(p.stat().st_mtime_ns).encode())
    return h.hexdigest()

def safe_config(value):
    if isinstance(value,dict):
        return {k:('[redacted]' if any(s in k.lower() for s in ['password','api_key','authorization','secret']) else safe_config(v)) for k,v in value.items()}
    if isinstance(value,list):return [safe_config(v) for v in value]
    return value

def has_technical_error(value):
    if isinstance(value, dict):
        if value.get('error_type') or value.get('tool_error_type') or value.get('planner_source') == 'deterministic_fallback':
            return True
        return any(has_technical_error(v) for v in value.values())
    if isinstance(value, list):
        return any(has_technical_error(v) for v in value)
    return False

def execute_cached(sequence, options, runtime, compute, callback=None, output_path='/dev/null', bypass_cache=False):
    """compute(callback) -> (template,csv,warnings,meta); exceptions stay failures."""
    config={'options':options,'runtime':safe_config(runtime),'environment':safe_config({k:v for k,v in os.environ.items() if k.startswith(('LLM_', 'AGENT_', 'ALIGN_', 'CM_', 'TRNASCAN_', 'RNAFOLD_', 'TRNAALIGN_'))}),'algorithm_version':algorithm_version()}
    key=digest({'sequence':normalize(sequence),'config':config})
    request_id=uuid.uuid4().hex
    with connection() as c:
        with c.cursor() as x:
            sequence_hash=put_sequence(x,sequence)
            x.execute('SELECT c.run_id,c.result FROM trna_cache k JOIN trna_computations c ON c.run_id=k.run_id WHERE k.cache_key=%s AND c.state=%s', (key,'success'))
            cached=None if bypass_cache else x.fetchone()
            run_id=cached['run_id'] if cached else uuid.uuid4().hex
            x.execute('INSERT INTO trna_requests(request_id,sequence_hash,cache_key,run_id,cache_hit) VALUES(%s,%s,%s,%s,%s)',(request_id,sequence_hash,key,run_id,bool(cached)))
            if not cached:
                x.execute('INSERT INTO trna_computations(run_id,cache_key,sequence_hash,config,state,events) VALUES(%s,%s,%s,%s,%s,%s)',(run_id,key,sequence_hash,encoded(config),'running','[]'))
        c.commit()
    if cached:
        result=json.loads(cached['result']);result[3]['cache']={'hit':True,'run_id':run_id,'request_id':request_id}
        result[3].setdefault('agent_v2', {})['cache'] = dict(result[3]['cache'])
        if callback:callback({'stage':'cache_hit','message':'已命中相同序列与配置的历史计算结果','process_event':{'stage':'cache_hit','source':'system','at':datetime.now(timezone.utc).isoformat(),'run_id':run_id}})
        if output_path and output_path!='/dev/null':Path(output_path).write_text(result[1])
        return tuple(result)
    events=[]
    def progress(event):
        events.append(safe_config(event))
        with connection() as c:
            with c.cursor() as x:
                x.execute('UPDATE trna_computations SET events=%s WHERE run_id=%s', (encoded(events),run_id))
            c.commit()
        if callback:callback(event)
    try:
        result=compute(progress)
        valid=bool(result[1]) and not result[3].get('error')
        meta=result[3]
        # Technical fallback outcomes are retained but are not reusable successful computations.
        technical_failure = bool(meta.get('llm_error') or meta.get('technical_failure') or has_technical_error(meta) or has_technical_error(events))
        if technical_failure:valid=False
        with connection() as c:
            with c.cursor() as x:
                x.execute('UPDATE trna_computations SET state=%s,result=%s,events=%s,finished_at=UTC_TIMESTAMP(6) WHERE run_id=%s',('success' if valid else 'technical_failed' if technical_failure else 'abstained',encoded(safe_config(list(result))),encoded(events),run_id))
                if valid:x.execute('INSERT INTO trna_cache(cache_key,run_id) VALUES(%s,%s) ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),updated_at=UTC_TIMESTAMP(6)',(key,run_id))
            c.commit()
        result[3]['cache']={'hit':False,'run_id':run_id,'request_id':request_id}
        result[3].setdefault('agent_v2', {})['cache'] = dict(result[3]['cache'])
        return result
    except Exception as exc:
        with connection() as c:
            with c.cursor() as x:x.execute('UPDATE trna_computations SET state=%s,error=%s,events=%s,finished_at=UTC_TIMESTAMP(6) WHERE run_id=%s',('failed',str(exc)[:4000],encoded(events),run_id))
            c.commit()
        raise
