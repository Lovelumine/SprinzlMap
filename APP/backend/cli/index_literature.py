"""Build the literature corpus tables from the local PDF mirror.

Run from the repository root with ``PYTHONPATH=APP``:

    python3 -m backend.cli.index_literature --preview
    python3 -m backend.cli.index_literature --write-categories
    python3 -m backend.cli.index_literature --rebuild
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.domain.literature import corpus, metadata, repository  # noqa: E402


def collect(want_passages: bool) -> tuple[list, list]:
    records = metadata.load_curated_records()
    paths = corpus.pdf_paths()
    curated = metadata.match_pdfs(records, paths)
    documents: list[dict] = []
    for path in paths:
        started = time.time()
        body, pages = corpus.read_pdf_text(path)
        head = corpus.head_lines(pages)
        cited = curated.get(path.name)
        title = metadata.clean_title(
            corpus.title_override(path) or (cited or {}).get("title")
            or corpus.file_stem_title(path)
        )
        category, score, hits = corpus.classify(title, body, path)
        mismatch = bool(cited) and not corpus.mirror_matches_citation(head, cited.get("title"))
        year = str((cited or {}).get("year") or metadata.guess_year(body) or "")
        if not year.isdigit() or not 1800 <= int(year) <= 2100:
            year = corpus.year_from_file_name(path) or year
        passages = corpus.split_passages(pages) if want_passages else []
        documents.append({
            "file_name": path.name,
            "corpus_dir": corpus.corpus_dir(path),
            "title": title,
            "authors": (cited or {}).get("authors") or [],
            "year": year,
            "venue": (cited or {}).get("journal") or (cited or {}).get("publisher") or "",
            "doi": (cited or {}).get("doi") or metadata.guess_doi(body),
            "pmid": "",
            "category": category,
            "category_score": score,
            "category_hits": hits,
            "page_count": len(pages),
            "body_chars": len(body),
            "citation_mismatch": mismatch,
            "curated": bool(cited),
            "match_score": (cited or {}).get("match_score"),
            "passages": passages,
            "seconds": round(time.time() - started, 2),
        })
    matched_titles = {item["title"] for item in documents if item["curated"]}
    unused = [record for record in records if record.get("title") not in matched_titles]
    return documents, unused


def main() -> int:
    parser = argparse.ArgumentParser(description="Index the local tRNA paper mirror into MySQL.")
    parser.add_argument("--preview", action="store_true", help="Read PDFs and report, without writing.")
    parser.add_argument("--rebuild", action="store_true", help="Delete existing rows before indexing.")
    parser.add_argument("--write-categories", action="store_true",
                        help="Dump the auto-assigned categories to docs/reference/papers/categories.json")
    parser.add_argument("--limit", type=int, default=0, help="Index only the first N PDFs (smoke test).")
    parser.add_argument("--verbose", action="store_true", help="Print one line per document.")
    args = parser.parse_args()

    documents, unused = collect(want_passages=not args.write_categories)
    if args.limit:
        documents = documents[: args.limit]

    if args.write_categories:
        path = corpus.CATEGORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {item["file_name"]: item["category"] for item in documents}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {len(payload)} categories to {path}")
        return 0

    counts: dict[str, int] = {}
    for item in documents:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
        if args.verbose or args.preview:
            flags = []
            if not item["curated"]:
                flags.append("no-citation-record")
            if item["citation_mismatch"]:
                flags.append("MIRROR-TITLE-MISMATCH")
            print(f"{item['category']:<26} {(item['match_score'] or 0):<5} "
                  f"{item['file_name'][:64]:<64} {' '.join(flags)}")

    print(f"\ndocuments={len(documents)} curated={sum(1 for d in documents if d['curated'])} "
          f"mismatched={sum(1 for d in documents if d['citation_mismatch'])}")
    for category, count in sorted(counts.items(), key=lambda pair: -pair[1]):
        print(f"  {category:<31} {count}")
    if unused:
        print(f"\ncitation records without a PDF: {len(unused)}")
        for record in unused[:20]:
            print(f"  {record.get('year','')}  {str(record.get('title',''))[:90]}")

    if args.preview:
        return 0

    repository.ensure_schema()
    if args.rebuild:
        removed = repository.clear_corpus()
        print(f"cleared {removed} passages before rebuild")
    written = 0
    for item in documents:
        repository.store_document(item, item["passages"])
        written += 1
    stats = repository.corpus_stats()
    print(f"indexed {written} documents, {stats['passages']} passages, {stats['passage_chars']} characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
