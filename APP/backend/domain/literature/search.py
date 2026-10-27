"""Literature lookup used by the interpretation step and the reviewer pages.

Two sources: the locally mirrored PDFs (verifiable, already curated) and Europe
PMC for anything not on disk.  Local hits are ranked first because their page and
passage can be re-checked; public hits always carry a DOI so they stay traceable.
"""
from __future__ import annotations

import html
import json
from concurrent.futures import ThreadPoolExecutor
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import repository, topics
from .metadata import normalize_title

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
SNIPPET_CHARS = 620
# A live page cannot wait on a slow index: give up on one query rather than the run.
PUBLIC_TIMEOUT_SECONDS = 6.0
# Enough lookups to cover the flagged regions and the molecule's odd features.
MAX_LIVE_QUERIES = 6
# A local hit must cover at least this share of the query's words to be worth showing.
MIN_LIVE_COVERAGE = 0.5
# Public hits are scored on title plus abstract, which is shorter than a full passage.
MIN_LIVE_PUBLIC_COVERAGE = 0.5
# Lookups run together: mostly against the local mirror, plus a few network calls.
SEARCH_WORKERS = 6
_STOP_TOKENS = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "positions", "structure"}
# Sprinzl coordinates carry the search: "T arm base pairs 49 to 53" and "T arm
# base pairs 10 to 13" must not retrieve the same page, so bare numbers are
# tokens too.  Only one- to three-digit groups count, which is the whole range
# of Sprinzl numbering plus V-series labels.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]{2,}|(?<![A-Za-z0-9])\d{1,3}(?![A-Za-z0-9])")


def _tokens(query: str) -> List[str]:
    return [token for token in _TOKEN_RE.findall(query or "")
            if token.lower() not in _STOP_TOKENS]


def _snippet(text: str, query: str) -> str:
    """Cut a readable window around the first query term that appears."""
    body = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(body) <= SNIPPET_CHARS:
        return body
    lowered = body.lower()
    found_positions = [lowered.find(token.lower()) for token in _tokens(query)]
    anchor = min((position for position in found_positions if position >= 0), default=0)
    start = max(0, anchor - SNIPPET_CHARS // 3)
    end = min(len(body), start + SNIPPET_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end].strip()}{suffix}"


def _plain_text(value: str) -> str:
    """Europe PMC returns markup, and returns it escaped.

    The tags must be unescaped before they can be removed, and twice for the
    entries that arrive as &amp;lt;sup&amp;gt;.
    """
    text = html.unescape(html.unescape(str(value or "")))
    text = re.sub(r"<[^>]*>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _dedupe_key(citation: Dict[str, Any]) -> str:
    doi = str(citation.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    return "title:" + normalize_title(citation.get("title"))


# Nearly every document in a tRNA corpus says "tRNA", so it carries no signal about
# which part of the molecule a query is about.
_UNIVERSAL_TOKENS = {"trna", "trnas", "rna"}


def _coverage(text: str, query: str) -> float:
    """Share of the query's content words that appear in a passage."""
    wanted = {token.lower() for token in _tokens(query)}
    specific = wanted - _UNIVERSAL_TOKENS
    if specific:
        wanted = specific
    if not wanted:
        return 0.0
    present = {token.lower() for token in _TOKEN_RE.findall(text or "")}
    return len(wanted & present) / len(wanted)


def search_local(query: str, *, limit: int = 6,
                 categories: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Full-text retrieval, then a keyword-coverage re-rank.

    MySQL relevance alone over-rewards passages that repeat generic words such as
    "tRNA" or "positions", so a passage is also credited for covering as many of
    the query's content words as possible.
    """
    rows = repository.search_passages(query, limit=max(limit * 8, 40), categories=categories)
    best: Dict[str, Dict[str, Any]] = {}
    wanted = {token.lower() for token in _tokens(query)}
    ceiling = max(float(row.get("relevance") or 0.0) for row in rows) if rows else 1.0
    for row in rows:
        key = str(row["doc_key"])
        relevance = (float(row.get("relevance") or 0.0) + 2 * float(row.get("title_relevance") or 0.0))
        passage_score = _coverage(row.get("body") or "", query)
        title_score = _coverage(row.get("title") or "", query)
        score = 0.30 * (relevance / ceiling) + 0.55 * passage_score + 0.15 * title_score
        existing = best.get(key)
        if existing and existing["score"] >= score:
            continue
        best[key] = {
            "source": "local_pdf",
            "doc_key": key,
            "file_name": row.get("file_name"),
            "title": row.get("title"),
            "authors": row.get("authors") or [],
            "year": str(row.get("year") or ""),
            "venue": row.get("venue") or "",
            "doi": row.get("doi") or "",
            "category": row.get("category"),
            "page": int(row.get("page_number") or 0),
            "passage_id": int(row.get("passage_id") or 0),
            "snippet": _snippet(row.get("body") or "", query),
            "score": round(score, 4),
            "coverage": round(passage_score, 3),
            "terms_present": sorted(token for token in wanted
                                    if token in (row.get("body") or "").lower()),
            "url": f"https://doi.org/{row['doi']}" if row.get("doi") else "",
            "query": query,
        }
    return sorted(best.values(), key=lambda item: -item["score"])[:limit]


def search_public(query: str, *, limit: int = 6, timeout: float = 12.0) -> List[Dict[str, Any]]:
    """Query Europe PMC; returns nothing on any network or parse failure."""
    params = {
        "query": query,
        "format": "json",
        "pageSize": str(max(1, min(limit, 25))),
        "resultType": "core",
    }
    url = EUROPE_PMC_SEARCH + "?" + urllib.parse.urlencode(params)
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "SprinzlMap-literature/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    citations: List[Dict[str, Any]] = []
    for item in payload.get("resultList", {}).get("result", []):
        if item.get("source") not in {"MED", "PMC"}:
            continue
        authors = [author.get("fullName") or author.get("name", "")
                   for author in item.get("authorList", {}).get("author", [])][:8]
        doi = str(item.get("doi") or "")
        pmid = str(item.get("pmid") or "")
        abstract = _plain_text(str(item.get("abstractText") or ""))
        title = _plain_text(str(item.get("title") or ""))
        citations.append({
            "source": "public_index",
            "doc_key": "",
            "file_name": "",
            "title": title,
            "authors": [author for author in authors if author],
            "year": str(item.get("pubYear") or ""),
            "venue": str(item.get("journalTitle") or ""),
            "doi": doi,
            "pmid": pmid,
            "category": "public_index",
            "page": 0,
            "passage_id": 0,
            "cited_by_count": int(item.get("citedByCount") or 0),
            "open_access": bool(item.get("isOpenAccess") == "Y"),
            "snippet": _snippet(abstract or str(item.get("title") or ""), query),
            "score": _coverage(f"{title} {abstract}", query),
            "url": f"https://doi.org/{doi}" if doi else (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""),
            "query": query,
        })
    return citations[:limit]


def gather(queries: Iterable[Tuple[str, str]] | Iterable[str], *, total_limit: int = 10,
           per_query_limit: int = 4, include_public: bool = True,
           public_quota: Optional[int] = None,
           max_queries: Optional[int] = None,
           categories: Optional[Sequence[str]] = None,
           min_local_coverage: float = 0.0,
           min_public_coverage: float = 0.0) -> List[Dict[str, Any]]:
    """Run several queries and return a mix of local and public citations.

    Local PDFs get the larger share because their page and passage can be
    re-checked, but some slots stay reserved for public index hits: the local
    mirror is incomplete, and topics such as CCA-adding enzymes have no PDF.

    A live case page runs this while the reader waits, so the number of queries is
    capped and each public lookup has a short timeout; a capped list is better than
    a slow one.
    """
    normalized: List[Tuple[str, str]] = []
    for entry in queries or ():
        if isinstance(entry, tuple):
            normalized.append((entry[0], entry[1]))
        else:
            normalized.append((str(entry), ""))
    if max_queries is not None and len(normalized) > max_queries:
        normalized = normalized[:max_queries]
    public_slots = public_quota if public_quota is not None else max(1, total_limit // 3)

    # One reader waits on this while the page is open, and each query costs either a
    # MySQL lookup or a network round trip.  The lookups run together, then merge in
    # query order, so the result does not depend on which thread finished first.
    wanted_kinds: List[str] = []
    for _query, kind in normalized:
        if kind not in wanted_kinds:
            wanted_kinds.append(kind)
    wanted_kinds = wanted_kinds[: max(1, public_slots)]

    def run_local(order: int, query: str, kind: str) -> List[Dict[str, Any]]:
        wanted = tuple(categories) if categories else (
            topics.category_for_kind(kind) if kind else ()
        )
        return search_local(query, limit=per_query_limit, categories=wanted or None)

    def run_public(kind: str, fallback_query: str) -> List[Dict[str, Any]]:
        return search_public(topics.public_query(kind, fallback_query),
                             limit=public_slots * 2, timeout=PUBLIC_TIMEOUT_SECONDS)

    tasks: List[Tuple[str, Any]] = [
        ("local", (order, query, kind)) for order, (query, kind) in enumerate(normalized)
    ]
    kind_query: Dict[str, str] = {}
    for query, kind in normalized:
        kind_query.setdefault(kind, query)
    if include_public:
        tasks += [("public", kind) for kind in wanted_kinds if kind in kind_query]

    with ThreadPoolExecutor(max_workers=min(len(tasks), SEARCH_WORKERS)) as pool:
        futures = {}
        for kind_of_task, argument in tasks:
            if kind_of_task == "local":
                order, query, kind = argument
                futures[pool.submit(run_local, order, query, kind)] = ("local", order)
            else:
                futures[pool.submit(run_public, argument, kind_query[argument])] = ("public", argument)
        collected: List[Tuple[Tuple[Any, ...], str, Dict[str, Any]]] = []
        for future, (task_kind, marker) in futures.items():
            try:
                hits = future.result()
            except Exception:
                # A failed lookup only costs one topic; the rest of the list stands.
                hits = []
            for hit in hits:
                if task_kind == "local":
                    if float(hit.get("coverage") or 0.0) < min_local_coverage:
                        continue
                    collected.append(((0, -float(hit.get("score") or 0.0), marker), task_kind, hit))
                else:
                    if float(hit.get("score") or 0.0) < min_public_coverage:
                        continue
                    collected.append(
                        ((1, -float(hit.get("score") or 0.0), -int(hit.get("cited_by_count") or 0)),
                         task_kind, hit)
                    )

    local: Dict[str, Dict[str, Any]] = {}
    public: Dict[str, Dict[str, Any]] = {}
    for rank_key, task_kind, hit in sorted(collected, key=lambda item: item[0]):
        key = _dedupe_key(hit)
        bucket = local if task_kind == "local" else public
        if task_kind == "public" and key in local:
            continue
        if key in bucket:
            continue
        hit["_rank"] = rank_key
        bucket[key] = hit

    public_ranked = sorted(public.values(), key=lambda item: item["_rank"])[: min(public_slots, len(public))]
    local_ranked = sorted(local.values(), key=lambda item: item["_rank"])[: max(0, total_limit - len(public_ranked))]
    ranked = local_ranked + public_ranked
    # When the local mirror has nothing strong enough for a topic, the free slots go
    # to further public hits rather than staying empty.
    if len(ranked) < total_limit and len(public) > len(public_ranked):
        extra = [item for item in sorted(public.values(), key=lambda entry: entry["_rank"])
                 if item not in public_ranked]
        ranked += extra[: total_limit - len(ranked)]
    for position, citation in enumerate(ranked, start=1):
        citation.pop("_rank", None)
        citation["citation_id"] = f"L{position}"
    return ranked


def citations_for_evidence(evidence: Dict[str, Any], *, total_limit: int = 8,
                           include_public: bool = True,
                           max_queries: int = MAX_LIVE_QUERIES,
                           min_local_coverage: float = MIN_LIVE_COVERAGE,
                           min_public_coverage: float = MIN_LIVE_PUBLIC_COVERAGE) -> List[Dict[str, Any]]:
    """Literature for a completed run, derived from its recorded evidence.

    A local passage that shares only a few of the query's words is a paper that
    happens to mention the region, not one about it, so weak hits are dropped and
    their slots go to the public index instead.
    """
    return gather(topics.queries_for_evidence(evidence or {}),
                  total_limit=total_limit, include_public=include_public,
                  max_queries=max_queries, min_local_coverage=min_local_coverage,
                  min_public_coverage=min_public_coverage)


def format_citation_list(citations: Sequence[Dict[str, Any]]) -> str:
    """Compact text block placed in front of the interpretation model."""
    lines: List[str] = []
    for citation in citations:
        authors = ", ".join(list(citation.get("authors") or [])[:3]) or "authors unavailable"
        where = " ".join(part for part in (citation.get("venue") or "", citation.get("year") or "") if part)
        locator = ""
        if citation.get("page"):
            locator = f" p.{citation['page']}"
        identity = citation.get("doi") or citation.get("pmid") or citation.get("file_name") or ""
        lines.append(
            f"[{citation.get('citation_id')}] {authors}. {citation.get('title')} ({where})."
            f"{locator} {identity}\nExcerpt: {citation.get('snippet')}"
        )
    return "\n".join(lines)
