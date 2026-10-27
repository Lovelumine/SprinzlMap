from __future__ import annotations

import csv
import hashlib
import io
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .calibration import CMDomainCalibrator, DEFAULT_CALIBRATION_ARTIFACT
from .sprinzl_coordinates import label_kind, labels_are_monotonic, labels_from_consensus_structure


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MODEL_ROOT = PROJECT_ROOT / "research" / "third_party" / "trnaviz" / "engine"
MODEL_FILES = {
    "all": "all-num.cm",
    "arch": "arch-num.cm",
    "bact": "bact-num.cm",
    "euk": "euk-num.cm",
}
MODEL_SCOPES = {
    "all": "cross-domain canonical fallback",
    "arch": "archaeal canonical tRNA",
    "bact": "bacterial canonical tRNA",
    "euk": "eukaryotic canonical tRNA",
}
DOMAIN_HINT_GROUPS = {
    "arch": "arch",
    "archaea": "arch",
    "archaeal": "arch",
    "bact": "bact",
    "bacteria": "bact",
    "bacterial": "bact",
    "cyanelle": "bact",
    "euk": "euk",
    "eukaryota": "euk",
    "eukaryotic": "euk",
    "cytosolic_eukaryote": "euk",
    "chloroplast": "euk",
    "all": "all",
}
ALIGNMENT_GAPS = frozenset({".", "-", "~", "_"})
PAIR_TOKENS = {"(": ")", "<": ">", "[": "]", "{": "}"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_sequence(raw: str) -> str:
    cleaned = "".join(str(raw or "").upper().replace("T", "U").split())
    invalid = sorted(set(cleaned) - set("ACGUN"))
    if invalid:
        raise ValueError(f"Unsupported sequence symbols: {''.join(invalid)}")
    if not cleaned:
        raise ValueError("Sequence is empty")
    return cleaned


def model_group_for_source(source: str) -> str:
    value = str(source or "").strip().lower()
    return DOMAIN_HINT_GROUPS.get(value, "all")


def explicit_model_group_for_hint(source: str) -> str | None:
    return DOMAIN_HINT_GROUPS.get(str(source or "").strip().lower())


@dataclass(frozen=True, slots=True)
class NumberingModel:
    group: str
    name: str
    path: str
    scope: str
    sha256: str


@dataclass(frozen=True, slots=True)
class CmalignScore:
    bit_score: float | None = None
    average_posterior: float | None = None
    cm_from: int | None = None
    cm_to: int | None = None
    truncated: str = ""


@dataclass(frozen=True, slots=True)
class NumberedResidue:
    residue_index: int
    base: str
    label: str
    label_kind: str
    alignment_column: int
    posterior_code: str = ""


@dataclass(slots=True)
class CMNumberingResult:
    sequence_id: str
    input_sequence: str
    model: NumberingModel
    score: CmalignScore
    residues: list[NumberedResidue]
    aligned_sequence: str
    consensus_structure: str
    reference_annotation: str
    structure_pairs: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    trace_events: list[dict[str, Any]] = field(default_factory=list)
    portfolio_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def labels(self) -> list[str]:
        return [residue.label for residue in self.residues]

    @property
    def bases(self) -> list[str]:
        return [residue.base for residue in self.residues]

    @property
    def base_preserved(self) -> bool:
        return "".join(self.bases) == self.input_sequence

    def csv_content(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(self.labels)
        writer.writerow(["-"] * len(self.residues))
        writer.writerow(self.bases)
        return output.getvalue()

    def metadata(self) -> dict[str, Any]:
        return {
            "numbering_backend": "cmalign",
            "numbering_model": asdict(self.model),
            "cm_score": asdict(self.score),
            "base_preserved": self.base_preserved,
            "structure_view": {"id_pairs": list(self.structure_pairs)},
            "agent_v2": {
                "route": "cm_direct",
                "route_confidence": self.score.average_posterior,
                "out_of_scope": self.model.group == "all",
                "in_canonical_benchmark": self.model.group != "all",
                "expanded": False,
                "best_template": self.model.name,
                "best_snapshot_id": "cm-root",
                "best_source_action": "align_numbering_cm",
                "best_score": self.score.bit_score,
                "hard_guard_passed": None,
                "difficulty_tier": None,
                "trace_events": list(self.trace_events),
                "branch_summaries": [],
                "work_state_summary": {"stop_reason": "cm_candidate_returned"},
                "context_summary": {},
            },
            "cm_numbering": {
                "sequence_id": self.sequence_id,
                "consensus_structure": self.consensus_structure,
                "reference_annotation": self.reference_annotation,
                "residues": [asdict(residue) for residue in self.residues],
            },
            "cm_portfolio": dict(self.portfolio_evidence),
        }


@dataclass(slots=True)
class CMPortfolioResult:
    selected: CMNumberingResult
    domain_hint: str
    selection_policy: str
    raw_bit_score_margin: float | None
    score_margin_calibrated: bool
    calibration: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def evidence(self) -> dict[str, Any]:
        return {
            "domain_hint": self.domain_hint,
            "selected_group": self.selected.model.group,
            "selected_model": self.selected.model.name,
            "selection_policy": self.selection_policy,
            "raw_bit_score_margin": self.raw_bit_score_margin,
            "score_margin_calibrated": self.score_margin_calibrated,
            "domain_probabilities_calibrated": self.calibration.get("status") == "calibrated",
            "calibration": dict(self.calibration),
            "candidates": list(self.candidates),
            "warnings": list(self.warnings),
        }


class CmalignNumberingService:
    def __init__(
        self,
        *,
        model_root: Path | str = DEFAULT_MODEL_ROOT,
        executable: str = "cmalign",
        cpu: int = 1,
        calibration_artifact: Path | str = DEFAULT_CALIBRATION_ARTIFACT,
    ) -> None:
        self.model_root = Path(model_root)
        self.executable = str(executable)
        self.cpu = max(1, int(cpu))
        self.calibrator = CMDomainCalibrator(calibration_artifact)

    def model(self, group: str) -> NumberingModel:
        normalized_group = str(group or "").strip().lower()
        if normalized_group == "other":
            normalized_group = "all"
        if normalized_group not in MODEL_FILES:
            raise ValueError(f"Unknown CM model group: {group}")
        path = self.model_root / MODEL_FILES[normalized_group]
        if not path.is_file():
            raise FileNotFoundError(path)
        return NumberingModel(
            group=normalized_group,
            name=path.stem,
            path=str(path),
            scope=MODEL_SCOPES[normalized_group],
            sha256=_sha256(path),
        )

    def provenance(self) -> dict[str, Any]:
        executable = shutil.which(self.executable) or self.executable
        version = subprocess.run(
            [self.executable, "-h"],
            check=False,
            capture_output=True,
            text=True,
        )
        version_text = version.stdout or version.stderr
        return {
            "backend": "cmalign",
            "executable": executable,
            "version": "\n".join(version_text.splitlines()[:3]),
            "options": ["-g", "--notrunc"],
            "cpu_per_case": self.cpu,
            "models": {group: asdict(self.model(group)) for group in MODEL_FILES},
        }

    def align(
        self,
        sequence: str,
        *,
        model_group: str,
        sequence_id: str = "query",
    ) -> CMNumberingResult:
        normalized = _normalize_sequence(sequence)
        model = self.model(model_group)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="cm_numbering_") as raw_temp:
            temp = Path(raw_temp)
            fasta = temp / "query.fa"
            stockholm = temp / "alignment.sto"
            scores = temp / "scores.txt"
            fasta.write_text(f">query\n{normalized}\n", encoding="ascii")
            command = [
                self.executable,
                "--cpu",
                str(self.cpu),
                "-g",
                "--notrunc",
                "--sfile",
                str(scores),
                "-o",
                str(stockholm),
                str(Path(model.path)),
                str(fasta),
            ]
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "cmalign failed").strip()
                raise RuntimeError(f"cmalign exited with {completed.returncode}: {detail}")
            parsed = _parse_stockholm(stockholm, "query")
            score = _parse_score_file(scores)

        aligned = parsed["sequence"]
        ss_cons = parsed["ss_cons"]
        posterior = parsed["posterior"]
        labels_by_column = labels_from_consensus_structure(ss_cons)
        if len(aligned) != len(labels_by_column):
            raise RuntimeError(
                f"Alignment/label length mismatch: {len(aligned)}!={len(labels_by_column)}"
            )
        residues: list[NumberedResidue] = []
        for column, (raw_base, label) in enumerate(zip(aligned, labels_by_column), start=1):
            if raw_base in ALIGNMENT_GAPS:
                continue
            base = raw_base.upper().replace("T", "U")
            residues.append(
                NumberedResidue(
                    residue_index=len(residues) + 1,
                    base=base,
                    label=label,
                    label_kind=label_kind(label),
                    alignment_column=column,
                    posterior_code=posterior[column - 1] if column <= len(posterior) else "",
                )
            )
        labels = [residue.label for residue in residues]
        warnings: list[str] = []
        if model.group == "all":
            warnings.append("cross_domain_cm_fallback_requires_scope_review")
        if not labels_are_monotonic(labels):
            warnings.append("cm_labels_not_monotonic")
        pairs = _structure_pairs(ss_cons, labels_by_column, aligned)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        result = CMNumberingResult(
            sequence_id=str(sequence_id or "query"),
            input_sequence=normalized,
            model=model,
            score=score,
            residues=residues,
            aligned_sequence=aligned,
            consensus_structure=ss_cons,
            reference_annotation=parsed["rf"],
            structure_pairs=pairs,
            warnings=warnings,
            trace_events=[
                {
                    "stage": "tool_started",
                    "tool": "align_numbering_cm",
                    "purpose": "assign_structure_homologous_sprinzl_coordinates",
                    "model_group": model.group,
                    "model_sha256": model.sha256,
                },
                {
                    "stage": "tool_observation",
                    "tool": "align_numbering_cm",
                    "bit_score": score.bit_score,
                    "average_posterior": score.average_posterior,
                    "residue_count": len(residues),
                    "structure_pair_count": len(pairs),
                    "elapsed_ms": elapsed_ms,
                },
            ],
        )
        if not result.base_preserved:
            raise RuntimeError("CM alignment failed base preservation")
        result.trace_events.append(
            {
                "stage": "deterministic_validation",
                "base_preserved": True,
                "labels_monotonic": labels_are_monotonic(result.labels),
                "warning_count": len(result.warnings),
            }
        )
        return result

    def align_portfolio(
        self,
        sequence: str,
        *,
        sequence_id: str = "query",
        domain_hint: str = "",
        model_groups: Sequence[str] = ("arch", "bact", "euk", "all"),
    ) -> CMPortfolioResult:
        normalized_groups: list[str] = []
        for raw_group in model_groups:
            group = str(raw_group or "").strip().lower()
            if group == "other":
                group = "all"
            if group not in MODEL_FILES:
                raise ValueError(f"Unknown CM model group in portfolio: {raw_group}")
            if group not in normalized_groups:
                normalized_groups.append(group)
        if not normalized_groups:
            raise ValueError("CM portfolio is empty")

        results: dict[str, CMNumberingResult] = {}
        candidates: list[dict[str, Any]] = []
        for group in normalized_groups:
            try:
                result = self.align(sequence, model_group=group, sequence_id=sequence_id)
            except Exception as exc:
                candidates.append(
                    {
                        "group": group,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            results[group] = result
            candidates.append(
                {
                    "group": group,
                    "status": "ok",
                    "model": asdict(result.model),
                    "bit_score": result.score.bit_score,
                    "average_posterior": result.score.average_posterior,
                    "base_preserved": result.base_preserved,
                    "labels_monotonic": labels_are_monotonic(result.labels),
                    "warning_count": len(result.warnings),
                }
            )
        if not results:
            raise RuntimeError("All CM portfolio models failed")

        explicit_group = explicit_model_group_for_hint(domain_hint)
        calibration = self.calibrator.predict(candidates, sequence_length=len(_normalize_sequence(sequence)))
        if explicit_group in results:
            selected_group = str(explicit_group)
            selection_policy = "explicit_domain_hint"
        elif (
            calibration.get("status") == "calibrated"
            and calibration.get("predicted_group") in results
        ):
            selected_group = str(calibration["predicted_group"])
            selection_policy = (
                "calibrated_domain_selector"
                if calibration.get("disposition") == "accepted"
                else "calibrated_domain_selector_abstained"
            )
        else:
            domain_results = [
                result for group, result in results.items() if group in {"arch", "bact", "euk"}
            ]
            selectable = domain_results or list(results.values())
            selected_group = max(selectable, key=self._portfolio_score_key).model.group
            selection_policy = "uncalibrated_domain_selector_unavailable"

        selected = results[selected_group]
        successful_others = [result for group, result in results.items() if group != selected_group]
        selected_score = selected.score.bit_score
        runner_up_score = max(
            (result.score.bit_score for result in successful_others if result.score.bit_score is not None),
            default=None,
        )
        raw_margin = (
            round(float(selected_score) - float(runner_up_score), 6)
            if selected_score is not None and runner_up_score is not None
            else None
        )
        warnings: list[str] = []
        if explicit_group is None and calibration.get("disposition") != "accepted":
            warnings.append("cm_domain_selection_requires_investigation")
        if explicit_group is None and calibration.get("status") != "calibrated":
            warnings.append("cm_domain_calibration_unavailable_or_incompatible")
        if raw_margin is not None and raw_margin < 5.0:
            warnings.append("cm_portfolio_low_raw_score_margin")
        if selected.model.group == "all":
            warnings.append("broad_cm_selected_requires_scope_review")

        portfolio = CMPortfolioResult(
            selected=selected,
            domain_hint=str(domain_hint or ""),
            selection_policy=selection_policy,
            raw_bit_score_margin=raw_margin,
            score_margin_calibrated=False,
            calibration=calibration,
            candidates=candidates,
            warnings=warnings,
        )
        selected.portfolio_evidence = portfolio.evidence()
        for warning in warnings:
            if warning not in selected.warnings:
                selected.warnings.append(warning)
        selected.trace_events.insert(
            0,
            {
                "stage": "tool_started",
                "tool": "score_numbering_models",
                "purpose": "compare_numbering_cm_candidates",
                "domain_hint": str(domain_hint or ""),
                "model_groups": list(normalized_groups),
            },
        )
        selected.trace_events.append(
            {
                "stage": "tool_observation",
                "tool": "score_numbering_models",
                "selected_group": selected.model.group,
                "selection_policy": selection_policy,
                "raw_bit_score_margin": raw_margin,
                "score_margin_calibrated": False,
                "domain_probabilities_calibrated": calibration.get("status") == "calibrated",
                "calibration_disposition": calibration.get("disposition"),
                "calibrated_probabilities": dict(calibration.get("probabilities") or {}),
                "candidate_count": len(candidates),
            }
        )
        return portfolio

    @staticmethod
    def _portfolio_score_key(result: CMNumberingResult) -> tuple[float, float]:
        return (
            float(result.score.bit_score) if result.score.bit_score is not None else float("-inf"),
            float(result.score.average_posterior)
            if result.score.average_posterior is not None
            else float("-inf"),
        )


def _parse_stockholm(path: Path, sequence_name: str) -> dict[str, str]:
    values: dict[str, list[str]] = {
        "sequence": [],
        "posterior": [],
        "ss_cons": [],
        "rf": [],
    }
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped == "//":
                continue
            if line.startswith(f"#=GR {sequence_name} PP"):
                values["posterior"].append(stripped.split()[-1])
            elif line.startswith("#=GC SS_cons"):
                values["ss_cons"].append(stripped.split()[-1])
            elif line.startswith("#=GC RF"):
                values["rf"].append(stripped.split()[-1])
            elif not line.startswith("#"):
                parts = stripped.split()
                if len(parts) >= 2 and parts[0] == sequence_name:
                    values["sequence"].append(parts[1])
    parsed = {key: "".join(chunks) for key, chunks in values.items()}
    if not parsed["sequence"] or not parsed["ss_cons"]:
        raise RuntimeError(f"Incomplete Stockholm output: {path}")
    if len(parsed["sequence"]) != len(parsed["ss_cons"]):
        raise RuntimeError("Stockholm sequence and SS_cons lengths differ")
    return parsed


def _parse_score_file(path: Path) -> CmalignScore:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 8:
                continue
            return CmalignScore(
                bit_score=float(parts[6]),
                average_posterior=float(parts[7]),
                cm_from=int(parts[3]),
                cm_to=int(parts[4]),
                truncated=str(parts[5]),
            )
    return CmalignScore()


def _structure_pairs(
    ss_cons: str,
    labels_by_column: Sequence[str],
    aligned_sequence: str,
) -> list[dict[str, str]]:
    stacks: dict[str, list[int]] = {opening: [] for opening in PAIR_TOKENS}
    closing_to_opening = {closing: opening for opening, closing in PAIR_TOKENS.items()}
    column_pairs: list[tuple[int, int]] = []
    for index, token in enumerate(ss_cons):
        if token in stacks:
            stacks[token].append(index)
        elif token in closing_to_opening:
            opening = closing_to_opening[token]
            if stacks[opening]:
                column_pairs.append((stacks[opening].pop(), index))

    pairs: list[dict[str, str]] = []
    for left, right in column_pairs:
        if aligned_sequence[left] in ALIGNMENT_GAPS or aligned_sequence[right] in ALIGNMENT_GAPS:
            continue
        pairs.append(
            {
                "left_id": str(labels_by_column[left]),
                "right_id": str(labels_by_column[right]),
            }
        )
    return pairs


__all__ = [
    "CMNumberingResult",
    "CMPortfolioResult",
    "CmalignNumberingService",
    "CmalignScore",
    "DEFAULT_MODEL_ROOT",
    "MODEL_FILES",
    "NumberedResidue",
    "NumberingModel",
    "explicit_model_group_for_hint",
    "model_group_for_source",
]
