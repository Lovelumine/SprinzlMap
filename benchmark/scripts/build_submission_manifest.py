#!/usr/bin/env python3
"""Build the nonidentical-template, unique-sequence submission manifest."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.benchmarking.protocol import (  # noqa: E402
    atomic_write_json,
    atomic_write_jsonl,
    build_case_records,
    load_export_metadata,
    load_gold_records,
    load_latest_corrections,
    load_template_sequence_digests,
    sha256_file,
)
from backend.domain.benchmarking.submission_protocol import (  # noqa: E402
    SUBMISSION_SPLITS,
    apply_template_identity_audit,
    build_submission_records,
    load_gold_label_records,
    load_template_identity_audit,
    summarize_submission_records,
)


DEFAULT_SEED = "sprinzlmap-submission-v2"


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
        default=Path("benchmark/manifests/submission_v2"),
    )
    parser.add_argument("--identity-threshold", type=float, default=0.95)
    parser.add_argument(
        "--template-identity-audit",
        type=Path,
        default=Path(
            "benchmark/manifests/submission_v2/template_identity_audit.jsonl"
        ),
    )
    parser.add_argument("--seed", default=DEFAULT_SEED)
    return parser.parse_args()


def relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def write_review_queue(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "representative_seqname",
                "sequence_sha256",
                "curation_state",
                "source_record_count",
                "broad_source",
                "organism",
            ]
        )
        for record in records:
            if bool(record.get("primary_reference_ready")):
                continue
            metadata = dict(record.get("metadata") or {})
            writer.writerow(
                [
                    record.get("representative_seqname"),
                    record.get("sequence_sha256"),
                    record.get("curation_state"),
                    record.get("source_record_count"),
                    metadata.get("broad_source"),
                    metadata.get("organism"),
                ]
            )


def main() -> None:
    args = parse_args()
    gold = load_gold_records(args.gold)
    gold_labels = load_gold_label_records(args.gold)
    metadata = load_export_metadata(args.export)
    corrections = load_latest_corrections(args.corrections)
    template_digests = load_template_sequence_digests(args.templates)
    case_records = build_case_records(
        gold,
        metadata,
        corrections,
        template_digests,
        seed=str(args.seed),
    )
    records = build_submission_records(
        case_records,
        gold_labels,
        seed=str(args.seed),
        identity_threshold=float(args.identity_threshold),
    )
    identity_audit_applied = args.template_identity_audit.exists()
    if identity_audit_applied:
        records = apply_template_identity_audit(
            records,
            load_template_identity_audit(args.template_identity_audit),
        )
    cluster_splits: dict[str, set[str]] = {}
    for record in records:
        cluster_id = str(dict(record.get("similarity_cluster") or {}).get("cluster_id") or "")
        cluster_splits.setdefault(cluster_id, set()).add(
            str(record.get("retrospective_split") or "")
        )
    crossing = [cluster_id for cluster_id, splits in cluster_splits.items() if len(splits) > 1]
    if crossing:
        raise RuntimeError(
            f"Similarity clusters crossed evaluation roles: {len(crossing)}"
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    cases_path = args.out_dir / "cases.jsonl"
    review_path = args.out_dir / "review_queue.tsv"
    atomic_write_jsonl(cases_path, records)
    write_review_queue(review_path, records)

    split_outputs = {}
    primary_split_outputs = {}
    for split in SUBMISSION_SPLITS:
        path = args.out_dir / f"{split}.seqnames.txt"
        names = sorted(
            str(record.get("representative_seqname") or "")
            for record in records
            if str(record.get("retrospective_split") or "") == split
        )
        path.write_text("".join(f"{name}\n" for name in names), encoding="utf-8")
        split_outputs[split] = {
            "path": relative(path),
            "sha256": sha256_file(path),
            "unique_sequence_count": len(names),
        }
        primary_path = args.out_dir / f"primary_{split}.seqnames.txt"
        primary_names = sorted(
            str(record.get("representative_seqname") or "")
            for record in records
            if str(record.get("retrospective_split") or "") == split
            and bool(record.get("primary_reference_ready"))
        )
        primary_path.write_text(
            "".join(f"{name}\n" for name in primary_names),
            encoding="utf-8",
        )
        primary_split_outputs[split] = {
            "path": relative(primary_path),
            "sha256": sha256_file(primary_path),
            "unique_sequence_count": len(primary_names),
        }

    protocol_path = args.out_dir / "protocol.json"
    inputs = {
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
    }
    if identity_audit_applied:
        inputs["template_identity_audit"] = {
            "path": relative(args.template_identity_audit),
            "sha256": sha256_file(args.template_identity_audit),
        }
    limitations = [
        "the current cohort is retrospective because earlier system versions evaluated the source records",
        "accepted_model records require blinded re-review before primary gold use",
        "records without a manual decision remain in the review queue",
        "the 95 percent clusters prevent near-identical sequences from crossing retrospective roles",
        "a prospective external cohort is still required for the final generalization claim",
    ]
    if not identity_audit_applied:
        limitations.insert(
            1,
            "nearest-template identity beyond exact overlap remains pending until the dedicated audit is complete",
        )
    payload = {
        "schema_version": "2.0",
        "seed": str(args.seed),
        "identity_cluster_threshold": float(args.identity_threshold),
        "primary_object": "nonidentical-template unique normalized sequence",
        "inputs": inputs,
        "outputs": {
            "cases": {"path": relative(cases_path), "sha256": sha256_file(cases_path)},
            "review_queue": {
                "path": relative(review_path),
                "sha256": sha256_file(review_path),
            },
            "split_lists": split_outputs,
            "primary_split_lists": primary_split_outputs,
        },
        "summary": summarize_submission_records(case_records, records),
        "limitations": limitations,
    }
    atomic_write_json(protocol_path, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {cases_path}")
    print(f"Wrote {review_path}")
    print(f"Wrote {protocol_path}")


if __name__ == "__main__":
    main()
