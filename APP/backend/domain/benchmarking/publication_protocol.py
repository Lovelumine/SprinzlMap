from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Mapping, Sequence

from .protocol import digest_text, normalize_sequence


PUBLICATION_SCHEMA_VERSION = "3.1"
PUBLICATION_BENCHMARK_ID = "sprinzlmap-benchmark-v3"
DEVELOPMENT_PARTITION = "retrospective_development"
EXTERNAL_TEST_PARTITION = "external_frozen_test"
EXTERNAL_STRATUM_TARGETS = {
    "canonical_numbering": 1500,
    "specialized_safety": 500,
}
REVIEW_PENDING_STATES = frozenset(
    {
        "blind_rereview_required",
        "review_required",
        "duplicate_label_conflict",
    }
)
VALID_EXTERNAL_BASES = frozenset("ACGUN")
DEVELOPMENT_FOLD_COUNT = 5
_TERMINAL_REFERENCE_BASES = frozenset("ACGUN")


def terminal_reference_category(gold_by_label: Mapping[str, Any]) -> str:
    """Classify whether reference columns 74-76 encode a CCA tail.

    Non-CCA residues in these columns are retained as an anomaly record, but
    are not a valid Sprinzl tail reference for coordinate-accuracy scoring.
    """
    def base(value: Any) -> str:
        normalized = str(value or "").strip().upper().replace("T", "U")
        return normalized if normalized in _TERMINAL_REFERENCE_BASES else "-"

    tail = [base(gold_by_label.get(label)) for label in ("74", "75", "76")]
    if tail == ["C", "C", "A"]:
        return "complete_cca"
    if tail in (["C", "-", "-"], ["C", "C", "-"]):
        return "incomplete_terminal"
    if not any(value != "-" for value in tail):
        return "no_74_76"
    return "non_cca_reference"


def development_fold(cluster_id: str, *, fold_count: int = DEVELOPMENT_FOLD_COUNT) -> int:
    if fold_count < 2:
        raise ValueError("fold_count must be at least 2")
    value = str(cluster_id or "").strip()
    if not value:
        raise ValueError("similarity cluster id is required")
    return int(
        digest_text(f"{PUBLICATION_BENCHMARK_ID}\0fold\0{value}")[:16],
        16,
    ) % fold_count + 1


def normalize_external_sequence(raw: str) -> str:
    sequence = "".join(str(raw or "").upper().replace("T", "U").split())
    invalid = set(sequence) - VALID_EXTERNAL_BASES
    if invalid:
        raise ValueError(
            f"external sequence contains invalid symbols: {''.join(sorted(invalid))}"
        )
    return sequence


def build_development_records(
    records: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    output: list[Dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_sequences: set[str] = set()
    for source in records:
        record = dict(source)
        if str(record.get("schema_version") or "") != "2.0":
            raise ValueError("development source must use submission-v2 schema 2.0")
        seqname = str(record.get("representative_seqname") or "").strip()
        sequence_digest = str(record.get("sequence_sha256") or "").strip()
        if not seqname or seqname in seen_names:
            raise ValueError(f"missing or duplicate representative seqname: {seqname}")
        if not sequence_digest or sequence_digest in seen_sequences:
            raise ValueError(f"missing or duplicate sequence digest: {sequence_digest}")
        if bool(record.get("template_exact_overlap")):
            raise ValueError(f"exact template overlap escaped development filter: {seqname}")

        curation_state = str(record.get("curation_state") or "").strip()
        expert_audited = bool(record.get("primary_reference_ready"))
        if expert_audited != (curation_state == "reviewed_reference"):
            raise ValueError(f"inconsistent curation state for {seqname}")
        if not expert_audited and curation_state not in REVIEW_PENDING_STATES:
            raise ValueError(f"unsupported review state for {seqname}: {curation_state}")

        label_conflict = bool(record.get("gold_label_conflict")) or (
            curation_state == "duplicate_label_conflict"
        )
        reference_evidence_tier = (
            "project_expert_audited"
            if expert_audited
            else "trnadb_database_reference"
        )

        legacy_split = str(record.pop("retrospective_split", "") or "")
        cluster_id = str(dict(record.get("similarity_cluster") or {}).get("cluster_id") or "")
        record.update(
            {
                "schema_version": PUBLICATION_SCHEMA_VERSION,
                "benchmark_id": PUBLICATION_BENCHMARK_ID,
                "benchmark_partition": DEVELOPMENT_PARTITION,
                "legacy_retrospective_split": legacy_split,
                "development_fold": development_fold(cluster_id),
                "evaluation_role": "database_reference_development",
                "gold_evaluation_allowed": True,
                "reference_evidence_tier": reference_evidence_tier,
                "reference_label_policy": (
                    "frozen_representative_record_with_source_conflict"
                    if label_conflict
                    else "frozen_trnadb_record"
                ),
                "project_expert_audit_available": expert_audited,
                "expert_sensitivity_eligible": expert_audited,
                "source_label_conflict": label_conflict,
                "review_required": not expert_audited,
            }
        )
        output.append(record)
        seen_names.add(seqname)
        seen_sequences.add(sequence_digest)
    return sorted(output, key=lambda item: str(item["sequence_sha256"]))


def summarize_development_records(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    curation_states = Counter(str(row.get("curation_state") or "") for row in records)
    scored = sum(bool(row.get("gold_evaluation_allowed")) for row in records)
    evidence_tiers = Counter(
        str(row.get("reference_evidence_tier") or "") for row in records
    )
    return {
        "case_count": len(records),
        "scored_case_count": scored,
        "expert_audited_case_count": sum(
            bool(row.get("project_expert_audit_available")) for row in records
        ),
        "database_reference_only_case_count": sum(
            not bool(row.get("project_expert_audit_available")) for row in records
        ),
        "source_label_conflict_case_count": sum(
            bool(row.get("source_label_conflict")) for row in records
        ),
        "blind_review_case_count": sum(bool(row.get("review_required")) for row in records),
        "reference_evidence_tiers": dict(sorted(evidence_tiers.items())),
        "curation_states": dict(sorted(curation_states.items())),
        "partition_counts": dict(
            sorted(Counter(str(row.get("benchmark_partition") or "") for row in records).items())
        ),
    }


def blind_review_task_id(sequence_sha256: str, reviewer: str) -> str:
    reviewer_name = str(reviewer or "").strip().upper()
    if reviewer_name not in {"A", "B"}:
        raise ValueError("reviewer must be A or B")
    return digest_text(
        f"{PUBLICATION_BENCHMARK_ID}\0{reviewer_name}\0{sequence_sha256}"
    )[:20]


def build_blind_review_tasks(
    records: Sequence[Mapping[str, Any]],
    sequences_by_seqname: Mapping[str, str],
    *,
    reviewer: str,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    tasks: list[Dict[str, Any]] = []
    key_rows: list[Dict[str, Any]] = []
    for record in records:
        if not bool(record.get("review_required")):
            continue
        seqname = str(record.get("representative_seqname") or "")
        sequence_digest = str(record.get("sequence_sha256") or "")
        sequence = normalize_sequence(sequences_by_seqname.get(seqname, ""))
        if not sequence or digest_text(sequence) != sequence_digest:
            raise ValueError(f"review sequence mismatch for {seqname}")
        task_id = blind_review_task_id(sequence_digest, reviewer)
        metadata = dict(record.get("metadata") or {})
        tasks.append(
            {
                "reviewer_task_id": task_id,
                "sequence": sequence,
                "sequence_length": len(sequence),
                "broad_source": str(metadata.get("broad_source") or ""),
                "amino_acid": str(metadata.get("amino_acid") or ""),
                "anticodon": str(metadata.get("anticodon") or ""),
                "organism": str(metadata.get("organism") or ""),
            }
        )
        key_rows.append(
            {
                "reviewer_task_id": task_id,
                "reviewer": str(reviewer).strip().upper(),
                "sequence_sha256": sequence_digest,
                "representative_seqname": seqname,
                "review_reason": str(record.get("curation_state") or ""),
            }
        )
    tasks.sort(
        key=lambda item: digest_text(
            f"{PUBLICATION_BENCHMARK_ID}\0{reviewer}\0order\0{item['reviewer_task_id']}"
        )
    )
    key_rows.sort(key=lambda item: str(item["reviewer_task_id"]))
    return tasks, key_rows


def normalize_external_candidates(
    rows: Sequence[Mapping[str, Any]],
    *,
    development_sequence_digests: set[str],
    template_sequence_digests: set[str],
    template_identity_audit: Mapping[str, Mapping[str, Any]],
    expected_targets: Mapping[str, int] = EXTERNAL_STRATUM_TARGETS,
) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_sequences: set[str] = set()
    strata = Counter()
    for raw in rows:
        external_id = str(raw.get("external_id") or "").strip()
        sequence = normalize_external_sequence(str(raw.get("sequence") or ""))
        source_database = str(raw.get("source_database") or "").strip()
        source_release = str(raw.get("source_release") or "").strip()
        source_accession = str(raw.get("source_accession") or "").strip()
        stratum = str(raw.get("test_stratum") or "").strip()
        if not external_id or external_id in seen_ids:
            raise ValueError(f"missing or duplicate external_id: {external_id}")
        if not sequence or set(sequence) - VALID_EXTERNAL_BASES:
            raise ValueError(f"invalid external sequence: {external_id}")
        if not 40 <= len(sequence) <= 200:
            raise ValueError(f"external sequence length out of range: {external_id}")
        sequence_digest = digest_text(sequence)
        if sequence_digest in seen_sequences:
            raise ValueError(f"duplicate external sequence: {external_id}")
        if sequence_digest in development_sequence_digests:
            raise ValueError(f"external sequence overlaps development: {external_id}")
        if sequence_digest in template_sequence_digests:
            raise ValueError(f"external sequence overlaps runtime template: {external_id}")
        identity_row = dict(template_identity_audit.get(sequence_digest) or {})
        if not identity_row:
            raise ValueError(f"external sequence lacks template identity audit: {external_id}")
        nearest_identity = float(identity_row.get("nearest_template_identity", -1.0))
        if not 0.0 <= nearest_identity < 1.0:
            raise ValueError(
                f"invalid or exact nearest-template identity for {external_id}: "
                f"{nearest_identity}"
            )
        audit_method = str(
            identity_row.get("identity_metric") or identity_row.get("metric") or ""
        ).strip()
        if not audit_method:
            raise ValueError(f"external identity audit method is missing: {external_id}")
        if stratum not in expected_targets:
            raise ValueError(f"unsupported external test stratum: {stratum}")
        if not source_database or not source_release or not source_accession:
            raise ValueError(f"external provenance is incomplete: {external_id}")
        records.append(
            {
                "schema_version": PUBLICATION_SCHEMA_VERSION,
                "benchmark_id": PUBLICATION_BENCHMARK_ID,
                "benchmark_partition": EXTERNAL_TEST_PARTITION,
                "external_id": external_id,
                "sequence": sequence,
                "sequence_sha256": sequence_digest,
                "sequence_length": len(sequence),
                "test_stratum": stratum,
                "source_database": source_database,
                "source_release": source_release,
                "source_accession": source_accession,
                "nearest_template_identity": nearest_identity,
                "nearest_template_identity_status": "audited",
                "nearest_template_identity_metric": audit_method,
                "broad_source": str(raw.get("broad_source") or "").strip(),
                "amino_acid": str(raw.get("amino_acid") or "").strip(),
                "anticodon": str(raw.get("anticodon") or "").strip(),
                "organism": str(raw.get("organism") or "").strip(),
                "gold_evaluation_allowed": False,
                "gold_status": "sealed",
            }
        )
        seen_ids.add(external_id)
        seen_sequences.add(sequence_digest)
        strata[stratum] += 1

    expected = {str(key): int(value) for key, value in expected_targets.items()}
    if dict(strata) != expected:
        raise ValueError(
            f"external stratum counts do not match frozen targets: "
            f"actual={dict(sorted(strata.items()))} expected={dict(sorted(expected.items()))}"
        )
    return sorted(records, key=lambda item: str(item["external_id"]))


__all__ = [
    "DEVELOPMENT_PARTITION",
    "DEVELOPMENT_FOLD_COUNT",
    "EXTERNAL_STRATUM_TARGETS",
    "EXTERNAL_TEST_PARTITION",
    "PUBLICATION_BENCHMARK_ID",
    "PUBLICATION_SCHEMA_VERSION",
    "blind_review_task_id",
    "build_blind_review_tasks",
    "build_development_records",
    "development_fold",
    "normalize_external_candidates",
    "normalize_external_sequence",
    "summarize_development_records",
    "terminal_reference_category",
]
