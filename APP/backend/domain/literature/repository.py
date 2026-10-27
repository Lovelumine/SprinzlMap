"""MySQL storage for the paper corpus: citations plus searchable passages.

The corpus is derived data.  Deleting the tables is safe because the PDFs and
the category file in git can rebuild them.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pymysql

from backend.infra.config.mysql_settings import load_mysql_config

DOCUMENTS_TABLE = "literature_documents"
PASSAGES_TABLE = "literature_passages"

_BOOLEAN_OPERATORS = set('+-~><()@"*')


def connection() -> pymysql.connections.Connection:
    cfg = load_mysql_config(include_timeouts=True)
    if not cfg:
        raise RuntimeError("literature repository needs MYSQL_* configuration")
    return pymysql.connect(**cfg)


def doc_key(file_name: str) -> str:
    return hashlib.sha256(str(file_name).encode("utf-8")).hexdigest()[:32]


def ensure_schema(conn=None) -> None:
    own = conn is None
    handle = conn or connection()
    try:
        with handle.cursor() as cursor:
            cursor.execute(
                f"""CREATE TABLE IF NOT EXISTS {DOCUMENTS_TABLE} (
                doc_key CHAR(32) PRIMARY KEY,
                file_name VARCHAR(255) NOT NULL,
                corpus_dir VARCHAR(32) NOT NULL,
                title VARCHAR(512) NOT NULL,
                authors TEXT NULL,
                year VARCHAR(8) NULL,
                venue VARCHAR(255) NULL,
                doi VARCHAR(320) NULL,
                pmid VARCHAR(24) NULL,
                category VARCHAR(48) NOT NULL,
                category_score DOUBLE NULL,
                category_hits TEXT NULL,
                page_count INT NULL,
                body_chars INT NULL,
                citation_mismatch TINYINT NOT NULL DEFAULT 0,
                indexed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                UNIQUE KEY uniq_literature_file (file_name),
                KEY idx_literature_category (category),
                KEY idx_literature_doi (doi(191)),
                FULLTEXT KEY ft_literature_title (title)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                f"""CREATE TABLE IF NOT EXISTS {PASSAGES_TABLE} (
                passage_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                doc_key CHAR(32) NOT NULL,
                ordinal INT NOT NULL,
                page_number INT NOT NULL,
                body TEXT NOT NULL,
                UNIQUE KEY uniq_literature_passage (doc_key, ordinal),
                KEY idx_literature_passage_doc (doc_key),
                FULLTEXT KEY ft_literature_body (body)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
        handle.commit()
    finally:
        if own:
            handle.close()


def clear_corpus(conn=None) -> int:
    """Remove every indexed document so a rebuild starts clean."""
    own = conn is None
    handle = conn or connection()
    try:
        with handle.cursor() as cursor:
            removed = cursor.execute(f"DELETE FROM {PASSAGES_TABLE}")
            cursor.execute(f"DELETE FROM {DOCUMENTS_TABLE}")
        handle.commit()
        return removed
    finally:
        if own:
            handle.close()


def store_document(record: Dict[str, Any], passages: Sequence[Dict[str, object]], conn=None) -> str:
    """Write one citation row and replace its passages."""
    own = conn is None
    handle = conn or connection()
    key = doc_key(record["file_name"])
    try:
        with handle.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {DOCUMENTS_TABLE}
                (doc_key, file_name, corpus_dir, title, authors, year, venue, doi, pmid,
                 category, category_score, category_hits, page_count, body_chars, citation_mismatch)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                 corpus_dir=VALUES(corpus_dir), title=VALUES(title), authors=VALUES(authors),
                 year=VALUES(year), venue=VALUES(venue), doi=VALUES(doi), pmid=VALUES(pmid),
                 category=VALUES(category), category_score=VALUES(category_score),
                 category_hits=VALUES(category_hits), page_count=VALUES(page_count),
                 body_chars=VALUES(body_chars), citation_mismatch=VALUES(citation_mismatch),
                 indexed_at=CURRENT_TIMESTAMP(6)""",
                (
                    key, record["file_name"], record.get("corpus_dir") or "related",
                    record["title"][:512], json.dumps(record.get("authors") or [], ensure_ascii=False),
                    str(record.get("year") or "")[:8], str(record.get("venue") or "")[:255],
                    str(record.get("doi") or "")[:320], str(record.get("pmid") or "")[:24],
                    record["category"], float(record.get("category_score") or 0.0),
                    json.dumps(record.get("category_hits") or [], ensure_ascii=False),
                    int(record.get("page_count") or 0), int(record.get("body_chars") or 0),
                    1 if record.get("citation_mismatch") else 0,
                ),
            )
            cursor.execute(f"DELETE FROM {PASSAGES_TABLE} WHERE doc_key=%s", (key,))
            if passages:
                cursor.executemany(
                    f"INSERT INTO {PASSAGES_TABLE} (doc_key, ordinal, page_number, body) VALUES (%s,%s,%s,%s)",
                    [(key, int(item["ordinal"]), int(item["page"]), str(item["text"])) for item in passages],
                )
        handle.commit()
    finally:
        if own:
            handle.close()
    return key


def set_category(file_name: str, category: str, conn=None) -> None:
    own = conn is None
    handle = conn or connection()
    try:
        with handle.cursor() as cursor:
            cursor.execute(
                f"UPDATE {DOCUMENTS_TABLE} SET category=%s, category_score=1.0, category_hits=%s WHERE file_name=%s",
                (category, json.dumps(["curated override"], ensure_ascii=False), file_name),
            )
        handle.commit()
    finally:
        if own:
            handle.close()


def list_documents(category: Optional[str] = None, conn=None) -> List[Dict[str, Any]]:
    own = conn is None
    handle = conn or connection()
    try:
        with handle.cursor(pymysql.cursors.DictCursor) as cursor:
            sql = (
                f"SELECT d.doc_key, d.file_name, d.title, d.authors, d.year, d.venue, d.doi, d.pmid, "
                f"d.category, d.category_hits, d.page_count, d.body_chars, d.citation_mismatch, d.indexed_at, "
                f"(SELECT COUNT(*) FROM {PASSAGES_TABLE} p WHERE p.doc_key=d.doc_key) AS passage_count "
                f"FROM {DOCUMENTS_TABLE} d"
            )
            params: List[Any] = []
            if category:
                sql += " WHERE d.category=%s"
                params.append(category)
            sql += " ORDER BY d.category, d.year, d.title"
            cursor.execute(sql, params)
            rows = list(cursor.fetchall())
    finally:
        if own:
            handle.close()
    for row in rows:
        try:
            row["authors"] = json.loads(row.get("authors") or "[]")
        except json.JSONDecodeError:
            row["authors"] = []
        try:
            row["category_hits"] = json.loads(row.get("category_hits") or "[]")
        except json.JSONDecodeError:
            row["category_hits"] = []
    return rows


def build_boolean_query(raw: str) -> str:
    """Keep words and quoted phrases, drop operators that break BOOLEAN MODE."""
    phrases = re.findall(r'"([^"]{3,120})"', raw or "")
    words = re.findall(r"[A-Za-z][A-Za-z0-9'’\-]{2,}", (raw or "").replace('"', " "))
    terms: List[str] = [f'"{re.sub(r"[^A-Za-z0-9\\' \\-]", "", p).strip()}"' for p in phrases if p.strip()]
    seen = {term.lower() for term in terms}
    for word in words:
        token = re.sub(r"[^A-Za-z0-9'’\-]", "", word)
        if len(token) < 3:
            continue
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(token)
    return " ".join(terms[:16])


def search_passages(query: str, *, limit: int = 12, categories: Optional[Iterable[str]] = None,
                    conn=None) -> List[Dict[str, Any]]:
    """Rank passages by FULLTEXT relevance, with title matches doubling the score."""
    boolean_query = build_boolean_query(query)
    if not boolean_query:
        return []
    own = conn is None
    handle = conn or connection()
    try:
        with handle.cursor(pymysql.cursors.DictCursor) as cursor:
            where = [f"MATCH(p.body) AGAINST (%s IN BOOLEAN MODE)"]
            params: List[Any] = [boolean_query]
            if categories:
                placeholders = ",".join(["%s"] * len(list(categories)))
                where.append(f"d.category IN ({placeholders})")
                params.extend(list(categories or []))
            cursor.execute(
                f"""SELECT p.passage_id, p.doc_key, p.page_number, p.ordinal, p.body,
                       d.file_name, d.title, d.authors, d.year, d.venue, d.doi, d.pmid, d.category,
                       MATCH(p.body) AGAINST (%s IN BOOLEAN MODE) AS relevance,
                       MATCH(d.title) AGAINST (%s IN BOOLEAN MODE) AS title_relevance
                    FROM {PASSAGES_TABLE} p
                    JOIN {DOCUMENTS_TABLE} d ON d.doc_key = p.doc_key
                    WHERE {' AND '.join(where)}
                    ORDER BY (MATCH(p.body) AGAINST (%s IN BOOLEAN MODE)
                              + 2 * MATCH(d.title) AGAINST (%s IN BOOLEAN MODE)) DESC
                    LIMIT %s""",
                [boolean_query, boolean_query, *params, boolean_query, boolean_query, int(limit)],
            )
            rows = list(cursor.fetchall())
    finally:
        if own:
            handle.close()
    for row in rows:
        try:
            row["authors"] = json.loads(row.get("authors") or "[]")
        except json.JSONDecodeError:
            row["authors"] = []
    return rows


def corpus_stamp(conn=None) -> str:
    """Identify the current index build, so a rebuilt corpus can refresh citations."""
    own = conn is None
    handle = conn or connection()
    try:
        with handle.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                f"SELECT COUNT(*) AS documents, MAX(indexed_at) AS indexed_at FROM {DOCUMENTS_TABLE}"
            )
            row = cursor.fetchone()
    finally:
        if own:
            handle.close()
    return f"{int(row.get('documents') or 0)}:{row.get('indexed_at') or ''}"


def corpus_stats(conn=None) -> Dict[str, Any]:
    own = conn is None
    handle = conn or connection()
    try:
        with handle.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(f"SELECT category, COUNT(*) AS documents FROM {DOCUMENTS_TABLE} GROUP BY category ORDER BY documents DESC")
            by_category = list(cursor.fetchall())
            cursor.execute(f"SELECT COUNT(*) AS documents FROM {DOCUMENTS_TABLE}")
            documents = int(cursor.fetchone()["documents"])
            cursor.execute(f"SELECT COUNT(*) AS passages, COALESCE(SUM(CHAR_LENGTH(body)),0) AS chars FROM {PASSAGES_TABLE}")
            passage_row = cursor.fetchone()
            cursor.execute(f"SELECT COUNT(*) AS mismatched FROM {DOCUMENTS_TABLE} WHERE citation_mismatch=1")
            mismatched = int(cursor.fetchone()["mismatched"])
            cursor.execute(f"SELECT MAX(indexed_at) AS indexed_at FROM {DOCUMENTS_TABLE}")
            indexed_at = cursor.fetchone()["indexed_at"]
    finally:
        if own:
            handle.close()
    return {
        "documents": documents,
        "passages": int(passage_row["passages"]),
        "passage_chars": int(passage_row["chars"]),
        "citation_mismatch": mismatched,
        "by_category": by_category,
        "indexed_at": str(indexed_at or ""),
    }
