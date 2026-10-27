"""Reading bibliographic records for the local paper mirror.

The numbered PDFs under ``docs/reference/papers`` are the corpus, but their file
names are not a reliable citation.  ``docs/paper/zotero_import.ris`` and
``research/paper/refs.bib`` carry the curated title, year, journal and DOI, so a
PDF is matched against those records first and only falls back to what the file
name and first page say when no record covers it.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[4]
PAPER_DIR = ROOT / "docs" / "reference" / "papers" / "related"
MANUAL_DIR = ROOT / "docs" / "reference" / "papers" / "manuals"
RIS_PATH = ROOT / "docs" / "paper" / "zotero_import.ris"
BIB_PATH = ROOT / "research" / "paper" / "refs.bib"

_STOPWORDS = {
    "a", "an", "and", "for", "in", "of", "on", "the", "to", "with", "via", "as",
    "is", "are", "at", "by", "from", "new", "based", "using",
}


_TEX_REPLACEMENTS = {
    "\\AA": "Å",
    "\\aa": "å",
    "\\ss": "ß",
    "\\&": "&",
    "\\'": "'",
    "\\`": "'",
    '\\"': '"',
    "\\/": "/",
}
_TEX_WORD_RE = re.compile(r"\\([A-Za-z]+)")


def clean_title(value: str) -> str:
    """Titles arrive from RIS files and PDF text, and both carry TeX leftovers."""
    text = str(value or "")
    for token, replacement in _TEX_REPLACEMENTS.items():
        text = text.replace(token, replacement)
    # \alpha3 -> alpha3: the command name is the readable fallback.
    text = _TEX_WORD_RE.sub(lambda match: match.group(1), text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(value: object) -> str:
    """Lowercase, strip accents and punctuation, keep word order."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def title_tokens(value: object) -> List[str]:
    return [token for token in normalize_title(value).split() if token not in _STOPWORDS]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_ris(path: Path = RIS_PATH) -> List[Dict[str, object]]:
    """Parse the Zotero export into citation records."""
    if not path.exists():
        return []
    field_map = {
        "TY": "type", "AU": "authors", "TI": "title", "JO": "journal",
        "T2": "journal", "PY": "year", "VL": "volume", "IS": "issue",
        "SP": "first_page", "EP": "last_page", "DO": "doi", "UR": "url",
        "PB": "publisher", "ET": "edition", "DA": "date",
    }
    records: List[Dict[str, object]] = []
    for block in re.split(r"\n\s*ER\s*-\s*\n", path.read_text(encoding="utf-8", errors="replace")):
        if "TY  -" not in block:
            continue
        raw: Dict[str, List[str]] = {}
        for line in block.splitlines():
            match = re.match(r"^([A-Z][A-Z0-9])\s+-\s?(.*)$", line)
            if not match:
                continue
            key = field_map.get(match.group(1))
            if not key:
                continue
            value = _clean(match.group(2))
            if value:
                raw.setdefault(key, []).append(value)
        records.append(_build_record(raw, path.name))
    return records


def parse_bib(path: Path = BIB_PATH) -> List[Dict[str, object]]:
    """Parse the BibTeX file used by the manuscript."""
    if not path.exists():
        return []
    records: List[Dict[str, object]] = []
    for entry in re.findall(r"^@\w+\s*\{(.*?)^\}", path.read_text(encoding="utf-8", errors="replace"), re.M | re.S):
        raw: Dict[str, List[str]] = {}
        for key, value in re.findall(r"^\s*([A-Za-z]+)\s*=\s*[{\"]?(.*?)[}\"]?,?\s*$", entry, re.M | re.S):
            value = _clean(re.sub(r"[{}]", "", value))
            if not value:
                continue
            bucket = {"title": "title", "author": "authors", "journal": "journal",
                      "year": "year", "doi": "doi", "url": "url", "booktitle": "publisher"}.get(key.lower())
            if bucket:
                raw.setdefault(bucket, []).append(value)
        record = _build_record(raw, path.name)
        if record["title"]:
            records.append(record)
    return records


def _build_record(raw: Dict[str, List[str]], source: str) -> Dict[str, object]:
    # BibTeX writes a whole author list into one field separated by " and ".
    authors: list[str] = []
    for item in raw.get("authors", []):
        for part in re.split(r"\s+and\s+", _clean(item)):
            if part:
                authors.append(part)
    year = ""
    for candidate in raw.get("year", []) + raw.get("date", []):
        match = re.search(r"(1[89]\d{2}|20\d{2})", candidate)
        if match:
            year = match.group(1)
            break
    doi = ""
    for candidate in raw.get("doi", []) + raw.get("url", []):
        match = re.search(r"10\.\d{4,9}/\S+", candidate)
        if match:
            doi = match.group(0).rstrip(".,;)")
            break
    return {
        "type": (raw.get("type") or ["JOUR"])[0].upper(),
        "title": _clean((raw.get("title") or [""])[0]).rstrip("."),
        "authors": authors,
        "year": year,
        "journal": _clean((raw.get("journal") or [""])[0]),
        "publisher": _clean((raw.get("publisher") or [""])[0]),
        "volume": _clean((raw.get("volume") or [""])[0]),
        "first_page": _clean((raw.get("first_page") or [""])[0]),
        "doi": doi,
        "url": _clean((raw.get("url") or [""])[0]),
        "metadata_source": source,
    }


def load_curated_records() -> List[Dict[str, object]]:
    """Merge RIS and BibTeX records, preferring the richer RIS entry."""
    merged: Dict[str, Dict[str, object]] = {}
    for record in parse_ris() + parse_bib():
        key = record["doi"] or normalize_title(record["title"])
        if not key:
            continue
        existing = merged.get(key)
        if existing is None or (not existing.get("authors") and record.get("authors")):
            merged[key] = record
    return [record for record in merged.values() if record.get("title")]


def title_similarity(left: str, right: str) -> float:
    """Dice coefficient over title tokens.

    Scaling by the longer title keeps a short file stem such as "Compilation of
    tRNA sequences" from being matched to the 2009 tRNAdb paper, which contains
    those same three words inside a much longer title.
    """
    a, b = set(title_tokens(left)), set(title_tokens(right))
    if not a or not b:
        return 0.0
    return (2 * len(a & b)) / (len(a) + len(b))


def token_coverage(phrase: str, text: str) -> float:
    """Share of a title's meaningful tokens that appear somewhere in the text.

    Used to confirm that a downloaded PDF really is the article its citation
    names, which a first-line title guess cannot do on its own.
    """
    wanted = set(title_tokens(phrase))
    if not wanted:
        return 1.0
    present = set(title_tokens(text))
    return len(wanted & present) / len(wanted)


def match_pdfs(records: List[Dict[str, object]], pdf_paths: List[Path],
               threshold: float = 0.55, min_shared_tokens: int = 3) -> Dict[str, Dict[str, object]]:
    """Assign curated records to PDF files by title similarity.

    Returns a mapping from PDF name to record.  A PDF may stay unmatched, and a
    record may be unused; the caller reports both so a bad mirror is visible.
    """
    assigned: Dict[str, Dict[str, object]] = {}
    used: set[str] = set()
    for path in pdf_paths:
        stem = re.sub(r"\.pdf$", "", path.name, flags=re.I)
        stem = re.sub(r"^\d+\.", "", stem)
        best: Optional[tuple[float, int, Dict[str, object]]] = None
        for index, record in enumerate(records):
            if index in used:
                continue
            score = title_similarity(stem, record["title"])
            shared = len(set(title_tokens(stem)) & set(title_tokens(record["title"])))
            if score >= threshold and shared >= min_shared_tokens and (best is None or score > best[0]):
                best = (score, index, record)
        if best:
            used.add(best[1])
            assigned[path.name] = {**best[2], "match_score": round(best[0], 3)}
    return assigned


def guess_year(text: str) -> str:
    """Pull a publication year out of the first page of a PDF."""
    head = text[:4000]
    patterns = (
        r"Published online\s+\d{1,2}\s+\w+\s+(1[89]\d{2}|20\d{2})",
        r"received[^\n]{0,80}?(1[89]\d{2}|20\d{2})",
        r"\b(1[89]\d{2}|20\d{2})\s*[,;]?\s*Vol\.",
        r"doi:\s*10\.\d{4,9}/[a-z]+(\d{4})",
        r"\b(1[89]\d{2}|20\d{2})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, head, re.I)
        if not match:
            continue
        candidate = match.group(1)
        # nature12104 is a DOI suffix, not a year: keep looking until a value in
        # the plausible publication range appears.
        if 1800 <= int(candidate) <= 2100:
            return candidate
    return ""


def guess_doi(text: str) -> str:
    match = re.search(r"10\.\d{4,9}/[^\s\"'<>()]+", text[:6000])
    return match.group(0).rstrip(".,;)") if match else ""
