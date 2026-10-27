from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "1.0"
DEFAULT_SEED = "llm-trnaalign-agentic-v1"
DEFAULT_SPLIT_RATIOS = (0.6, 0.2, 0.2)
SPLIT_NAMES = ("train", "calibration", "test")
PROTOCOL_GROUP_FIELDS = {
    "unique_sequence": "sequence_sha256",
    "isotype_holdout": "isotype_key",
    "organism_holdout": "organism_key",
}
VALID_BASES = frozenset("ACGUN")
GAP_BASES = frozenset({"", "-", ".", "_", "~"})


@dataclass(frozen=True)
class GoldRecord:
    seqname: str
    sequence: str

    @property
    def sequence_sha256(self) -> str:
        return digest_text(self.sequence)


def digest_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_sequence(raw: str) -> str:
    return "".join(
        base
        for base in str(raw or "").upper().replace("T", "U")
        if base in VALID_BASES
    )


def normalize_key(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def make_seqname(row: Mapping[str, str]) -> str:
    trna_id = str(row.get("tRNA ID") or "").strip()
    sprinzl_id = str(row.get("SprinzlID") or "").strip()
    row_id = str(row.get("ID") or "").strip()
    if trna_id and sprinzl_id:
        return f"{trna_id}|{sprinzl_id}"
    return trna_id or sprinzl_id or row_id


def load_gold_records(path: Path) -> list[GoldRecord]:
    records: list[GoldRecord] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames or fieldnames[0] != "seqname":
            raise ValueError(f"Unexpected gold header: {path}")
        numbering = fieldnames[1:]
        for row in reader:
            seqname = str(row.get("seqname") or "").strip()
            if not seqname or seqname in seen:
                continue
            sequence = "".join(normalize_sequence(row.get(label, "")) for label in numbering)
            if not sequence:
                continue
            seen.add(seqname)
            records.append(GoldRecord(seqname=seqname, sequence=sequence))
    return records


def load_export_metadata(path: Path) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            seqname = make_seqname(row)
            if not seqname:
                continue
            metadata[seqname] = {
                "database": str(row.get("Database") or "").strip(),
                "kingdom": str(row.get("Kingdom") or "").strip(),
                "organism": str(row.get("Organism") or "").strip(),
                "amino_acid": str(row.get("Amino Acid") or "").strip(),
                "anticodon": normalize_sequence(str(row.get("Anticodon") or "")),
            }
    return metadata


def load_latest_corrections(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid correction JSON at {path}:{line_number}") from exc
            seqname = str(payload.get("seqname") or "").strip()
            if seqname:
                latest[seqname] = dict(payload)
    return latest


def load_template_sequence_digests(path: Path) -> set[str]:
    digests: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if not header or header[0] != "seqname":
            raise ValueError(f"Unexpected template header: {path}")
        for row in reader:
            sequence = "".join(normalize_sequence(value) for value in row[1:])
            if sequence:
                digests.add(digest_text(sequence))
    return digests


def broad_source(kingdom: str) -> str:
    value = str(kingdom or "").strip()
    if value in {"Archaea", "ARCHAE"}:
        return "archaea"
    if value in {"Bacteria", "EUBACT"}:
        return "bacteria"
    if value == "Eukaryota" or value.startswith("CY "):
        return "cytosolic_eukaryote"
    if value == "CHLORO":
        return "chloroplast"
    if value.startswith("MI "):
        return "mitochondrial"
    if value == "CYANEL":
        return "cyanelle"
    if value == "VIRUS":
        return "virus"
    return "unknown"


def correction_decision(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return "no_manual_correction"
    return str(payload.get("status") or payload.get("decision") or "unknown").strip() or "unknown"


def gold_source_for_decision(decision: str) -> str:
    if decision in {"accepted_model", "manual_corrected"}:
        return "curated_labels"
    if decision == "excluded":
        return "excluded"
    return "source_labels"


def validate_split_ratios(ratios: Sequence[float]) -> tuple[float, float, float]:
    if len(ratios) != 3:
        raise ValueError("Expected train/calibration/test ratios")
    values = tuple(float(value) for value in ratios)
    if any(value < 0 for value in values) or abs(sum(values) - 1.0) > 1e-9:
        raise ValueError(f"Split ratios must be non-negative and sum to 1: {values}")
    return values  # type: ignore[return-value]


def assign_split(
    group_key: str,
    *,
    protocol: str,
    seed: str = DEFAULT_SEED,
    ratios: Sequence[float] = DEFAULT_SPLIT_RATIOS,
) -> str:
    train_ratio, calibration_ratio, _ = validate_split_ratios(ratios)
    payload = f"{seed}\0{protocol}\0{group_key}"
    bucket = int(digest_text(payload)[:16], 16) / float(16**16)
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + calibration_ratio:
        return "calibration"
    return "test"


def build_case_records(
    gold_records: Sequence[GoldRecord],
    metadata: Mapping[str, Mapping[str, str]],
    corrections: Mapping[str, Mapping[str, Any]],
    template_sequence_digests: set[str],
    *,
    seed: str = DEFAULT_SEED,
    ratios: Sequence[float] = DEFAULT_SPLIT_RATIOS,
) -> list[dict[str, Any]]:
    validate_split_ratios(ratios)
    duplicate_sizes = Counter(record.sequence_sha256 for record in gold_records)
    records: list[dict[str, Any]] = []

    for gold in gold_records:
        meta = dict(metadata.get(gold.seqname) or {})
        decision = correction_decision(corrections.get(gold.seqname))
        eligible = decision != "excluded"
        source = broad_source(str(meta.get("kingdom") or ""))
        amino_acid = normalize_key(str(meta.get("amino_acid") or ""))
        organism = normalize_key(str(meta.get("organism") or ""))
        isotype_key = f"{source}|{amino_acid}" if amino_acid else f"missing|{gold.sequence_sha256}"
        organism_key = f"{source}|{organism}" if organism else f"missing|{gold.sequence_sha256}"
        groups = {
            "sequence_sha256": gold.sequence_sha256,
            "isotype_key": isotype_key,
            "organism_key": organism_key,
        }
        splits = {
            protocol: (
                assign_split(groups[group_field], protocol=protocol, seed=seed, ratios=ratios)
                if eligible
                else "excluded"
            )
            for protocol, group_field in PROTOCOL_GROUP_FIELDS.items()
        }
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "seqname": gold.seqname,
                "sequence_sha256": gold.sequence_sha256,
                "sequence_length": len(gold.sequence),
                "duplicate_group_size": int(duplicate_sizes[gold.sequence_sha256]),
                "eligible": eligible,
                "exclusion_reason": "manual_decision_excluded" if not eligible else "",
                "correction_decision": decision,
                "gold_source": gold_source_for_decision(decision),
                "template_exact_overlap": gold.sequence_sha256 in template_sequence_digests,
                "metadata": {
                    "database": str(meta.get("database") or ""),
                    "kingdom": str(meta.get("kingdom") or ""),
                    "broad_source": source,
                    "organism": str(meta.get("organism") or ""),
                    "amino_acid": str(meta.get("amino_acid") or ""),
                    "anticodon": str(meta.get("anticodon") or ""),
                },
                "groups": groups,
                "splits": splits,
            }
        )
    return records


def leakage_checks(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for protocol, group_field in PROTOCOL_GROUP_FIELDS.items():
        group_splits: dict[str, set[str]] = defaultdict(set)
        for record in records:
            if not bool(record.get("eligible")):
                continue
            groups = dict(record.get("groups") or {})
            splits = dict(record.get("splits") or {})
            group_splits[str(groups.get(group_field) or "")].add(str(splits.get(protocol) or ""))
        leaked = sorted(group for group, values in group_splits.items() if len(values) > 1)
        checks[protocol] = {
            "group_field": group_field,
            "group_count": len(group_splits),
            "cross_split_group_count": len(leaked),
            "cross_split_group_examples": leaked[:10],
        }
    return checks


def summarize_case_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [record for record in records if bool(record.get("eligible"))]
    correction_counts = Counter(str(record.get("correction_decision") or "") for record in records)
    source_counts = Counter(
        str(dict(record.get("metadata") or {}).get("broad_source") or "unknown") for record in eligible
    )
    split_counts = {
        protocol: dict(
            Counter(str(dict(record.get("splits") or {}).get(protocol) or "") for record in eligible)
        )
        for protocol in PROTOCOL_GROUP_FIELDS
    }
    unique_sequences = {str(record.get("sequence_sha256") or "") for record in eligible}
    overlap_rows = sum(bool(record.get("template_exact_overlap")) for record in eligible)
    overlap_sequences = {
        str(record.get("sequence_sha256") or "")
        for record in eligible
        if bool(record.get("template_exact_overlap"))
    }
    return {
        "all_rows": len(records),
        "eligible_rows": len(eligible),
        "excluded_rows": len(records) - len(eligible),
        "eligible_unique_sequences": len(unique_sequences),
        "eligible_rows_in_duplicate_groups": sum(
            int(record.get("duplicate_group_size") or 0) > 1 for record in eligible
        ),
        "template_exact_overlap_rows": overlap_rows,
        "template_exact_overlap_unique_sequences": len(overlap_sequences),
        "correction_decisions": dict(correction_counts.most_common()),
        "broad_sources": dict(source_counts.most_common()),
        "split_counts": split_counts,
        "leakage_checks": leakage_checks(records),
    }


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(raw_temp)
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def atomic_write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(raw_temp)
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def load_case_manifest(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid manifest JSON at {path}:{line_number}") from exc
            if not isinstance(record, dict) or not str(record.get("seqname") or ""):
                raise ValueError(f"Invalid manifest record at {path}:{line_number}")
            records.append(record)
    return records


def select_manifest_cases(
    records: Sequence[Mapping[str, Any]],
    *,
    protocol: str,
    split: str,
    template_overlap: str = "all",
) -> dict[str, dict[str, Any]]:
    if protocol not in PROTOCOL_GROUP_FIELDS:
        raise ValueError(f"Unknown protocol: {protocol}")
    if split not in SPLIT_NAMES:
        raise ValueError(f"Unknown split: {split}")
    if template_overlap not in {"all", "visible", "novel"}:
        raise ValueError(f"Unknown template-overlap view: {template_overlap}")
    selected: dict[str, dict[str, Any]] = {}
    for raw_record in records:
        record = dict(raw_record)
        if not bool(record.get("eligible")):
            continue
        if str(dict(record.get("splits") or {}).get(protocol) or "") != split:
            continue
        overlap = bool(record.get("template_exact_overlap"))
        if template_overlap == "visible" and not overlap:
            continue
        if template_overlap == "novel" and overlap:
            continue
        selected[str(record["seqname"])] = record
    return selected
