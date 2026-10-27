# -*- coding: utf-8 -*-
"""Runtime helpers for invoking tRNAscan-SE consistently across modules."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
import platform
import re
import shutil
import subprocess
import tempfile
from typing import Dict, Optional


def _trnascan_timeout_seconds() -> float:
    """Return a bounded timeout for one direct tRNAscan-SE invocation."""
    try:
        value = float(os.getenv("TRNASCAN_TIMEOUT_SECONDS", "120"))
    except (TypeError, ValueError):
        value = 120.0
    return max(5.0, min(3600.0, value))


@dataclass(frozen=True)
class TrnaScanResult:
    struct_file_sequence: str
    secondary_structure: str
    type: str = ""
    anticodon: str = ""
    stdout: str = ""
    stderr: str = ""
    output_text: str = ""
    structure_text: str = ""
    stats_text: str = ""
    command: str = ""
    command_options: tuple[str, ...] = ()
    executable: str = ""
    version: str = ""
    search_mode: str = "G"
    domain_hint: str = "unknown"
    mitochondrial_subtype: str = "unknown"

    def as_mapping(self) -> Dict[str, str]:
        data = {
            "StructFileSequence": self.struct_file_sequence,
            "SecondaryStructure": self.secondary_structure,
        }
        if self.type:
            data["Type"] = self.type
        if self.anticodon:
            data["Anticodon"] = self.anticodon
        data["tRNAscanSearchMode"] = self.search_mode
        data["tRNAscanDomainHint"] = self.domain_hint
        if self.mitochondrial_subtype != "unknown":
            data["tRNAscanMitochondrialSubtype"] = self.mitochondrial_subtype
        return data


@dataclass(frozen=True)
class TrnaScanSearchMode:
    """Resolved tRNAscan-SE search mode and its provenance."""

    flag: str
    args: tuple[str, ...]
    domain_hint: str
    mitochondrial_subtype: str
    fallback_reason: str = ""


TRNASCAN_SENSITIVE_OPTIONS: tuple[str, ...] = (
    "--max",
    "-z",
    "100",
    "-X",
    "0",
)


@lru_cache(maxsize=1)
def trnascan_runtime_provenance() -> Dict[str, str]:
    """Return stable executable/version metadata without case-specific paths."""
    executable = shutil.which("tRNAscan-SE") or "tRNAscan-SE"
    try:
        completed = subprocess.run(
            [executable, "-h"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        raw_version = "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        )
        version = next(
            (
                line.strip()
                for line in str(raw_version or "").splitlines()
                if line.strip().startswith("tRNAscan-SE ")
            ),
            "",
        )
    except Exception:
        version = ""
    return {"executable": executable, "version": version}


def resolve_trnascan_search_mode(
    domain_hint: str = "unknown",
    mitochondrial_subtype: str = "unknown",
) -> TrnaScanSearchMode:
    """Map a biological source hint to an explicit tRNAscan-SE search mode.

    ``unknown`` and virus-like inputs use the general cytosolic model (``-G``).
    The other-organellar mode is reserved for chloroplast/cyanelle inputs.
    """
    domain = str(domain_hint or "unknown").strip().lower()
    domain = {
        "general": "unknown",
        "mixed": "unknown",
        "eukaryotic": "eukaryota",
        "eukaryote": "eukaryota",
    }.get(domain, domain) or "unknown"
    subtype = str(mitochondrial_subtype or "unknown").strip().lower()
    subtype = {"vert": "vertebrate", "mammalian": "mammal"}.get(subtype, subtype)

    if domain == "bacteria":
        return TrnaScanSearchMode("B", ("-B",), domain, "unknown")
    if domain == "archaea":
        return TrnaScanSearchMode("A", ("-A",), domain, "unknown")
    if domain in {"eukaryota", "cytosolic_eukaryote"}:
        return TrnaScanSearchMode("E", ("-E",), domain, "unknown")
    if domain in {"chloroplast", "cyanelle"}:
        return TrnaScanSearchMode("O", ("-O",), domain, "unknown")
    if domain == "mitochondrial":
        if subtype == "mammal":
            return TrnaScanSearchMode("M:mammal", ("-M", "mammal"), domain, subtype)
        if subtype in {"vertebrate", "unknown"}:
            reason = "mitochondrial_subtype_defaulted_to_vertebrate" if subtype == "unknown" else ""
            return TrnaScanSearchMode("M:vert", ("-M", "vert"), domain, "vertebrate", reason)
        return TrnaScanSearchMode(
            "M:vert",
            ("-M", "vert"),
            domain,
            "vertebrate",
            "unsupported_mitochondrial_subtype_defaulted_to_vertebrate",
        )

    return TrnaScanSearchMode(
        "G",
        ("-G",),
        domain,
        "unknown",
        "general_mode_for_unknown_or_unsupported_source",
    )


def windows_to_wsl_path(win_path: str) -> str:
    path = win_path.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", path)
    if not match:
        raise RuntimeError(f"无法解析的 Windows 路径格式: {win_path}")
    drive = match.group(1).lower()
    rest = match.group(2)
    return f"/mnt/{drive}/{rest}"


def should_use_wsl() -> bool:
    if not platform.system().lower().startswith("win"):
        return False
    flag = os.getenv("TRNAALIGN_USE_WSL")
    if flag is None:
        return shutil.which("wsl") is not None
    normalized = flag.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return shutil.which("wsl") is not None


def _extract_struct_fields(structure_text: str) -> tuple[str, str]:
    sequence = ""
    structure = ""
    lines = structure_text.strip().splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("Seq:"):
            continue
        seq_line = line.replace("Seq:", "").strip()
        sequence = "".join(char for char in seq_line if char.upper() in "ACGTU").replace("T", "U")
        if index + 1 < len(lines):
            next_line = lines[index + 1]
            if next_line.startswith("Str:"):
                struct_line = next_line.replace("Str:", "").strip()
                structure = "".join(char for char in struct_line if char in ".<>")
        break
    return sequence, structure


def _extract_annotation_fields(output_text: str, seq_id: str) -> tuple[str, str]:
    trna_type = ""
    anticodon = ""
    for line in output_text.strip().splitlines():
        if not line.startswith(seq_id) or line.startswith("Sequence"):
            continue
        parts = line.split()
        # Standard tRNAscan-SE rows are:
        # name, number, begin, end, type, anticodon, intron begin/end, score.
        if len(parts) >= 6:
            trna_type = parts[4]
            anticodon = parts[5]
        break
    return trna_type, anticodon


def run_trnascan_directly(
    seq: str,
    *,
    seq_id: str = "query",
    include_stats: bool = False,
    domain_hint: str = "unknown",
    mitochondrial_subtype: str = "unknown",
) -> TrnaScanResult:
    """Run tRNAscan-SE once and return parsed output plus raw diagnostics."""
    fasta_file: Optional[str] = None
    out_file: Optional[str] = None
    struct_file: Optional[str] = None
    stats_file: Optional[str] = None
    files_to_cleanup: list[str] = []

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=False) as handle:
            handle.write(f">{seq_id}\n{seq}\n")
            fasta_file = handle.name
        out_file = f"{fasta_file}.out"
        struct_file = f"{fasta_file}.struct"
        stats_file = f"{fasta_file}.stats" if include_stats else None
        files_to_cleanup = [path for path in (fasta_file, out_file, struct_file, stats_file) if path]

        use_wsl = should_use_wsl()
        fasta_exec = windows_to_wsl_path(fasta_file) if use_wsl else fasta_file
        out_exec = windows_to_wsl_path(out_file) if use_wsl else out_file
        struct_exec = windows_to_wsl_path(struct_file) if use_wsl else struct_file

        search_mode = resolve_trnascan_search_mode(domain_hint, mitochondrial_subtype)
        command_options = (
            *TRNASCAN_SENSITIVE_OPTIONS,
            *search_mode.args,
            "-Q",
        )
        command = [
            "tRNAscan-SE",
            *command_options,
            "-o",
            out_exec,
            "-f",
            struct_exec,
        ]
        if stats_file is not None:
            stats_exec = windows_to_wsl_path(stats_file) if use_wsl else stats_file
            command.extend(["-m", stats_exec])
        command.append(fasta_exec)
        if use_wsl:
            command = ["wsl"] + command

        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_trnascan_timeout_seconds(),
        )
        if proc.returncode != 0:
            raise RuntimeError(f"tRNAscan-SE 运行失败: {proc.stderr or proc.stdout}".strip())

        output_text = ""
        structure_text = ""
        stats_text = ""
        if out_file and os.path.exists(out_file):
            with open(out_file, "r", encoding="utf-8", errors="replace") as handle:
                output_text = handle.read()
        if struct_file and os.path.exists(struct_file):
            with open(struct_file, "r", encoding="utf-8", errors="replace") as handle:
                structure_text = handle.read()
        if stats_file and os.path.exists(stats_file):
            with open(stats_file, "r", encoding="utf-8", errors="replace") as handle:
                stats_text = handle.read()

        if not output_text.strip() and not structure_text.strip():
            no_hit_text = f"{proc.stdout}\n{proc.stderr}"
            if "No tRNAs found" in no_hit_text:
                raise RuntimeError("tRNAscan-SE 未检测到 tRNA 候选")

        struct_file_sequence, secondary_structure = _extract_struct_fields(structure_text)
        if not struct_file_sequence or not secondary_structure:
            raise RuntimeError("无法从 tRNAscan-SE 输出中解析序列和结构")
        trna_type, anticodon = _extract_annotation_fields(output_text, seq_id)

        runtime_provenance = trnascan_runtime_provenance()
        return TrnaScanResult(
            struct_file_sequence=struct_file_sequence,
            secondary_structure=secondary_structure,
            type=trna_type,
            anticodon=anticodon,
            stdout=proc.stdout,
            stderr=proc.stderr,
            output_text=output_text,
            structure_text=structure_text,
            stats_text=stats_text,
            command=" ".join(command),
            command_options=tuple(command_options),
            executable=str(runtime_provenance.get("executable") or ""),
            version=str(runtime_provenance.get("version") or ""),
            search_mode=search_mode.flag,
            domain_hint=search_mode.domain_hint,
            mitochondrial_subtype=search_mode.mitochondrial_subtype,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"tRNAscan-SE 直接调用超时（超过 {_trnascan_timeout_seconds():g} 秒）"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"tRNAscan-SE 直接调用失败: {exc}") from exc
    finally:
        for path in files_to_cleanup:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
