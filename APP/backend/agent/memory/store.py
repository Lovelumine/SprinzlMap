from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

MEMORY_SCHEMA_VERSION = "2.0"
DEFAULT_MEMORY_PATH = "data/agent_memory/patterns.v2.jsonl"
MEMORY_MODES = frozenset({"off", "read_only", "development_write"})
VERIFIED_SPLIT = "development"
VERIFICATION_SOURCES = frozenset({"gold", "expert", "gold_and_harness"})
ALLOWED_TRIGGER_FIELDS = frozenset(
    {
        "length_bin",
        "difficulty_tier",
        "route",
        "numbering_backend",
        "domain_group",
        "input_type",
        "intron_status",
        "cm_evidence_present",
        "cm_confidence_bin",
        "template_identity_bin",
        "warning_types",
        "failure_regions",
        "validation_reason_codes",
    }
)
PROHIBITED_MEMORY_FIELDS = frozenset(
    {
        "sequence",
        "input_sequence",
        "seqname",
        "template_name",
        "gold",
        "gold_labels",
        "sprinzl_coordinates",
        "source_cases",
        "source_case",
    }
)


class MemoryIntegrityError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_prohibited_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).strip().lower() in PROHIBITED_MEMORY_FIELDS:
                return True
            if _contains_prohibited_field(item):
                return True
    elif isinstance(value, list):
        return any(_contains_prohibited_field(item) for item in value)
    return False


@dataclass
class PatternEntry:
    pattern_id: str
    trigger_signature: Dict[str, Any]
    regions: List[str]
    recommended_actions: List[Dict[str, Any]]
    verified_successes: int
    verified_trials: int
    mean_score_gain: float
    confidence: float
    dataset_id: str
    pattern_type: str = "verified_tool_route"
    split_name: str = VERIFIED_SPLIT
    verification_source: str = "gold_and_harness"
    source_case_count: int = 1
    schema_version: str = MEMORY_SCHEMA_VERSION

    @property
    def historical_gain(self) -> float:
        return float(self.mean_score_gain)

    def validate(self) -> None:
        if self.schema_version != MEMORY_SCHEMA_VERSION:
            raise MemoryIntegrityError("unsupported_memory_schema")
        if self.split_name != VERIFIED_SPLIT:
            raise MemoryIntegrityError("memory_must_come_from_development_split")
        if self.verification_source not in VERIFICATION_SOURCES:
            raise MemoryIntegrityError("memory_requires_gold_or_expert_verification")
        if not self.pattern_id or not self.dataset_id:
            raise MemoryIntegrityError("memory_identity_missing")
        if self.verified_trials < 1 or self.verified_successes < 1:
            raise MemoryIntegrityError("memory_requires_verified_success")
        if self.verified_successes > self.verified_trials:
            raise MemoryIntegrityError("memory_success_count_invalid")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise MemoryIntegrityError("memory_confidence_out_of_range")
        unknown = set(self.trigger_signature) - ALLOWED_TRIGGER_FIELDS
        if unknown:
            raise MemoryIntegrityError(
                f"memory_trigger_fields_not_allowed:{','.join(sorted(unknown))}"
            )
        if _contains_prohibited_field(asdict(self)):
            raise MemoryIntegrityError("memory_contains_case_or_answer_data")


class PatternStore:
    """Versioned store for verified, answer-free tool-routing experience."""

    def __init__(
        self,
        path: str = DEFAULT_MEMORY_PATH,
        *,
        mode: str = "read_only",
        require_frozen: bool = True,
    ) -> None:
        normalized_mode = str(mode or "off").strip().lower()
        if normalized_mode not in MEMORY_MODES:
            raise ValueError(f"unsupported memory mode: {normalized_mode}")
        self.path = Path(path)
        self.manifest_path = self.path.with_suffix(self.path.suffix + ".manifest.json")
        self.mode = normalized_mode
        self.require_frozen = bool(require_frozen)
        self._patterns: List[PatternEntry] = []
        self._loaded = False
        self._snapshot_audit: Dict[str, Any] = {
            "mode": self.mode,
            "path": str(self.path),
            "status": "not_loaded",
        }

    @property
    def snapshot_audit(self) -> Dict[str, Any]:
        return dict(self._snapshot_audit)

    def _ensure_writable(self) -> None:
        if self.mode != "development_write":
            raise MemoryIntegrityError("memory_store_is_read_only")

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> List[PatternEntry]:
        if self.mode == "off":
            self._snapshot_audit["status"] = "disabled"
            return []
        if self._loaded:
            if self.require_frozen and self.mode == "read_only":
                self._verify_frozen_snapshot()
            return list(self._patterns)
        if self.require_frozen and self.mode == "read_only":
            self._verify_frozen_snapshot()
        self._patterns = []
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                        entry = PatternEntry(**payload)
                        entry.validate()
                    except (json.JSONDecodeError, TypeError, MemoryIntegrityError) as exc:
                        raise MemoryIntegrityError(
                            f"invalid_memory_entry:{line_number}:{exc}"
                        ) from exc
                    self._patterns.append(entry)
        self._loaded = True
        self._snapshot_audit.update(
            {
                "status": "loaded",
                "pattern_count": len(self._patterns),
                "sha256": _sha256_file(self.path) if self.path.exists() else "",
                "frozen": self.manifest_path.exists(),
            }
        )
        return list(self._patterns)

    def append(self, pattern: PatternEntry) -> None:
        self._ensure_writable()
        pattern.validate()
        self._ensure_dir()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(pattern), ensure_ascii=True, sort_keys=True) + "\n")
        self._patterns.append(pattern)
        self._loaded = True

    def save_all(self) -> None:
        self._ensure_writable()
        self._ensure_dir()
        with self.path.open("w", encoding="utf-8") as handle:
            for pattern in self._patterns:
                pattern.validate()
                handle.write(json.dumps(asdict(pattern), ensure_ascii=True, sort_keys=True) + "\n")

    def deduplicate(self) -> int:
        self._ensure_writable()
        strongest: Dict[str, PatternEntry] = {}
        for pattern in self._patterns:
            current = strongest.get(pattern.pattern_id)
            pattern_rank = (
                int(pattern.verified_trials),
                int(pattern.verified_successes),
                float(pattern.confidence),
                float(pattern.mean_score_gain),
            )
            current_rank = (
                int(current.verified_trials),
                int(current.verified_successes),
                float(current.confidence),
                float(current.mean_score_gain),
            ) if current is not None else None
            if current is None or pattern_rank > current_rank:
                strongest[pattern.pattern_id] = pattern
        removed = len(self._patterns) - len(strongest)
        self._patterns = sorted(strongest.values(), key=lambda item: item.pattern_id)
        if removed:
            self.save_all()
        return removed

    def freeze(self, *, dataset_id: str) -> Dict[str, Any]:
        self._ensure_writable()
        self.save_all()
        payload = {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "dataset_id": str(dataset_id or ""),
            "source_split": VERIFIED_SPLIT,
            "pattern_count": len(self._patterns),
            "memory_sha256": _sha256_file(self.path),
            "contains_raw_sequences": False,
            "contains_gold_coordinates": False,
        }
        if not payload["dataset_id"]:
            raise MemoryIntegrityError("memory_dataset_id_missing")
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._snapshot_audit.update(
            {
                "status": "frozen",
                "frozen": True,
                "sha256": payload["memory_sha256"],
                "pattern_count": len(self._patterns),
                "dataset_id": payload["dataset_id"],
            }
        )
        return dict(payload)

    def _verify_frozen_snapshot(self) -> None:
        if not self.path.exists() or not self.manifest_path.exists():
            raise MemoryIntegrityError("frozen_memory_snapshot_missing")
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryIntegrityError("frozen_memory_manifest_invalid") from exc
        if payload.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise MemoryIntegrityError("frozen_memory_schema_mismatch")
        if payload.get("source_split") != VERIFIED_SPLIT:
            raise MemoryIntegrityError("frozen_memory_split_mismatch")
        actual = _sha256_file(self.path)
        if payload.get("memory_sha256") != actual:
            raise MemoryIntegrityError("frozen_memory_hash_mismatch")
        self._snapshot_audit.update(
            {
                "status": "verified",
                "frozen": True,
                "sha256": actual,
                "dataset_id": str(payload.get("dataset_id") or ""),
                "pattern_count": int(payload.get("pattern_count") or 0),
            }
        )


__all__ = [
    "ALLOWED_TRIGGER_FIELDS",
    "DEFAULT_MEMORY_PATH",
    "MEMORY_MODES",
    "MEMORY_SCHEMA_VERSION",
    "MemoryIntegrityError",
    "PatternEntry",
    "PatternStore",
    "VERIFIED_SPLIT",
]
