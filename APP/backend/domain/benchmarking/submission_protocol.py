from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import edlib

from .protocol import digest_text, normalize_sequence


SUBMISSION_SCHEMA_VERSION = "2.0"
SUBMISSION_SPLITS = ("development", "calibration", "internal_test")
SUBMISSION_SPLIT_RATIOS = (0.6, 0.2, 0.2)
CURATION_PRIORITY = {
    "manual_corrected": 0,
    "accepted_gold": 1,
    "accepted_model": 2,
    "no_manual_correction": 3,
}


@dataclass(frozen=True)
class GoldLabelRecord:
    seqname: str
    sequence: str
    sequence_sha256: str
    label_sha256: str


@dataclass(frozen=True)
class TemplateIdentityMatch:
    identity: float
    edit_distance: int
    template_sequence_sha256: str
    template_length: int


class _UnionFind:
    def __init__(self, values: Sequence[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        smaller, larger = sorted((root_left, root_right))
        self.parent[larger] = smaller


def load_gold_label_records(path: Path) -> Dict[str, GoldLabelRecord]:
    records: Dict[str, GoldLabelRecord] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames or fieldnames[0] != "seqname":
            raise ValueError(f"Unexpected gold header: {path}")
        labels = fieldnames[1:]
        for row in reader:
            seqname = str(row.get("seqname") or "").strip()
            if not seqname:
                continue
            label_values = [str(row.get(label) or "").strip() for label in labels]
            sequence = "".join(normalize_sequence(value) for value in label_values)
            records[seqname] = GoldLabelRecord(
                seqname=seqname,
                sequence=sequence,
                sequence_sha256=digest_text(sequence),
                label_sha256=hashlib.sha256(
                    "\0".join(label_values).encode("utf-8")
                ).hexdigest(),
            )
    return records


def global_identity(left: str, right: str, *, threshold: float | None = None) -> float:
    denominator = max(len(left), len(right), 1)
    max_distance = -1
    if threshold is not None:
        max_distance = max(0, int(math.floor((1.0 - float(threshold)) * denominator + 1e-9)))
        if abs(len(left) - len(right)) > max_distance:
            return 0.0
    result = edlib.align(left, right, mode="NW", task="distance", k=max_distance)
    distance = int(result.get("editDistance") or 0)
    if distance < 0:
        return 0.0
    return max(0.0, 1.0 - distance / denominator)


def load_unique_template_sequences(path: Path) -> Dict[str, str]:
    sequences: Dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if not header or header[0] != "seqname":
            raise ValueError(f"Unexpected template header: {path}")
        for row in reader:
            sequence = "".join(normalize_sequence(value) for value in row[1:])
            if sequence:
                sequences.setdefault(digest_text(sequence), sequence)
    return sequences


def group_templates_by_length(
    template_sequences: Mapping[str, str],
) -> Dict[int, tuple[tuple[str, str], ...]]:
    grouped: Dict[int, list[tuple[str, str]]] = defaultdict(list)
    for digest, sequence in template_sequences.items():
        grouped[len(sequence)].append((str(digest), str(sequence)))
    return {
        length: tuple(sorted(items))
        for length, items in sorted(grouped.items())
    }


def nearest_template_match(
    sequence: str,
    templates_by_length: Mapping[int, Sequence[tuple[str, str]]],
) -> TemplateIdentityMatch:
    query = normalize_sequence(sequence)
    if not query:
        raise ValueError("query sequence is empty")
    if not templates_by_length:
        raise ValueError("template sequence collection is empty")

    query_length = len(query)
    ranked_lengths = sorted(
        templates_by_length,
        key=lambda length: (
            -min(query_length, int(length)) / max(query_length, int(length), 1),
            abs(query_length - int(length)),
            int(length),
        ),
    )
    best: TemplateIdentityMatch | None = None
    for template_length in ranked_lengths:
        denominator = max(query_length, int(template_length), 1)
        length_upper_bound = min(query_length, int(template_length)) / denominator
        if best is not None and length_upper_bound + 1e-12 < best.identity:
            continue
        max_distance = -1
        if best is not None:
            max_distance = max(
                0,
                int(math.floor((1.0 - best.identity) * denominator + 1e-9)),
            )
        for template_digest, template_sequence in templates_by_length[template_length]:
            result = edlib.align(
                query,
                template_sequence,
                mode="NW",
                task="distance",
                k=max_distance,
            )
            distance = int(result.get("editDistance") or 0)
            if distance < 0:
                continue
            identity = max(0.0, 1.0 - distance / denominator)
            candidate = TemplateIdentityMatch(
                identity=identity,
                edit_distance=distance,
                template_sequence_sha256=template_digest,
                template_length=int(template_length),
            )
            if best is None or candidate.identity > best.identity + 1e-12 or (
                abs(candidate.identity - best.identity) <= 1e-12
                and (
                    candidate.edit_distance,
                    candidate.template_sequence_sha256,
                )
                < (best.edit_distance, best.template_sequence_sha256)
            ):
                best = candidate
                max_distance = max(
                    0,
                    int(math.floor((1.0 - best.identity) * denominator + 1e-9)),
                )
    if best is None:
        raise RuntimeError("nearest-template audit produced no candidate")
    return best


def load_template_identity_audit(path: Path) -> Dict[str, Dict[str, Any]]:
    audit: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid identity audit JSON at {path}:{line_number}") from exc
            digest = str(payload.get("sequence_sha256") or "")
            if not digest or digest in audit:
                raise ValueError(f"Missing or duplicate sequence digest at {path}:{line_number}")
            audit[digest] = dict(payload)
    return audit


def apply_template_identity_audit(
    records: Sequence[Mapping[str, Any]],
    audit: Mapping[str, Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    expected = {str(record.get("sequence_sha256") or "") for record in records}
    if set(audit) != expected:
        missing = len(expected - set(audit))
        extra = len(set(audit) - expected)
        raise ValueError(f"Identity audit coverage mismatch: missing={missing}, extra={extra}")
    merged: list[Dict[str, Any]] = []
    for source in records:
        record = dict(source)
        digest = str(record.get("sequence_sha256") or "")
        result = dict(audit[digest])
        identity = float(result.get("nearest_template_identity"))
        if not 0.0 <= identity <= 1.0:
            raise ValueError(f"Invalid nearest-template identity for {digest}")
        if identity >= 1.0 and not bool(record.get("template_exact_overlap")):
            raise ValueError(
                f"Exact template match escaped the digest overlap filter for {digest}"
            )
        record["nearest_template_identity"] = identity
        record["nearest_template_identity_status"] = "audited"
        record["nearest_template_match"] = {
            "metric": "one_minus_global_levenshtein_over_max_length",
            "edit_distance": int(result.get("edit_distance")),
            "template_length": int(result.get("template_length")),
            "template_sequence_sha256": str(
                result.get("template_sequence_sha256") or ""
            ),
        }
        merged.append(record)
    return merged


def cluster_sequences(
    sequences: Mapping[str, str],
    *,
    identity_threshold: float = 0.95,
) -> Dict[str, str]:
    if not 0.0 < identity_threshold <= 1.0:
        raise ValueError("identity_threshold must be in (0, 1]")
    digests = sorted(sequences)
    groups_by_length: Dict[int, list[str]] = defaultdict(list)
    for digest in digests:
        groups_by_length[len(sequences[digest])].append(digest)
    union_find = _UnionFind(digests)
    lengths = sorted(groups_by_length)
    for left_index, left_length in enumerate(lengths):
        for right_length in lengths[left_index:]:
            denominator = max(left_length, right_length, 1)
            max_distance = int(math.floor((1.0 - identity_threshold) * denominator + 1e-9))
            if right_length - left_length > max_distance:
                break
            left_items = groups_by_length[left_length]
            right_items = groups_by_length[right_length]
            for left_pos, left_digest in enumerate(left_items):
                start = left_pos + 1 if left_length == right_length else 0
                for right_digest in right_items[start:]:
                    if global_identity(
                        sequences[left_digest],
                        sequences[right_digest],
                        threshold=identity_threshold,
                    ) >= identity_threshold:
                        union_find.union(left_digest, right_digest)
    components: Dict[str, list[str]] = defaultdict(list)
    for digest in digests:
        components[union_find.find(digest)].append(digest)
    cluster_ids: Dict[str, str] = {}
    for members in components.values():
        cluster_id = min(members)
        for digest in members:
            cluster_ids[digest] = cluster_id
    return cluster_ids


def assign_submission_split(
    cluster_id: str,
    *,
    source: str,
    seed: str,
    ratios: Sequence[float] = SUBMISSION_SPLIT_RATIOS,
) -> str:
    if len(ratios) != 3 or abs(sum(float(value) for value in ratios) - 1.0) > 1e-9:
        raise ValueError("submission split ratios must contain three values summing to one")
    # A similarity component is indivisible. Including record-level metadata in
    # this hash would let members of one component cross evaluation roles.
    _ = source
    bucket = int(digest_text(f"{seed}\0{cluster_id}")[:16], 16) / float(16**16)
    if bucket < float(ratios[0]):
        return "development"
    if bucket < float(ratios[0]) + float(ratios[1]):
        return "calibration"
    return "internal_test"


def build_submission_records(
    case_records: Sequence[Mapping[str, Any]],
    gold_records: Mapping[str, GoldLabelRecord],
    *,
    seed: str,
    identity_threshold: float = 0.95,
) -> list[Dict[str, Any]]:
    by_sequence: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    sequences: Dict[str, str] = {}
    for case in case_records:
        if not bool(case.get("eligible")) or bool(case.get("template_exact_overlap")):
            continue
        seqname = str(case.get("seqname") or "")
        gold = gold_records.get(seqname)
        if gold is None:
            continue
        digest = str(case.get("sequence_sha256") or gold.sequence_sha256)
        by_sequence[digest].append(case)
        sequences[digest] = gold.sequence

    cluster_ids = cluster_sequences(
        sequences,
        identity_threshold=identity_threshold,
    )
    cluster_sizes = Counter(cluster_ids.values())
    records: list[Dict[str, Any]] = []
    for digest, cases in sorted(by_sequence.items()):
        ranked = sorted(
            cases,
            key=lambda item: (
                CURATION_PRIORITY.get(str(item.get("correction_decision") or ""), 99),
                str(item.get("seqname") or ""),
            ),
        )
        representative = ranked[0]
        seqnames = sorted(str(item.get("seqname") or "") for item in cases)
        labels = {
            gold_records[name].label_sha256
            for name in seqnames
            if name in gold_records
        }
        decisions = Counter(str(item.get("correction_decision") or "") for item in cases)
        representative_decision = str(representative.get("correction_decision") or "")
        if len(labels) > 1:
            curation_state = "duplicate_label_conflict"
        elif representative_decision == "accepted_model":
            curation_state = "blind_rereview_required"
        elif representative_decision == "no_manual_correction":
            curation_state = "review_required"
        else:
            curation_state = "reviewed_reference"
        metadata = dict(representative.get("metadata") or {})
        cluster_id = cluster_ids[digest]
        records.append(
            {
                "schema_version": SUBMISSION_SCHEMA_VERSION,
                "sequence_sha256": digest,
                "sequence_length": len(sequences[digest]),
                "representative_seqname": str(representative.get("seqname") or ""),
                "source_record_count": len(cases),
                "source_seqnames": seqnames,
                "gold_label_sha256": next(iter(labels)) if len(labels) == 1 else "",
                "gold_label_conflict": len(labels) > 1,
                "correction_decisions": dict(sorted(decisions.items())),
                "curation_state": curation_state,
                "primary_reference_ready": curation_state == "reviewed_reference",
                "template_exact_overlap": False,
                "nearest_template_identity": None,
                "nearest_template_identity_status": "pending_audit",
                "similarity_cluster": {
                    "threshold": identity_threshold,
                    "cluster_id": cluster_id,
                    "cluster_size": int(cluster_sizes[cluster_id]),
                },
                "retrospective_split": assign_submission_split(
                    cluster_id,
                    source=str(metadata.get("broad_source") or "unknown"),
                    seed=seed,
                ),
                "metadata": metadata,
            }
        )
    return records


def summarize_submission_records(
    case_records: Sequence[Mapping[str, Any]],
    submission_records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    eligible = [record for record in case_records if bool(record.get("eligible"))]
    overlap = [record for record in eligible if bool(record.get("template_exact_overlap"))]
    nonoverlap = [record for record in eligible if not bool(record.get("template_exact_overlap"))]
    audited = [
        record
        for record in submission_records
        if str(record.get("nearest_template_identity_status") or "") == "audited"
    ]
    identity_bins = Counter()
    for record in audited:
        identity = float(record.get("nearest_template_identity") or 0.0)
        if identity >= 0.95:
            identity_bins["0.95_to_below_1.00"] += 1
        elif identity >= 0.90:
            identity_bins["0.90_to_below_0.95"] += 1
        else:
            identity_bins["below_0.90"] += 1
    return {
        "source_rows": len(eligible),
        "exact_template_overlap_rows": len(overlap),
        "nonidentical_template_rows": len(nonoverlap),
        "nonidentical_template_unique_sequences": len(submission_records),
        "curation_states": dict(
            Counter(str(record.get("curation_state") or "") for record in submission_records)
        ),
        "retrospective_splits": dict(
            Counter(str(record.get("retrospective_split") or "") for record in submission_records)
        ),
        "similarity_cluster_count": len(
            {
                str(dict(record.get("similarity_cluster") or {}).get("cluster_id") or "")
                for record in submission_records
            }
        ),
        "gold_label_conflict_sequences": sum(
            bool(record.get("gold_label_conflict")) for record in submission_records
        ),
        "nearest_template_identity_status": (
            "audited" if len(audited) == len(submission_records) else "pending_audit"
        ),
        "nearest_template_identity_bins": dict(identity_bins),
        "evidence_role": "retrospective_internal_benchmark",
    }


__all__ = [
    "GoldLabelRecord",
    "TemplateIdentityMatch",
    "SUBMISSION_SCHEMA_VERSION",
    "SUBMISSION_SPLITS",
    "apply_template_identity_audit",
    "assign_submission_split",
    "build_submission_records",
    "cluster_sequences",
    "global_identity",
    "group_templates_by_length",
    "load_gold_label_records",
    "load_template_identity_audit",
    "load_unique_template_sequences",
    "nearest_template_match",
    "summarize_submission_records",
]
