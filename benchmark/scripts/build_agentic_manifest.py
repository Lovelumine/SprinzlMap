#!/usr/bin/env python3
"""Build frozen, leakage-audited case manifests for agentic numbering experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.benchmarking.protocol import (  # noqa: E402
    DEFAULT_SEED,
    DEFAULT_SPLIT_RATIOS,
    PROTOCOL_GROUP_FIELDS,
    SPLIT_NAMES,
    atomic_write_json,
    atomic_write_jsonl,
    build_case_records,
    load_export_metadata,
    load_gold_records,
    load_latest_corrections,
    load_template_sequence_digests,
    sha256_file,
    summarize_case_records,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold",
        type=Path,
        default=PROJECT_ROOT / ".runtime" / "gold" / "mature_benchmark.curated.tsv",
    )
    parser.add_argument("--export", type=Path, default=PROJECT_ROOT / ".runtime" / "gold" / "export.tsv")
    parser.add_argument(
        "--corrections",
        type=Path,
        default=PROJECT_ROOT / ".runtime" / "gold" / "manual_corrections.jsonl",
    )
    parser.add_argument("--templates", type=Path, default=Path("data/tRNAs_flat.tsv"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("benchmark/manifests/agentic_v1"),
    )
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--train-ratio", type=float, default=DEFAULT_SPLIT_RATIOS[0])
    parser.add_argument("--calibration-ratio", type=float, default=DEFAULT_SPLIT_RATIOS[1])
    parser.add_argument("--test-ratio", type=float, default=DEFAULT_SPLIT_RATIOS[2])
    return parser.parse_args()


def relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def write_seqname_lists(out_dir: Path, records: list[dict]) -> dict[str, dict[str, dict[str, str]]]:
    outputs: dict[str, dict[str, dict[str, str]]] = {}
    lists_dir = out_dir / "seqnames"
    lists_dir.mkdir(parents=True, exist_ok=True)
    for protocol in PROTOCOL_GROUP_FIELDS:
        outputs[protocol] = {}
        for split in SPLIT_NAMES:
            names = sorted(
                str(record["seqname"])
                for record in records
                if bool(record.get("eligible"))
                and str(dict(record.get("splits") or {}).get(protocol) or "") == split
            )
            path = lists_dir / f"{protocol}.{split}.txt"
            path.write_text("".join(f"{name}\n" for name in names), encoding="utf-8")
            outputs[protocol][split] = {
                "path": relative(path),
                "sha256": sha256_file(path),
            }
    return outputs


def main() -> None:
    args = parse_args()
    ratios = (args.train_ratio, args.calibration_ratio, args.test_ratio)
    gold_records = load_gold_records(args.gold)
    metadata = load_export_metadata(args.export)
    corrections = load_latest_corrections(args.corrections)
    template_digests = load_template_sequence_digests(args.templates)
    records = build_case_records(
        gold_records,
        metadata,
        corrections,
        template_digests,
        seed=str(args.seed),
        ratios=ratios,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    cases_path = args.out_dir / "cases.jsonl"
    atomic_write_jsonl(cases_path, records)
    seqname_lists = write_seqname_lists(args.out_dir, records)
    gold_names = {record.seqname for record in gold_records}
    manual_excluded = sorted(
        seqname
        for seqname, correction in corrections.items()
        if str(correction.get("status") or correction.get("decision") or "").strip() == "excluded"
    )
    protocol_path = args.out_dir / "protocol.json"
    payload = {
        "schema_version": "1.0",
        "seed": str(args.seed),
        "split_ratios": dict(zip(SPLIT_NAMES, ratios)),
        "protocol_group_fields": dict(PROTOCOL_GROUP_FIELDS),
        "inputs": {
            "gold": {"path": relative(args.gold), "sha256": sha256_file(args.gold)},
            "export": {"path": relative(args.export), "sha256": sha256_file(args.export)},
            "corrections": {
                "path": relative(args.corrections),
                "sha256": sha256_file(args.corrections),
            },
            "templates": {
                "path": relative(args.templates),
                "sha256": sha256_file(args.templates),
            },
        },
        "outputs": {
            "cases": {"path": relative(cases_path), "sha256": sha256_file(cases_path)},
            "seqname_lists": seqname_lists,
        },
        "summary": summarize_case_records(records),
        "exclusion_audit": {
            "manual_excluded_count": len(manual_excluded),
            "manual_excluded_present_in_gold": sum(name in gold_names for name in manual_excluded),
            "manual_excluded_absent_from_gold": sum(name not in gold_names for name in manual_excluded),
            "manual_excluded_seqnames": manual_excluded,
        },
        "limitations": [
            "isotype_holdout groups by broad biological source and amino-acid isotype",
            "organism_holdout is not a taxonomic clade holdout",
            "a clade holdout requires a versioned taxonomy source not present in export.tsv",
            "template_exact_overlap marks exact sequence visibility; runtime template exclusion is separate",
        ],
    }
    atomic_write_json(protocol_path, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {cases_path}")
    print(f"Wrote {protocol_path}")


if __name__ == "__main__":
    main()
