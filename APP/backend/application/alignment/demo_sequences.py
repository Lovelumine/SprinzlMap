# -*- coding: utf-8 -*-
"""Demo sequence helpers for alignment caching."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Set


def _normalize_seq(raw: str) -> str:
    return (
        (raw or "")
        .upper()
        .replace(" ", "")
        .replace("\n", "")
        .replace("\r", "")
        .replace("T", "U")
    )


def _extract_default_fasta(file_path: Path) -> Iterable[str]:
    """Extract sequences from a DEFAULT_FASTA block in a Vue file."""
    try:
        text = file_path.read_text()
    except OSError:
        return []
    marker = "const DEFAULT_FASTA = `"
    start = text.find(marker)
    if start == -1:
        return []
    start += len(marker)
    end = text.find("`", start)
    if end == -1:
        return []
    fasta = text[start:end]
    seqs = []
    current = []
    for line in fasta.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current:
                seqs.append(_normalize_seq("".join(current)))
                current = []
            continue
        current.append(line)
    if current:
        seqs.append(_normalize_seq("".join(current)))
    return seqs


def load_demo_sequences() -> Set[str]:
    """Load demo sequences from the front-end default FASTA blocks."""
    repo_root = Path(__file__).resolve().parents[4]
    forge_path = repo_root / "src" / "views" / "forge" / "ForgeNormal.vue"
    sprinzl_path = repo_root / "src" / "views" / "sprinzl" / "SprinzlNormal.vue"
    seqs = set()
    for path in (forge_path, sprinzl_path):
        seqs.update(_extract_default_fasta(path))
    return seqs


DEMO_SEQS = load_demo_sequences()


def is_demo_sequence(raw: str) -> bool:
    return _normalize_seq(raw) in DEMO_SEQS
