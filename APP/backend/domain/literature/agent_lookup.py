"""Reference notes the Agent can ask for while a case is still open.

The Agent never reads a paper and never edits a numbering through this path.  It
asks one question about the region it is stuck on and receives a short, cited
note, which the next planning round can weigh against the alignment evidence.

Lookups are memoised per process on the query set, so a 2530-case batch does not
repeat the same Europe PMC call thousands of times, and a benchmark run sees one
answer per question rather than one that depends on how fast the index replied.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import search as literature_search
from . import topics

# A note has to be small enough that adding it to the prompt is cheaper than the
# decision it improves.
DEFAULT_NOTES = 4
MAX_NOTES = 8
NOTE_EXCERPT_CHARS = 220

_REGION_ALIASES: Dict[str, Tuple[str, ...]] = {
    # ``failure_regions`` can report the whole arm without saying which end.
    "Aminoacyl arm": ("Aminoacyl arm 5' end", "Aminoacyl arm 3' end"),
}

_LOCK = threading.Lock()
_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _display_regions(values: Iterable[Any]) -> List[str]:
    regions: List[str] = []
    for value in values or ():
        name = str(value or "").strip()
        if not name:
            continue
        for item in _REGION_ALIASES.get(name, (name,)):
            if item not in regions:
                regions.append(item)
    return regions


def evidence_from_observation(
    observation: Mapping[str, Any],
    *,
    extra_regions: Sequence[str] = (),
) -> Dict[str, Any]:
    """Turn the Agent's own observation into the evidence shape ``topics`` reads."""
    context = dict(observation.get("context") or {})
    contract = dict(observation.get("input_contract") or {})
    scientific = dict(observation.get("scientific_evidence") or {})

    regions = _display_regions(list(context.get("failure_regions") or []))
    regions += _display_regions(list(extra_regions or []))
    for reason in list(context.get("validation_reasons") or ()):
        code = str(reason or "")
        if "cca" in code or code.startswith("-1"):
            regions += _display_regions(["Aminoacyl arm 3' end" if "cca" in code else "Aminoacyl arm 5' end"])
        elif "variable" in code or code.startswith("V"):
            regions += _display_regions(["Variable loop"])
        elif "anticodon" in code:
            regions += _display_regions(["Anticodon loop + Anticodon stem"])
    regions = [item for index, item in enumerate(regions) if item not in regions[:index]]

    posterior: Optional[float] = None
    try:
        posterior = float(scientific.get("cm_average_posterior"))
    except (TypeError, ValueError):
        posterior = None

    cca_status = str(contract.get("cca_status") or "").strip().lower()
    return {
        # Region keys are already translated for the query tables.
        "conflict_regions": topics.keys_for_display_regions(regions[:3]),
        "domain": str(contract.get("domain_hint") or context.get("domain_hint") or ""),
        "trna_class": str(contract.get("trna_class") or ""),
        "intron_status": str(contract.get("intron_status") or context.get("intron_status") or ""),
        "terminal_cca": "missing_cca" if cca_status in {"absent", "missing", "none"} else cca_status,
        # The same posterior band used by the reviewer pages: the 2530-case batch
        # spans 0.888 to 1.000, and cases below 0.95 are where template homology
        # stops being a reliable guide.
        "template_similarity": "medium" if (posterior is not None and posterior < 0.95) else "high",
        "note": str(context.get("segmentation_status") or ""),
    }


def cache_key(queries: Sequence[Tuple[str, str]], *, include_public: bool, limit: int) -> str:
    payload = json.dumps(
        {"queries": [list(item) for item in queries], "public": bool(include_public), "limit": int(limit)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def gather_note(
    evidence: Mapping[str, Any],
    *,
    include_public: bool = True,
    limit: int = DEFAULT_NOTES,
) -> Dict[str, Any]:
    """One bounded, cited answer to the Agent's question."""
    queries = topics.queries_for_evidence(dict(evidence or {}))
    limit = max(1, min(int(limit), MAX_NOTES))
    key = cache_key(queries, include_public=include_public, limit=limit)
    with _LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        citations = [dict(item) for item in cached]
        cache_state = "hit"
    else:
        citations = literature_search.gather(
            queries,
            total_limit=limit,
            per_query_limit=2,
            include_public=include_public,
            max_queries=literature_search.MAX_LIVE_QUERIES,
            min_local_coverage=literature_search.MIN_LIVE_COVERAGE,
            min_public_coverage=literature_search.MIN_LIVE_PUBLIC_COVERAGE,
        )
        with _LOCK:
            _CACHE[key] = [dict(item) for item in citations]
        cache_state = "miss"
    return {
        "queries": [query for query, _kind in queries],
        "citations": citations,
        "cache": cache_state,
    }


def as_prompt_notes(citations: Sequence[Mapping[str, Any]], *, limit: int = DEFAULT_NOTES) -> List[Dict[str, str]]:
    """The compact form that goes into the Agent's next observation."""
    notes: List[Dict[str, str]] = []
    for citation in list(citations or [])[:limit]:
        excerpt = " ".join(str(citation.get("snippet") or "").split())
        if len(excerpt) > NOTE_EXCERPT_CHARS:
            excerpt = excerpt[:NOTE_EXCERPT_CHARS].rsplit(" ", 1)[0] + " ..."
        notes.append(
            {
                "title": " ".join(str(citation.get("title") or "").split())[:180],
                "year": str(citation.get("year") or ""),
                "venue": str(citation.get("venue") or "")[:80],
                "source": str(citation.get("source") or ""),
                "excerpt": excerpt,
            }
        )
    return notes


def citation_ids(citations: Sequence[Mapping[str, Any]]) -> List[str]:
    """Stable identifiers for provenance: local mirror or DOI/PMID, never a title."""
    identifiers: List[str] = []
    for citation in citations or ():
        doi = str(citation.get("doi") or "").strip()
        pmid = str(citation.get("pmid") or "").strip()
        doc_key = str(citation.get("doc_key") or "").strip()
        if doi:
            identifiers.append(f"doi:{doi}")
        elif pmid:
            identifiers.append(f"pmid:{pmid}")
        elif doc_key:
            identifiers.append(f"local:{doc_key}")
    return identifiers


def cached_lookup_count() -> int:
    with _LOCK:
        return len(_CACHE)


def reset_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = [
    "DEFAULT_NOTES",
    "MAX_NOTES",
    "NOTE_EXCERPT_CHARS",
    "as_prompt_notes",
    "cache_key",
    "cached_lookup_count",
    "citation_ids",
    "evidence_from_observation",
    "gather_note",
    "reset_cache",
]
