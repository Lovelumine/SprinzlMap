from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_TRNASCAN_MODEL_ROOT = Path("/usr/share/trnascan-se/models")
SEC_MODELS = {
    "arch": "TRNAinf-arch-SeC.cm",
    "bact": "TRNAinf-bact-SeC.cm",
    "euk": "TRNAinf-euk-SeC.cm",
}
BHB_MODELS = {
    "cren_eury": "Cren-eury-BHB-noncan.cm",
    "thaum": "Thaum-BHB-noncan.cm",
}
DOMAIN_GROUPS = {
    "archaea": "arch",
    "bacteria": "bact",
    "cyanelle": "bact",
    "eukaryota": "euk",
    "cytosolic_eukaryote": "euk",
    "chloroplast": "euk",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_sequence(raw: str) -> str:
    sequence = "".join(str(raw or "").upper().replace("T", "U").split())
    invalid = sorted(set(sequence) - set("ACGUN"))
    if invalid:
        raise ValueError(f"Unsupported sequence symbols: {''.join(invalid)}")
    if not sequence:
        raise ValueError("Sequence is empty")
    return sequence


@dataclass(slots=True)
class SpecialRouteEvidence:
    route: str
    status: str
    disposition: str
    message: str
    canonical_numbering_eligible: bool = False
    mature_sequence: str = ""
    raw_to_mature: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    tool_provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    trace_events: list[dict[str, Any]] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return asdict(self)


class SpecialRouteEvidenceService:
    """Run evidence-only models and explicit pre-tRNA transformations."""

    def __init__(
        self,
        *,
        model_root: Path | str = DEFAULT_TRNASCAN_MODEL_ROOT,
        trnascan_executable: str = "tRNAscan-SE",
        cmsearch_executable: str = "cmsearch",
        timeout_seconds: float = 120.0,
    ) -> None:
        self.model_root = Path(model_root)
        self.trnascan_executable = trnascan_executable
        self.cmsearch_executable = cmsearch_executable
        self.timeout_seconds = max(5.0, float(timeout_seconds))

    def run_if_applicable(self, request: Any) -> SpecialRouteEvidence | None:
        domain_hint = str(getattr(request, "domain_hint", "") or "").strip().lower()
        trna_class = str(getattr(request, "trna_class", "") or "unknown").strip().lower()
        intron_status = str(getattr(request, "intron_status", "") or "unknown").strip().lower()
        if domain_hint == "mitochondrial":
            return self._mitochondrial(request)
        if domain_hint == "virus":
            return SpecialRouteEvidence(
                route="virus",
                status="abstained",
                disposition="validated_numbering_model_unavailable",
                message="Viral tRNA numbering requires a validated route-specific model.",
                warnings=["viral_numbering_model_unavailable"],
                trace_events=[
                    {
                        "stage": "special_route_abstained",
                        "route": "virus",
                        "reason": "validated_numbering_model_unavailable",
                        "disposition": "abstained",
                    }
                ],
            )
        if trna_class == "selenocysteine":
            return self._selenocysteine(request)
        if intron_status == "present":
            return self._intron(request)
        input_type = str(getattr(request, "input_type", "") or "unknown").strip().lower()
        if (
            intron_status == "unknown"
            and domain_hint == "archaea"
            and input_type in {"gene_body", "pre_trna"}
        ):
            return self._intron(request, automatic_detection=True)
        return None

    def _mitochondrial(self, request: Any) -> SpecialRouteEvidence:
        subtype = str(getattr(request, "mitochondrial_subtype", "") or "unknown").strip().lower()
        if subtype not in {"mammal", "vertebrate"}:
            return SpecialRouteEvidence(
                route="mitochondrial",
                status="abstained",
                disposition="subtype_required",
                message="Mammalian or vertebrate mitochondrial model scope must be explicit.",
                warnings=["mitochondrial_subtype_required"],
                trace_events=[
                    {
                        "stage": "special_route_abstained",
                        "route": "mitochondrial",
                        "reason": "mitochondrial_subtype_required",
                    }
                ],
            )
        mode = "mammal" if subtype == "mammal" else "vert"
        started = time.perf_counter()
        scan = self._run_trnascan(
            _normalize_sequence(getattr(request, "target_seq", "")),
            mode_flags=("-M", mode),
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        model_prefix = self.model_root / f"TRNAinf-mito-{mode}"
        model_files = sorted(self.model_root.glob(f"TRNAinf-mito-{mode}*.cm"))
        provenance = self._trnascan_provenance(
            models=model_files,
            extra={
                "mode": f"mitochondrial:{subtype}",
                "pressed_model_database": self._path_provenance(model_prefix),
            },
        )
        hits = list(scan.get("hits") or [])
        detected = bool(hits)
        return SpecialRouteEvidence(
            route="mitochondrial",
            status="evidence_ready" if detected else "not_detected",
            disposition="evidence_only_abstain",
            message=(
                "Specialized mitochondrial evidence is available, but no validated Sprinzl crosswalk is installed."
                if detected
                else "The selected mitochondrial model scope did not detect a tRNA candidate."
            ),
            evidence={**scan, "subtype": subtype},
            tool_provenance=provenance,
            warnings=[
                "mitochondrial_special_model_is_evidence_only",
                "sprinzl_numbering_abstained_without_validated_crosswalk",
            ],
            trace_events=[
                {
                    "stage": "special_tool_observation",
                    "route": "mitochondrial",
                    "tool": "tRNAscan-SE",
                    "subtype": subtype,
                    "hit_count": len(hits),
                    "elapsed_ms": elapsed_ms,
                    "disposition": "abstained",
                }
            ],
        )

    def _selenocysteine(self, request: Any) -> SpecialRouteEvidence:
        domain_hint = str(getattr(request, "domain_hint", "") or "").strip().lower()
        explicit_group = DOMAIN_GROUPS.get(domain_hint)
        groups = [explicit_group] if explicit_group else list(SEC_MODELS)
        sequence = _normalize_sequence(getattr(request, "target_seq", ""))
        observations: list[dict[str, Any]] = []
        started = time.perf_counter()
        for group in groups:
            model_path = self.model_root / SEC_MODELS[group]
            observations.append(
                self._run_cmsearch(
                    sequence,
                    model_path=model_path,
                    reporting_threshold=0.0,
                    inclusion_threshold=20.0,
                )
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        scored = [item for item in observations if item.get("best_score") is not None]
        selected = max(scored, key=lambda item: float(item["best_score"])) if scored else None
        detected = bool(selected and float(selected["best_score"]) >= 20.0)
        model_paths = [self.model_root / SEC_MODELS[group] for group in groups]
        return SpecialRouteEvidence(
            route="selenocysteine",
            status="evidence_ready" if detected else "not_detected",
            disposition="evidence_only_abstain",
            message=(
                "A domain-specific Sec CM met the configured evidence threshold; canonical Sprinzl projection remains disabled."
                if detected
                else "No scored Sec CM met the configured 20-bit evidence threshold."
            ),
            evidence={
                "domain_hint": domain_hint or "unknown",
                "selection_policy": "explicit_domain_sec_model" if explicit_group else "score_all_domain_sec_models",
                "threshold_bits": 20.0,
                "selected_model": (selected or {}).get("model_name"),
                "observations": observations,
            },
            tool_provenance=self._cmsearch_provenance(model_paths),
            warnings=[
                "sec_special_model_is_evidence_only",
                "sprinzl_numbering_abstained_without_validated_sec_crosswalk",
            ],
            trace_events=[
                {
                    "stage": "special_tool_observation",
                    "route": "selenocysteine",
                    "tool": "cmsearch",
                    "model_count": len(observations),
                    "detected": detected,
                    "elapsed_ms": elapsed_ms,
                    "disposition": "abstained",
                }
            ],
        )

    def _intron(
        self,
        request: Any,
        *,
        automatic_detection: bool = False,
    ) -> SpecialRouteEvidence:
        domain_hint = str(getattr(request, "domain_hint", "") or "").strip().lower()
        sequence = _normalize_sequence(getattr(request, "target_seq", ""))
        if domain_hint != "archaea":
            return SpecialRouteEvidence(
                route="intron",
                status="abstained",
                disposition="unsupported_intron_scope",
                message="The installed BHB route is limited to archaeal noncanonical introns.",
                warnings=["archaeal_domain_required_for_bhb_route"],
                trace_events=[
                    {
                        "stage": "special_route_abstained",
                        "route": "intron",
                        "reason": "archaeal_domain_required",
                    }
                ],
            )
        started = time.perf_counter()
        scan = self._run_trnascan(sequence, mode_flags=("-A",))
        bhb_observations = [
            self._run_cmsearch(
                sequence,
                model_path=self.model_root / filename,
                reporting_threshold=0.0,
                inclusion_threshold=6.5,
            )
            for filename in BHB_MODELS.values()
        ]
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        hits = sorted(
            list(scan.get("hits") or []),
            key=lambda item: float(item.get("score") or float("-inf")),
            reverse=True,
        )
        hit = hits[0] if len(hits) == 1 else None
        transform = self._splice_from_hit(
            sequence,
            hit,
            require_intron=not automatic_detection,
        )
        models = [self.model_root / filename for filename in BHB_MODELS.values()]
        provenance = self._trnascan_provenance(
            models=models,
            extra={
                "mode": "archaea_with_noncanonical_intron_scan",
                "BHB_cm_cutoff_bits": 6.5,
                "cmsearch": self._cmsearch_provenance(models),
            },
        )
        evidence = {
            **scan,
            "bhb_threshold_bits": 6.5,
            "bhb_observations": bhb_observations,
        }
        if transform is None:
            ambiguity_reason = (
                "multiple_trnascan_hits"
                if len(hits) > 1
                else "intron_not_detected"
                if hits and not automatic_detection
                else "boundaries_ambiguous_or_not_detected"
            )
            return SpecialRouteEvidence(
                route="archaeal_ncintron",
                status="abstained",
                disposition="ambiguous_splice_boundaries",
                message=(
                    "No single forward-strand tRNA body and intron interval could be established from tRNAscan-SE output."
                    if not automatic_detection
                    else "Automatic input inspection could not establish one forward-strand tRNA body."
                ),
                evidence=evidence,
                tool_provenance=provenance,
                warnings=[f"intron_evidence_{ambiguity_reason}"],
                trace_events=[
                    {
                        "stage": "special_tool_observation",
                        "route": "archaeal_ncintron",
                        "tool": "tRNAscan-SE+BHB",
                        "hit_count": len(hits),
                        "elapsed_ms": elapsed_ms,
                        "disposition": "abstained",
                    }
                ],
            )
        mature_sequence, raw_to_mature, transform_meta = transform
        removed = dict(transform_meta.get("removed_intron_interval") or {})
        intron_detected = bool(transform_meta.get("intron_detected"))
        evidence["removed_intron_interval"] = removed
        evidence["input_transform"] = transform_meta
        evidence["intron_detection_mode"] = (
            "automatic_unknown_status" if automatic_detection else "explicit_present_claim"
        )
        evidence["derived_intron_status"] = "removed" if intron_detected else "absent"
        evidence["raw_sequence_length"] = len(sequence)
        evidence["mature_sequence_length"] = len(mature_sequence)
        return SpecialRouteEvidence(
            route="archaeal_ncintron",
            status="mature_sequence_ready",
            disposition="ready_for_canonical_numbering",
            message=(
                "A single tRNAscan-SE intron interval was removed; numbering applies to the derived mature sequence."
                if intron_detected
                else "No intron was detected; tRNA body boundaries were used to derive the mature numbering input."
            ),
            canonical_numbering_eligible=True,
            mature_sequence=mature_sequence,
            raw_to_mature=raw_to_mature,
            evidence=evidence,
            tool_provenance=provenance,
            warnings=[
                "numbering_applies_to_derived_mature_sequence",
                (
                    "intron_removed_from_trnascan_interval"
                    if intron_detected
                    else "intron_not_detected_by_trnascan"
                ),
            ],
            trace_events=[
                {
                    "stage": "special_tool_observation",
                    "route": "archaeal_ncintron",
                    "tool": "tRNAscan-SE+BHB",
                    "hit_count": len(hits),
                    "removed_interval": removed,
                    "source_interval": dict(transform_meta.get("source_interval") or {}),
                    "automatic_detection": automatic_detection,
                    "intron_detected": intron_detected,
                    "elapsed_ms": elapsed_ms,
                    "disposition": "mature_sequence_ready",
                }
            ],
        )

    def _run_trnascan(self, sequence: str, *, mode_flags: Sequence[str]) -> dict[str, Any]:
        if shutil.which(self.trnascan_executable) is None:
            raise FileNotFoundError(self.trnascan_executable)
        with tempfile.TemporaryDirectory(prefix="special_trnascan_") as raw_temp:
            temp = Path(raw_temp)
            fasta = temp / "query.fa"
            results = temp / "results.tsv"
            structures = temp / "structures.txt"
            fasta.write_text(f">query\n{sequence.replace('U', 'T')}\n", encoding="ascii")
            command = [
                self.trnascan_executable,
                *mode_flags,
                "--detail",
                "--max",
                "-X",
                "0",
                "-Q",
                "-o",
                str(results),
                "-f",
                str(structures),
                str(fasta),
            ]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "tRNAscan-SE failed").strip()
                raise RuntimeError(f"tRNAscan-SE exited with {completed.returncode}: {detail}")
            result_text = results.read_text(encoding="utf-8") if results.exists() else ""
            structure_text = structures.read_text(encoding="utf-8") if structures.exists() else ""
        return {
            "command_options": list(mode_flags) + ["--detail", "--max", "-X", "0"],
            "hits": self._parse_trnascan_results(result_text),
            "structures": self._parse_trnascan_structures(structure_text),
        }

    @staticmethod
    def _parse_trnascan_results(text: str) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for raw_line in str(text or "").splitlines():
            parts = raw_line.split()
            if len(parts) < 11 or not parts[1].isdigit():
                continue
            try:
                hit = {
                    "sequence_name": parts[0],
                    "trna_number": int(parts[1]),
                    "begin": int(parts[2]),
                    "end": int(parts[3]),
                    "isotype": parts[4],
                    "anticodon": parts[5].upper().replace("T", "U"),
                    "intron_begin": int(parts[6]),
                    "intron_end": int(parts[7]),
                    "score": float(parts[8]),
                    "isotype_model": parts[9],
                    "isotype_score": float(parts[10]),
                    "note": " ".join(parts[11:]),
                }
            except (TypeError, ValueError):
                continue
            hits.append(hit)
        return hits

    @staticmethod
    def _parse_trnascan_structures(text: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if ".trna" in line and not line.startswith(("Seq:", "Str:")):
                if current:
                    records.append(current)
                current = {"header": line}
            elif current is not None and line.startswith("Type:"):
                current["type_line"] = line
            elif current is not None and line.startswith("Seq:"):
                current["sequence"] = line[4:].strip().upper().replace("T", "U")
            elif current is not None and line.startswith("Str:"):
                current["structure"] = line[4:].strip()
        if current:
            records.append(current)
        return records

    def _run_cmsearch(
        self,
        sequence: str,
        *,
        model_path: Path,
        reporting_threshold: float,
        inclusion_threshold: float,
    ) -> dict[str, Any]:
        if shutil.which(self.cmsearch_executable) is None:
            raise FileNotFoundError(self.cmsearch_executable)
        if not model_path.is_file():
            raise FileNotFoundError(model_path)
        with tempfile.TemporaryDirectory(prefix="special_cmsearch_") as raw_temp:
            temp = Path(raw_temp)
            fasta = temp / "query.fa"
            table = temp / "hits.tbl"
            fasta.write_text(f">query\n{sequence}\n", encoding="ascii")
            command = [
                self.cmsearch_executable,
                "--toponly",
                "--noali",
                "-T",
                str(float(reporting_threshold)),
                "--incT",
                str(float(inclusion_threshold)),
                "--tblout",
                str(table),
                str(model_path),
                str(fasta),
            ]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "cmsearch failed").strip()
                raise RuntimeError(f"cmsearch exited with {completed.returncode}: {detail}")
            table_text = table.read_text(encoding="utf-8") if table.exists() else ""
        hits = self._parse_cmsearch_table(table_text)
        best = max(hits, key=lambda item: float(item["score"])) if hits else None
        return {
            "model_name": model_path.name,
            "model_sha256": _sha256(model_path),
            "reporting_threshold_bits": float(reporting_threshold),
            "inclusion_threshold_bits": float(inclusion_threshold),
            "best_score": (best or {}).get("score"),
            "best_evalue": (best or {}).get("evalue"),
            "included": bool(best and float(best["score"]) >= float(inclusion_threshold)),
            "hits": hits,
        }

    @staticmethod
    def _parse_cmsearch_table(text: str) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for raw_line in str(text or "").splitlines():
            if not raw_line or raw_line.startswith("#"):
                continue
            parts = raw_line.split(maxsplit=17)
            if len(parts) < 17:
                continue
            try:
                hits.append(
                    {
                        "target": parts[0],
                        "query_model": parts[2],
                        "model_from": int(parts[5]),
                        "model_to": int(parts[6]),
                        "sequence_from": int(parts[7]),
                        "sequence_to": int(parts[8]),
                        "strand": parts[9],
                        "truncated": parts[10],
                        "bias": float(parts[13]),
                        "score": float(parts[14]),
                        "evalue": float(parts[15]),
                        "included_marker": parts[16],
                        "description": parts[17] if len(parts) > 17 else "",
                    }
                )
            except (TypeError, ValueError):
                continue
        return hits

    @staticmethod
    def _splice_from_hit(
        sequence: str,
        hit: Mapping[str, Any] | None,
        *,
        require_intron: bool = True,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]] | None:
        if not hit:
            return None
        begin = int(hit.get("begin") or 0)
        end = int(hit.get("end") or 0)
        intron_begin = int(hit.get("intron_begin") or 0)
        intron_end = int(hit.get("intron_end") or 0)
        if begin <= 0 or end < begin or end > len(sequence):
            return None
        intron_detected = intron_begin > 0 or intron_end > 0
        if intron_detected and (intron_begin <= 0 or intron_end < intron_begin):
            return None
        if require_intron and not intron_detected:
            return None
        if intron_detected and not (begin <= intron_begin <= intron_end <= end):
            return None
        retained = [
            index
            for index in range(begin, end + 1)
            if not intron_detected or not (intron_begin <= index <= intron_end)
        ]
        mature = "".join(sequence[index - 1] for index in retained)
        mapping = [
            {
                "mature_residue_index": mature_index,
                "raw_residue_index": raw_index,
                "base": sequence[raw_index - 1],
            }
            for mature_index, raw_index in enumerate(retained, start=1)
        ]
        return mature, mapping, {
            "source_interval": {"start": begin, "end": end},
            "removed_intron_interval": (
                {"start": intron_begin, "end": intron_end}
                if intron_detected
                else {}
            ),
            "removed_flanks": {
                "five_prime": max(0, begin - 1),
                "three_prime": max(0, len(sequence) - end),
            },
            "intron_detected": intron_detected,
        }

    def _trnascan_provenance(
        self,
        *,
        models: Sequence[Path],
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        completed = subprocess.run(
            [self.trnascan_executable, "-h"],
            check=False,
            capture_output=True,
            text=True,
        )
        version_text = completed.stdout or completed.stderr
        return {
            "tool": "tRNAscan-SE",
            "executable": shutil.which(self.trnascan_executable) or self.trnascan_executable,
            "version": "\n".join(version_text.splitlines()[:3]),
            "models": [self._path_provenance(path) for path in models],
            **dict(extra or {}),
        }

    def _cmsearch_provenance(self, models: Sequence[Path]) -> dict[str, Any]:
        completed = subprocess.run(
            [self.cmsearch_executable, "-h"],
            check=False,
            capture_output=True,
            text=True,
        )
        version_text = completed.stdout or completed.stderr
        return {
            "tool": "cmsearch",
            "executable": shutil.which(self.cmsearch_executable) or self.cmsearch_executable,
            "version": "\n".join(version_text.splitlines()[:3]),
            "models": [self._path_provenance(path) for path in models],
        }

    @staticmethod
    def _path_provenance(path: Path) -> dict[str, Any]:
        return {
            "name": path.name,
            "path": str(path),
            "sha256": _sha256(path) if path.is_file() else "",
        }


__all__ = ["SpecialRouteEvidence", "SpecialRouteEvidenceService"]
