"""Turn the local PDF mirror into searchable passages.

Text extraction happens on demand and the derived text lives in MySQL; the
repository keeps only the PDFs, one curated category file, and this code.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .metadata import MANUAL_DIR, PAPER_DIR, ROOT, normalize_title

CATEGORY_FILE = ROOT / "docs" / "reference" / "papers" / "categories.json"

CATEGORIES: Sequence[str] = (
    "coordinate_standard",
    "database_resource",
    "detection_tool",
    "structure_alignment_method",
    "evaluation_benchmark",
    "atypical_trna",
    "modification_function_disease",
    "engineered_trna",
    "agent_method",
    "ai_context",
    "manual",
    "unclassified",
)

# Reviewer-facing names.  The keys stay English because they are also the query
# filter values a reviewer can pass to the API.
CATEGORY_LABELS: Dict[str, Dict[str, str]] = {
    "coordinate_standard": {"en": "Numbering standard", "zh-CN": "编号标准"},
    "database_resource": {"en": "Database and resource", "zh-CN": "数据库与资源"},
    "detection_tool": {"en": "Gene detection", "zh-CN": "基因检测工具"},
    "structure_alignment_method": {"en": "RNA alignment method", "zh-CN": "RNA 比对方法"},
    "evaluation_benchmark": {"en": "Method evaluation", "zh-CN": "方法评测"},
    "atypical_trna": {"en": "Atypical and mitochondrial tRNA", "zh-CN": "非典型与线粒体 tRNA"},
    "modification_function_disease": {"en": "Modification, function and disease", "zh-CN": "修饰、功能与疾病"},
    "engineered_trna": {"en": "Engineered and suppressor tRNA", "zh-CN": "工程与抑制 tRNA"},
    "agent_method": {"en": "Agent method", "zh-CN": "智能体方法"},
    "ai_context": {"en": "AI context", "zh-CN": "人工智能背景"},
    "manual": {"en": "Software manual", "zh-CN": "软件手册"},
    "unclassified": {"en": "Unclassified", "zh-CN": "未分类"},
}

_CATEGORY_KEYWORDS: Dict[str, Sequence[str]] = {
    "coordinate_standard": (
        "sprinzl", "standard numbering", "naming tRNA-derived", "ontology",
        "tertiary structure of yeast", "three-dimensional structure", "l shape",
        "compilation of tRNA sequences", "crw", "secondary structure maps",
    ),
    "database_resource": (
        "database", "trnadb", "gtrnadb", "trnadb-ce", "rnacentral", "rfam",
        "modomics", "mamit-trna", "mitotip", "mitos", "encyclopedia",
    ),
    "detection_tool": (
        "trnascan-se", "infernal", "covariance model", "aragorn", "arwen",
        "viennarna", "r2dt", "annotation of tRNA genes",
        "identification of", "detect tRNA genes",
    ),
    "structure_alignment_method": (
        "structural alignment", "sequence alignment", "alignment of rna",
        "rna align", "comparative rna structure", "multiple sequence",
        "modules with discriminative", "secondary structure prediction",
    ),
    "evaluation_benchmark": (
        "benchmark", "evaluation of", "casp", "comparison of",
        "continuous benchmarking",
    ),
    "atypical_trna": (
        "mitochondrial trna", "d-armless", "d arm", "missing d", "elongator",
        "selenocysteine", "noncanon", "non-canon", "hypermodification",
    ),
    "modification_function_disease": (
        "modification", "pseudouridine", "methylation", "disease", "cancer",
        "aminoacylation", "identity", "decoding", "frameshifting",
        "translation", "abundance", "nanopore",
    ),
    "engineered_trna": (
        "suppressor trna", "nonsense", "orthogonal", "genetic code expansion",
        "pyrrolysyl", "aav", "therapeut", "engineering", "amber",
    ),
    "agent_method": (
        "react", "reflexion", "language agent", "llm agent", "agentic",
        "self-verification", "geneagent", "large language model agent",
    ),
    "ai_context": (
        "artificial intelligence", "machine learning", "foundation model",
        "large model", "deep learning", "alphafold", "snakemake",
        "data-centric",
    ),
}

_WEIGHTS = {
    "coordinate_standard": 1.6,
    "atypical_trna": 1.35,
    "engineered_trna": 1.3,
    "detection_tool": 1.2,
    "database_resource": 1.15,
    "agent_method": 1.2,
    "modification_function_disease": 1.0,
    "structure_alignment_method": 1.0,
    "evaluation_benchmark": 0.85,
    "ai_context": 0.7,
}


# Three mirrors carry no machine-readable title on page one, so their names sit
# in the table until the text is read.  Titles here are taken from the first page.
TITLE_OVERRIDES: Dict[str, str] = {
    "cannone_2002_crw_site.pdf": (
        "The Comparative RNA Web (CRW) Site: an online database of comparative "
        "sequence and structure information for ribosomal, intron, and other RNAs"
    ),
    "petrov_2014_rrna_secondary_structures.pdf": (
        "Secondary Structures of rRNAs from All Three Domains of Life"
    ),
    "anger_2013_human_drosophila_80s_nature.pdf": (
        "Structures of the human and Drosophila 80S ribosome"
    ),
}


def title_override(path: Path) -> str:
    return TITLE_OVERRIDES.get(str(path.name).lower(), "")


_YEAR_IN_NAME_RE = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")


def year_from_file_name(path: Path) -> str:
    """Mirrors are usually named Author_YYYY_topic, which beats a stray digit."""
    match = _YEAR_IN_NAME_RE.search(str(path.name))
    return match.group(1) if match else ""


def pdf_paths() -> List[Path]:
    found: List[Path] = []
    for folder in (PAPER_DIR, MANUAL_DIR):
        if folder.exists():
            found.extend(sorted(p for p in folder.glob("*.pdf") if p.is_file()))
    return found


def read_pdf_text(path: Path, timeout: int = 120) -> tuple:
    """Return (whole text, per-page text) using poppler's pdftotext."""
    if shutil.which("pdftotext") is None:
        raise RuntimeError("pdftotext is missing; install poppler-utils to index papers")
    completed = subprocess.run(
        ["pdftotext", "-q", str(path), "-"],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    raw = completed.stdout.decode("utf-8", errors="replace")
    pages = [page.strip() for page in raw.split("\f")]
    return "\n".join(pages), pages


def file_stem_title(path: Path) -> str:
    """Readable title taken from the mirrored file name."""
    stem = re.sub(r"\.pdf$", "", path.name, flags=re.I)
    return re.sub(r"^\d+\.", "", stem).strip()


def head_lines(pages: Sequence[str], count: int = 3) -> str:
    """Text of the first pages, used to confirm a PDF matches its citation."""
    return "\n".join(pages[:count])


def split_passages(pages: Sequence[str], *, min_chars: int = 240, max_chars: int = 1600) -> List[Dict[str, object]]:
    """Merge short paragraphs page by page into blocks sized for citation."""
    passages: List[Dict[str, object]] = []
    for page_number, page in enumerate(pages, start=1):
        for chunk in re.split(r"\n\s*\n+", page):
            text = re.sub(r"\s+", " ", chunk).strip()
            if not text or len(text) < 80 or looks_like_references(text):
                continue
            if len(text) < min_chars and passages and passages[-1]["page"] == page_number:
                if len(str(passages[-1]["text"])) + len(text) <= max_chars:
                    passages[-1]["text"] = f"{passages[-1]['text']} {text}"
                    continue
            while len(text) > max_chars:
                window = text[:max_chars]
                cut = max(window.rfind(". "), window.rfind("; "), window.rfind(".\n"))
                if cut < 200:
                    cut = max_chars - 1
                piece = text[: cut + 1].strip()
                passages.append({"page": page_number, "text": piece})
                text = text[len(piece):].strip()
            if text:
                passages.append({"page": page_number, "text": text})
    for ordinal, passage in enumerate(passages, start=1):
        passage["ordinal"] = ordinal
    return passages


def looks_like_references(text: str) -> bool:
    """True for bibliography blocks, which retrieve well but explain nothing."""
    entries = len(re.findall(r"(?:^|\s)\d{1,3}\.\s+[A-Z][A-Za-z'’\-]+,?\s+[A-Z]\.", text))
    years = len(re.findall(r"\((?:1[89]\d{2}|20\d{2})[a-z]?\)", text))
    if entries >= 3 or (entries >= 1 and years >= 3):
        return True
    return text.count("et al") >= 2 and years >= 4


def classify(title: str, body: str, path: Path) -> tuple:
    """Score a document against the category keyword tables."""
    overrides = load_category_overrides()
    if path.name in overrides:
        return overrides[path.name], 1.0, ["curated override"]
    if MANUAL_DIR in path.parents:
        return "manual", 1.0, ["manuals directory"]
    haystack_title = normalize_title(title) + " " + normalize_title(path.stem)
    haystack_body = normalize_title(body[:12000])
    scores: Dict[str, tuple] = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        hits: List[str] = []
        value = 0.0
        for keyword in keywords:
            token = normalize_title(keyword)
            if not token:
                continue
            if token in haystack_title:
                value += 3.0
                hits.append(keyword)
            elif token in haystack_body:
                value += 1.0
                hits.append(keyword)
        if value:
            scores[category] = (value * _WEIGHTS.get(category, 1.0), hits[:6])
    if not scores:
        return "unclassified", 0.0, []
    best_category, (best_score, hits) = max(scores.items(), key=lambda item: item[1][0])
    return best_category, round(best_score, 2), hits


def load_category_overrides(path: Path = CATEGORY_FILE) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in (data or {}).items() if value in CATEGORIES}


def corpus_dir(path: Path) -> str:
    return "manuals" if MANUAL_DIR in path.parents else "related"


def mirror_matches_citation(head_text: str, cited_title: Optional[str]) -> bool:
    """False when the downloaded PDF does not contain the cited article."""
    if not cited_title:
        return True
    from .metadata import token_coverage
    return token_coverage(cited_title, head_text[:6000]) >= 0.7
