from backend.domain.alignment.tRNAscan_SE_annotator import _build_trnascan_cmd
from backend.domain.common.trnascan_runtime import (
    _extract_annotation_fields,
    resolve_trnascan_search_mode,
)


def test_extract_annotation_fields_uses_type_and_anticodon_columns() -> None:
    output = """\
Sequence tRNA Bounds tRNA Anti Intron Bounds Inf
Name tRNA # Begin End Type Codon Begin End Score Note
query 1 2 74 His GTG 0 0 57.8
"""

    assert _extract_annotation_fields(output, "query") == ("His", "GTG")


def test_unknown_source_uses_general_mode() -> None:
    mode = resolve_trnascan_search_mode("unknown")

    assert mode.flag == "G"
    assert mode.args == ("-G",)


def test_domain_hints_select_explicit_trnascan_modes() -> None:
    assert resolve_trnascan_search_mode("bacteria").args == ("-B",)
    assert resolve_trnascan_search_mode("archaea").args == ("-A",)
    assert resolve_trnascan_search_mode("cytosolic_eukaryote").args == ("-E",)
    assert resolve_trnascan_search_mode("chloroplast").args == ("-O",)
    assert resolve_trnascan_search_mode("mitochondrial", "mammal").args == (
        "-M",
        "mammal",
    )


def test_annotator_command_contains_resolved_mode() -> None:
    command = _build_trnascan_cmd(
        "/tmp/input.fa",
        "/tmp/results.txt",
        "/tmp/struct.txt",
        domain_hint="bacteria",
    )

    assert "-B" in command
    assert "-O" not in command
    assert "-Q" in command
    assert "--max" in command
    assert command[command.index("-X") + 1] == "0"


def test_direct_and_batch_commands_share_scientific_search_options(monkeypatch) -> None:
    from backend.domain.common import trnascan_runtime

    captured = {}

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "stopped after command capture"

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        return Completed()

    monkeypatch.setattr(trnascan_runtime.subprocess, "run", fake_run)
    try:
        trnascan_runtime.run_trnascan_directly(
            "GCGGCC",
            domain_hint="archaea",
        )
    except RuntimeError:
        pass

    direct = captured["command"]
    assert "--max" in direct
    assert "-A" in direct
    assert "-O" not in direct
    assert direct[direct.index("-X") + 1] == "0"


def test_runtime_provenance_reads_version_from_stderr(monkeypatch) -> None:
    from backend.domain.common import trnascan_runtime

    class Completed:
        returncode = 0
        stdout = "Usage: tRNAscan-SE [-options] <FASTA file(s)>"
        stderr = "tRNAscan-SE 2.0.12 (Nov 2022)\nCopyright"

    monkeypatch.setattr(
        trnascan_runtime.subprocess,
        "run",
        lambda *args, **kwargs: Completed(),
    )
    trnascan_runtime.trnascan_runtime_provenance.cache_clear()
    try:
        assert trnascan_runtime.trnascan_runtime_provenance()["version"] == (
            "tRNAscan-SE 2.0.12 (Nov 2022)"
        )
    finally:
        trnascan_runtime.trnascan_runtime_provenance.cache_clear()
