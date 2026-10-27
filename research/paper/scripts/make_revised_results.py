#!/usr/bin/env python3
"""Build revised result tables and figures for the manuscript."""

from __future__ import annotations

import csv
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, patches


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The figure scripts sit under research/, while the application packages live in
# APP/ at the repository root.  Figure code imports the same region and
# segmentation helpers the website uses, so both roots go on the path.
REPO_ROOT = ROOT.parent
for _root in (REPO_ROOT, REPO_ROOT / "APP"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

TRNAVIZ_PROCESS = ROOT / "external_projects/tRNAviz/process-data"
if str(TRNAVIZ_PROCESS) not in sys.path:
    sys.path.insert(0, str(TRNAVIZ_PROCESS))

from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.preparation.segmentation import compare_input_template
from backend.domain.gold_curation.service import export_curated_tsv
try:
    from tRNA_position import annotate_positions
except ModuleNotFoundError:  # external tRNAviz checkout, not tracked in this repository
    def annotate_positions(ss_cons: str) -> list[str]:
        raise ModuleNotFoundError(
            "annotate_positions needs external_projects/tRNAviz/process-data/"
            "tRNA_position.py, which is not tracked in this repository."
        )


FULL_RUN_ID = "deepseek-v4-pro-no-thinking-full-v1"
FULL_RUN = ROOT / "benchmark/runs" / FULL_RUN_ID
NO_AGENT_RUN_ID = "no-agent-comparison-v1"
NO_AGENT_RUN = ROOT / "benchmark/runs" / NO_AGENT_RUN_ID
REPORT_DATA = ROOT / "benchmark/reports" / FULL_RUN_ID / "report_data.json"
# The curated gold standard lives in MySQL; these names are for a temporary
# offline export written by benchmark/scripts/export_run_from_store.py.
GOLD_EXPORT_DIR = REPO_ROOT / ".runtime" / "gold-export"
ORIGINAL_GOLD_TSV = GOLD_EXPORT_DIR / "mature_benchmark.tsv"
CURATED_GOLD_TSV = GOLD_EXPORT_DIR / "mature_benchmark.curated.tsv"
GOLD_TSV = CURATED_GOLD_TSV
CORRECTIONS_JSONL = GOLD_EXPORT_DIR / "manual_corrections.jsonl"
EXPORT_TSV = GOLD_EXPORT_DIR / "export.tsv"
TEMPLATE_TSV = REPO_ROOT / "data/tRNAs_flat.tsv"
AYLM_TSV = Path.home() / "AYLMV3/all_dataset/tRNAs_flat.tsv"
CM_PATH = ROOT / "external_projects/tRNAviz/engine/all-num.cm"
RFAM_TRNA_CM = ROOT / "downstream/alignment_probe/analysis/tmp_RF00005.cm"
LAYOUT_TS = REPO_ROOT / "APP/web/src/components/layout/sprinzlLayout.ts"
RESULTS_DIR = ROOT / "paper/results"
FIGURES_DIR = ROOT / "paper/figures"
SEQNAME_FILE = RESULTS_DIR / "comparison_seqnames.txt"
FIG03_MODEL_CACHE = RESULTS_DIR / "fig03_model_outputs.json"
FIG03_LLM_PROVIDER = "deepseek"
FIG03_LLM_MODEL = "deepseek-v4-pro"
FIG03_LLM_THINKING = "disabled"
FIG03_MODEL_CACHE_VERSION = 2
FIG03_LLM_RUNTIME_OVERRIDES = {
    "LLM_PROVIDER": FIG03_LLM_PROVIDER,
    "DEEPSEEK_MODEL": FIG03_LLM_MODEL,
    "DEEPSEEK_THINKING": FIG03_LLM_THINKING,
}
SIMSUN_PATH = Path.home() / ".local/share/fonts/aylm_fonts/SIMSUN.ttf"
ARIAL_REGULAR_FONT = Path.home() / ".local/share/fonts/aylm_fonts/Arial.TTF"
ARIAL_BOLD_FONT = Path.home() / ".local/share/fonts/aylm_fonts/Arialbd.TTF"
HEROS_REGULAR_FONT = Path("/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-regular.otf")
MAFFT_BIN = Path("/home/ouyangzhuo/.conda/envs/mafft/bin/mafft")

SOURCE_LABELS = {
    "Archaea": "古菌",
    "Bacteria": "细菌",
    "Cytosolic Eukaryote": "细胞质真核",
    "Mitochondrial": "线粒体",
    "Chloroplast": "叶绿体",
    "Virus": "病毒",
    "Cyanelle": "蓝小体",
    "UNKNOWN": "未标注",
}

SOURCE_LABELS_EN = {
    "Archaea": "Archaea",
    "Bacteria": "Bacteria",
    "Cytosolic Eukaryote": "Eukaryotes",
    "Mitochondrial": "Mitochondrial",
    "Chloroplast": "Chloroplast",
    "Virus": "Virus",
    "Cyanelle": "Cyanelle",
    "UNKNOWN": "Unannotated",
}

SOURCE_COLORS = {
    "Archaea": "#0F766E",
    "Bacteria": "#2563A6",
    "Cytosolic Eukaryote": "#B45309",
    "Mitochondrial": "#B42318",
    "Chloroplast": "#4D7C0F",
    "Virus": "#7C3AED",
    "Cyanelle": "#0891B2",
    "UNKNOWN": "#667085",
}

BIN_COLORS = {
    "<0.50": "#D0D5DD",
    "0.50--0.69": "#A9B8C8",
    "0.70--0.89": "#7AA6B8",
    ">=0.90": "#2E7D6D",
}

BASE_MATRIX_COLORS = {
    "A": "#F19CA3",
    "C": "#86C99A",
    "G": "#F3C06C",
    "U": "#86BCE8",
    "T": "#86BCE8",
    "N": "#CBD5E1",
    "-": "#E8ECF2",
}

HIGHLIGHT_STYLES = {
    "true_tail": {"face": "#DDEFE7", "edge": "#2E7D6D", "line": "-", "text": "#0F766E"},
    "terminal_like": {"face": "#FBE6C8", "edge": "#B76E00", "line": "--", "text": "#B76E00"},
    "v_region": {"face": "#E3F2E8", "edge": "#2E7D32", "line": "-", "text": "#14532D"},
    "v_stem": {"face": "#E3F2E8", "edge": "#2E7D32", "line": "-", "text": "#14532D"},
    "v_loop": {"face": "#FFF1D6", "edge": "#B76E00", "line": "-", "text": "#9A5B00"},
    "t_region": {"face": "#EAF1FB", "edge": "#3F6FA8", "line": "-", "text": "#1D4E89"},
    "reference_row": {"face": "#DBEAFE", "edge": "#2563A6", "line": "-", "text": "#2563A6"},
    "anticodon": {"face": "#FDEBE9", "edge": "#B42318", "line": "-", "text": "#9F1239"},
}

TYPICAL_SOURCES = ["Archaea", "Bacteria", "Cytosolic Eukaryote"]
SPRINZL_POSITIONS = [
    "-1",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "17a",
    "18",
    "19",
    "20",
    "20a",
    "20b",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31",
    "32",
    "33",
    "34",
    "35",
    "36",
    "37",
    "38",
    "39",
    "40",
    "41",
    "42",
    "43",
    "44",
    "45",
    "V11",
    "V12",
    "V13",
    "V14",
    "V15",
    "V16",
    "V17",
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V27",
    "V26",
    "V25",
    "V24",
    "V23",
    "V22",
    "V21",
    "46",
    "47",
    "48",
    "49",
    "50",
    "51",
    "52",
    "53",
    "54",
    "55",
    "56",
    "57",
    "58",
    "59",
    "60",
    "61",
    "62",
    "63",
    "64",
    "65",
    "66",
    "67",
    "68",
    "69",
    "70",
    "71",
    "72",
    "73",
    "74",
    "75",
    "76",
]

PAIR_LINKS = [
    ("1", "72"),
    ("2", "71"),
    ("3", "70"),
    ("4", "69"),
    ("5", "68"),
    ("6", "67"),
    ("7", "66"),
    ("10", "25"),
    ("11", "24"),
    ("12", "23"),
    ("13", "22"),
    ("27", "43"),
    ("28", "42"),
    ("29", "41"),
    ("30", "40"),
    ("31", "39"),
    ("49", "65"),
    ("50", "64"),
    ("51", "63"),
    ("52", "62"),
    ("53", "61"),
]

POSITION_LABEL_OFFSETS = {
    "17a": (-18, -13),
    "20a": (-8, 21),
    "20b": (18, 21),
    "34": (-32, 33),
    "35": (-20, 43),
    "36": (-8, 33),
    "V1": (16, 18),
    "V3": (0, 30),
    "V5": (21, -12),
}


def configure_style() -> None:
    for font_path in (SIMSUN_PATH, ARIAL_REGULAR_FONT, ARIAL_BOLD_FONT, HEROS_REGULAR_FONT):
        if font_path.exists():
            font_manager.fontManager.addfont(str(font_path))
    plt.rcParams.update(
        {
            "font.family": "SimSun",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.unicode_minus": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#D0D5DD",
            "xtick.color": "#667085",
            "ytick.color": "#344054",
        }
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fin:
        return [dict(row) for row in csv.DictReader(fin, delimiter="\t")]


def read_tsv_with_header(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as fin:
        reader = csv.DictReader(fin, delimiter="\t")
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def load_latest_corrections() -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not CORRECTIONS_JSONL.exists():
        return latest
    for line in CORRECTIONS_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        seqname = str(record.get("seqname") or "").strip()
        if seqname:
            latest[seqname] = record
    return latest


def excluded_seqnames() -> set[str]:
    return {
        seqname
        for seqname, record in load_latest_corrections().items()
        if str(record.get("status") or record.get("decision") or "").strip() == "excluded"
    }


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{100 * float(value):.{digits}f}"


def safe_base(value: Any) -> str:
    base = str(value or "").strip().upper().replace("T", "U")
    return base if base in {"A", "C", "G", "U", "N"} else "-"


def broad_source(kingdom: str) -> str:
    if kingdom in {"Archaea", "ARCHAE"}:
        return "Archaea"
    if kingdom in {"Bacteria", "EUBACT"}:
        return "Bacteria"
    if kingdom == "Eukaryota" or kingdom.startswith("CY "):
        return "Cytosolic Eukaryote"
    if kingdom.startswith("MI "):
        return "Mitochondrial"
    if kingdom == "CHLORO":
        return "Chloroplast"
    if kingdom == "CYANEL":
        return "Cyanelle"
    if kingdom == "VIRUS":
        return "Virus"
    return "UNKNOWN"


def parse_alignment(csv_raw: str) -> tuple[list[str], list[str], list[str]]:
    rows = list(csv.reader(io.StringIO(csv_raw or "")))
    if len(rows) < 3:
        return [], [], []
    return list(rows[0]), list(rows[1]), list(rows[2])


def residue_labels(numbering: list[str], sequence: list[str]) -> list[str]:
    labels: list[str] = []
    for label, base in zip(numbering, sequence):
        if safe_base(base) != "-":
            labels.append(str(label or "").strip())
    return labels


def load_gold() -> tuple[list[str], dict[str, dict[str, str]]]:
    # The curated TSV is a frozen benchmark artifact: reading it must never rewrite
    # it. Re-exporting merges the latest manual decisions, which would silently move
    # the reference under every figure and collapse the v39 run cohort, so it only
    # happens when the file is genuinely absent.
    if CURATED_GOLD_TSV.exists():
        gold_path = CURATED_GOLD_TSV
    elif CORRECTIONS_JSONL.exists():
        export_curated_tsv(CURATED_GOLD_TSV)
        gold_path = CURATED_GOLD_TSV
    else:
        gold_path = ORIGINAL_GOLD_TSV
    header, rows = read_tsv_with_header(gold_path)
    excluded = excluded_seqnames()
    return header[1:], {row["seqname"]: row for row in rows if row.get("seqname") not in excluded}


def gold_labels(columns: list[str], row: dict[str, str]) -> list[str]:
    return [col for col in columns if safe_base(row.get(col)) != "-"]


def gold_sequence(columns: list[str], row: dict[str, str]) -> str:
    return "".join(safe_base(row.get(col)) for col in columns if safe_base(row.get(col)) != "-")


def workflow_structure_pairs(cmp: dict[str, Any]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    offset = 0
    region_map = cmp.get("regions") or {}
    region_order = [
        "Aminoacyl arm 5' end",
        "D loop + D stem",
        "Anticodon loop + Anticodon stem",
        "Variable loop",
        "T loop + T stem",
        "Aminoacyl arm 3' end",
    ]
    for region in region_order:
        region_data = region_map.get(region) or {}
        seq = "".join(ch for ch in str(region_data.get("input_seq", "") or "") if not ch.isspace())
        struct = [ch for ch in str(region_data.get("input_struct", "") or "") if not ch.isspace()]
        stack: list[int] = []
        for local_idx, char in enumerate(struct, start=1):
            residue_idx = offset + local_idx
            if char in {">", "("}:
                stack.append(residue_idx)
            elif char in {"<", ")"} and stack:
                pairs.append((stack.pop(), residue_idx))
        offset += len(seq)
    return pairs


def structure_pairs_from_dotbracket(struct: str) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    stack: list[int] = []
    for residue_idx, char in enumerate((ch for ch in str(struct or "") if not ch.isspace()), start=1):
        if char in {">", "("}:
            stack.append(residue_idx)
        elif char in {"<", ")"} and stack:
            pairs.append((stack.pop(), residue_idx))
    return pairs


def direct_trnascan_structure(seqname: str, seq: str) -> str:
    with tempfile.TemporaryDirectory(prefix="llm_trnaalign_fig04_trnascan_") as tmp:
        tmpdir = Path(tmp)
        fasta = tmpdir / "input.fa"
        out = tmpdir / "trnascan.out"
        struct = tmpdir / "trnascan.struct"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", seqname)
        fasta.write_text(f">{safe_name}\n{seq.upper().replace('U', 'T')}\n", encoding="utf-8")
        subprocess.run(
            ["tRNAscan-SE", "-G", "--score", "0", "-o", str(out), "-f", str(struct), str(fasta)],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        for line in struct.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("Str:"):
                return line.split("Str:", 1)[1].strip()
    return ""


def load_export_meta() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in read_tsv(EXPORT_TSV):
        trna_id = str(row.get("tRNA ID") or "").strip()
        sprinzl_id = str(row.get("SprinzlID") or "").strip()
        seqname = f"{trna_id}|{sprinzl_id}" if sprinzl_id else trna_id
        row["source"] = broad_source(row.get("Kingdom", ""))
        if seqname:
            out[seqname] = row
    return out


def load_template_rows() -> dict[str, dict[str, str]]:
    _header, rows = read_tsv_with_header(TEMPLATE_TSV)
    return {row["seqname"]: row for row in rows}


def case_path(run_dir: Path, seqname: str) -> Path:
    import hashlib

    digest = hashlib.sha1(seqname.encode("utf-8")).hexdigest()[:16]
    return run_dir / "cases" / f"{digest}.json"


def load_case(run_dir: Path, seqname: str) -> dict[str, Any] | None:
    path = case_path(run_dir, seqname)
    if not path.exists():
        return None
    return read_json(path)


def parse_template_isotype(template_name: str) -> str:
    match = re.search(r"-([A-Za-z][A-Za-z0-9]*)([ACGTU]{3})$", template_name or "")
    return match.group(1) if match else ""


def reconstruct_template_similarity(
    columns: list[str],
    gold_row: dict[str, str],
    template_row: dict[str, str] | None,
) -> float | None:
    if template_row is None:
        return None
    overlap = 0
    match = 0
    for col in columns:
        target = safe_base(gold_row.get(col))
        templ = safe_base(template_row.get(col))
        if target != "-" and templ != "-":
            overlap += 1
            match += int(target == templ)
    return (match / overlap) if overlap else None


def eval_metrics(gold: list[str], pred: list[str]) -> dict[str, Any]:
    total = len(gold)
    label_correct = sum(1 for idx, label in enumerate(gold) if idx < len(pred) and pred[idx] == label)
    region_correct = 0
    for idx, label in enumerate(gold):
        if idx >= len(pred):
            continue
        try:
            region_correct += int(number_to_region(pred[idx]) == number_to_region(label))
        except Exception:
            pass

    def subset(labels: set[str]) -> tuple[int, int, bool | None]:
        idxs = [idx for idx, label in enumerate(gold) if label in labels]
        if not idxs:
            return 0, 0, None
        correct = sum(1 for idx in idxs if idx < len(pred) and pred[idx] == gold[idx])
        return len(idxs), correct, correct == len(idxs)

    tail_total, tail_correct, tail_exact = subset({"74", "75", "76"})
    n73_total, n73_correct, n73_exact = subset({"73"})
    return {
        "label_correct": label_correct,
        "label_total": total,
        "label_accuracy": (label_correct / total) if total else None,
        "exact_match": pred == gold,
        "region_correct": region_correct,
        "region_total": total,
        "region_accuracy": (region_correct / total) if total else None,
        "tail_accuracy": (tail_correct / tail_total) if tail_total else None,
        "tail_exact": tail_exact,
        "n73_accuracy": (n73_correct / n73_total) if n73_total else None,
        "n73_exact": n73_exact,
        "pred_count": len(pred),
        "gold_count": len(gold),
    }


def parse_case_csv(case: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    csv_content = case.get("csv_content") or ""
    rows = list(csv.reader(io.StringIO(csv_content)))
    if len(rows) < 3:
        return [], [], []
    return rows[0], rows[1], rows[2]


def gold_sequence_labels(columns: list[str], gold_row: dict[str, str]) -> list[str]:
    return [col for col in columns if safe_base(gold_row.get(col)) != "-"]


def predicted_sequence_labels(case: dict[str, Any]) -> list[str]:
    labels, _template, target = parse_case_csv(case)
    return [label for label, base in zip(labels, target) if safe_base(base) != "-"]


def load_sprinzl_positions() -> dict[str, tuple[float, float]]:
    text = LAYOUT_TS.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(?:"(?P<quoted>[-\w]+)"|(?P<bare>V\d+)):\s*\{\s*x:\s*(?P<x>[-\d.]+),\s*y:\s*(?P<y>[-\d.]+)\s*\}'
    )
    positions: dict[str, tuple[float, float]] = {}
    for match in pattern.finditer(text):
        key = match.group("quoted") or match.group("bare")
        positions[key] = (float(match.group("x")), float(match.group("y")))
    if not positions:
        raise RuntimeError(f"No Sprinzl positions parsed from {LAYOUT_TS}")
    return positions


def region_cn(label: str) -> str:
    try:
        region = number_to_region(label)
    except Exception:
        region = ""
    return {
        "Aminoacyl arm 5' end": "受体臂5'端",
        "D loop + D stem": "D区",
        "Anticodon loop + Anticodon stem": "反密码子区",
        "Variable loop": "可变区",
        "T loop + T stem": "T区",
        "Aminoacyl arm 3' end": "受体臂3'端",
    }.get(region, "可变区" if label.startswith("V") else region)


def build_position_accuracy(columns: list[str], gold_rows: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, int]] = {label: {"correct": 0, "total": 0} for label in SPRINZL_POSITIONS}
    for seqname, gold_row in gold_rows.items():
        case = load_case(FULL_RUN, seqname)
        if not case or case.get("status") != "ok":
            continue
        gold_labels = gold_sequence_labels(columns, gold_row)
        pred_labels = predicted_sequence_labels(case)
        for idx, gold_label in enumerate(gold_labels):
            if gold_label not in counts:
                continue
            counts[gold_label]["total"] += 1
            if idx < len(pred_labels) and pred_labels[idx] == gold_label:
                counts[gold_label]["correct"] += 1

    rows: list[dict[str, Any]] = []
    for label in SPRINZL_POSITIONS:
        correct = counts[label]["correct"]
        total = counts[label]["total"]
        rows.append(
            {
                "position": label,
                "region": region_cn(label),
                "correct": correct,
                "total": total,
                "accuracy": (correct / total) if total else "",
            }
        )
    return rows


def build_variable_error_example(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
) -> dict[str, Any]:
    preferred = os.environ.get("FIG05_VARIABLE_EXAMPLE", "tdbD00001537|DL1664")
    candidates = [preferred] + sorted(gold_rows)
    local_labels = {str(n) for n in range(26, 49)}
    for seqname in candidates:
        if seqname not in gold_rows:
            continue
        case = load_case(FULL_RUN, seqname)
        if not case or case.get("status") != "ok":
            continue
        metrics = case.get("metrics", {})
        if seqname != preferred:
            if float(metrics.get("variable_accuracy") or 1.0) > 0.05:
                continue
            if int(metrics.get("variable_total") or 0) < 12:
                continue
            if float(metrics.get("label_accuracy") or 0.0) < 0.65:
                continue
        gold_row = gold_rows[seqname]
        gold_labels = gold_sequence_labels(columns, gold_row)
        gold_bases = [safe_base(gold_row.get(col)) for col in columns if safe_base(gold_row.get(col)) != "-"]
        pred_labels = predicted_sequence_labels(case)
        label_total = len(gold_labels)
        label_correct = sum(1 for idx, label in enumerate(gold_labels) if idx < len(pred_labels) and pred_labels[idx] == label)
        variable_idxs = [idx for idx, label in enumerate(gold_labels) if str(label).startswith("V")]
        variable_correct = sum(1 for idx in variable_idxs if idx < len(pred_labels) and pred_labels[idx] == gold_labels[idx])
        seq = gold_sequence(columns, gold_row)
        annotation_cmp = compare_input_template(seq)
        direct_struct = direct_trnascan_structure(seqname, seq)
        workflow_pairs = structure_pairs_from_dotbracket(direct_struct) if direct_struct else (workflow_structure_pairs(annotation_cmp) if annotation_cmp else [])
        entries = []
        for idx, gold_label in enumerate(gold_labels):
            pred_label = pred_labels[idx] if idx < len(pred_labels) else ""
            if gold_label.startswith("V") or pred_label.startswith("V") or gold_label in local_labels or pred_label in local_labels:
                entries.append(
                    {
                        "residue_index": idx + 1,
                        "base": gold_bases[idx],
                        "gold_label": gold_label,
                        "pred_label": pred_label,
                        "correct": pred_label == gold_label,
                    }
                )
        return {
            "seqname": seqname,
            "entries": entries,
            "label_accuracy": (label_correct / label_total) if label_total else None,
            "variable_accuracy": (variable_correct / len(variable_idxs)) if variable_idxs else None,
            "variable_total": len(variable_idxs),
            "annotation_source": (annotation_cmp.get("annotation_meta") or {}).get("annotation_source", "") if annotation_cmp else "",
            "variable_struct": direct_struct or (((annotation_cmp.get("regions") or {}).get("Variable loop") or {}).get("input_struct", "") if annotation_cmp else ""),
            "pair_indices": workflow_pairs,
        }
    raise RuntimeError("No variable-region error example found")


def choose_comparison_seqnames(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
    *,
    per_source: int = 100,
) -> list[str]:
    rng = random.Random(20260514)
    by_source_aa: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for seqname, row in gold_rows.items():
        meta = export_meta.get(seqname)
        if not meta:
            continue
        source = meta.get("source", "")
        if source not in TYPICAL_SOURCES:
            continue
        if load_case(FULL_RUN, seqname) is None:
            continue
        aa = meta.get("Amino Acid", "unknown")
        by_source_aa[source][aa].append(seqname)

    selected: list[str] = []
    for source in TYPICAL_SOURCES:
        groups = {aa: values[:] for aa, values in by_source_aa[source].items()}
        for values in groups.values():
            rng.shuffle(values)
        aa_order = sorted(groups)
        rng.shuffle(aa_order)
        source_selected: list[str] = []
        while len(source_selected) < per_source and any(groups.values()):
            for aa in aa_order:
                if groups[aa]:
                    source_selected.append(groups[aa].pop())
                    if len(source_selected) >= per_source:
                        break
        selected.extend(source_selected)
    return selected


def ensure_comparison_seqnames(columns: list[str], gold_rows: dict[str, dict[str, str]], export_meta: dict[str, dict[str, str]]) -> list[str]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if SEQNAME_FILE.exists():
        names = [line.strip() for line in SEQNAME_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
        if names:
            return names
    names = choose_comparison_seqnames(columns, gold_rows, export_meta)
    SEQNAME_FILE.write_text("\n".join(names) + "\n", encoding="utf-8")
    return names


def write_comparison_fasta(seqnames: list[str], columns: list[str], gold_rows: dict[str, dict[str, str]], path: Path) -> dict[str, str]:
    id_to_seqname: dict[str, str] = {}
    with path.open("w", encoding="utf-8") as fout:
        for idx, seqname in enumerate(seqnames, start=1):
            sid = f"S{idx:04d}"
            id_to_seqname[sid] = seqname
            fout.write(f">{sid}\n{gold_sequence(columns, gold_rows[seqname])}\n")
    return id_to_seqname


def parse_stockholm(text: str) -> tuple[str, dict[str, str]]:
    ss_cons = ""
    seqs: dict[str, str] = defaultdict(str)
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#=GF") or line == "//":
            continue
        if line.startswith("#=GC SS_cons"):
            ss_cons += line.split()[-1]
            continue
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            seqs[parts[0]] += parts[1]
    return ss_cons, dict(seqs)


def labels_from_infernal_alignment(ss_cons: str, aligned_seq: str) -> list[str]:
    positions = annotate_positions(ss_cons)
    col_to_label: dict[int, str] = {}
    for pos in positions:
        sprinzl = str(pos.sprinzl)
        if getattr(pos, "paired", False):
            cols = [int(part) - 1 for part in str(pos.position).split(":")]
            labs = sprinzl.split(":")
            if len(cols) == 2 and len(labs) == 2:
                col_to_label[cols[0]] = labs[0]
                col_to_label[cols[1]] = labs[1]
        else:
            col_to_label[int(pos.position) - 1] = sprinzl

    labels: list[str] = []
    for idx, char in enumerate(aligned_seq):
        if safe_base(char) != "-":
            label = col_to_label.get(idx)
            if label:
                labels.append(label)
    return labels


def run_infernal(seqnames: list[str], columns: list[str], gold_rows: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    with tempfile.TemporaryDirectory(prefix="llm_trnaalign_infernal_") as tmp:
        fasta = Path(tmp) / "comparison.fa"
        id_to_seqname = write_comparison_fasta(seqnames, columns, gold_rows, fasta)
        proc = subprocess.run(
            ["cmalign", "-g", "--notrunc", str(CM_PATH), str(fasta)],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    ss_cons, aligned = parse_stockholm(proc.stdout)
    return {
        id_to_seqname[sid]: labels_from_infernal_alignment(ss_cons, seq)
        for sid, seq in aligned.items()
        if sid in id_to_seqname
    }


def run_trnascan(seqnames: list[str], columns: list[str], gold_rows: dict[str, dict[str, str]]) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="llm_trnaalign_trnascan_") as tmp:
        tmp_path = Path(tmp)
        fasta = tmp_path / "comparison.fa"
        out_path = tmp_path / "trnascan.out"
        id_to_seqname = write_comparison_fasta(seqnames, columns, gold_rows, fasta)
        subprocess.run(
            ["tRNAscan-SE", "-q", "-o", str(out_path), str(fasta)],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        calls: dict[str, str] = {}
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("Sequence") or line.startswith("Name") or line.startswith("-"):
                continue
            parts = line.split()
            if len(parts) >= 5 and parts[0] in id_to_seqname:
                calls[id_to_seqname[parts[0]]] = parts[4]
        return calls


def build_domain_similarity_tables(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
    template_rows: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, dict[str, Any]] = {
        source: {
            "n_analyzed": 0,
            "n_ok": 0,
            "n_error": 0,
            "label_correct": 0,
            "label_total": 0,
            "region_correct": 0,
            "region_total": 0,
            "exact": 0,
            "legality": 0,
        }
        for source in TYPICAL_SOURCES
    }
    points = []
    for seqname, gold_row in gold_rows.items():
        meta = export_meta.get(seqname, {})
        source = meta.get("source", "")
        if source not in TYPICAL_SOURCES:
            continue
        grouped[source]["n_analyzed"] += 1
        payload = load_case(FULL_RUN, seqname)
        if not payload or payload.get("status") != "ok":
            grouped[source]["n_error"] += 1
            continue
        gold = gold_sequence_labels(columns, gold_row)
        pred = predicted_sequence_labels(payload)
        metrics = eval_metrics(gold, pred)
        grouped[source]["n_ok"] += 1
        grouped[source]["label_correct"] += int(metrics["label_correct"])
        grouped[source]["label_total"] += int(metrics["label_total"])
        grouped[source]["region_correct"] += int(metrics["region_correct"])
        grouped[source]["region_total"] += int(metrics["region_total"])
        grouped[source]["exact"] += int(bool(metrics["exact_match"]))
        grouped[source]["legality"] += int(bool((payload.get("metrics") or {}).get("legality_passed")))
        template = payload.get("template_name") or (payload.get("agent") or {}).get("best_template") or ""
        similarity = reconstruct_template_similarity(columns, gold_row, template_rows.get(template))
        if similarity is None:
            continue
        points.append(
            {
                "seqname": seqname,
                "source": source,
                "source_cn": SOURCE_LABELS[source],
                "amino_acid": meta.get("Amino Acid", ""),
                "template_similarity": similarity,
                "label_accuracy": metrics.get("label_accuracy"),
                "label_correct": metrics.get("label_correct"),
                "label_total": metrics.get("label_total"),
                "exact_match": int(bool(metrics.get("exact_match"))),
                "region_accuracy": metrics.get("region_accuracy"),
                "legality_passed": int(bool((payload.get("metrics") or {}).get("legality_passed"))),
            }
        )
    domain_summary = []
    for source in TYPICAL_SOURCES:
        stats = grouped[source]
        n_ok = int(stats["n_ok"])
        domain_summary.append(
            {
                "source": source,
                "source_cn": SOURCE_LABELS[source],
                "n_analyzed": stats["n_analyzed"],
                "n_ok": n_ok,
                "n_error": stats["n_error"],
                "residue_label_accuracy": (stats["label_correct"] / stats["label_total"]) if stats["label_total"] else None,
                "exact_match_rate": (stats["exact"] / n_ok) if n_ok else None,
                "region_accuracy": (stats["region_correct"] / stats["region_total"]) if stats["region_total"] else None,
                "legality_pass_rate": (stats["legality"] / n_ok) if n_ok else None,
            }
        )
    return domain_summary, points


def build_isotype_summary(points: list[dict[str, Any]], min_n: int = 10) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in points:
        amino = str(row.get("amino_acid") or "").strip() or "unknown"
        grouped[(str(row["source"]), amino)].append(row)

    rows: list[dict[str, Any]] = []
    for (source, amino), bucket in sorted(grouped.items()):
        n = len(bucket)
        if n < min_n:
            continue
        correct = sum(int(float(row.get("label_correct") or 0)) for row in bucket)
        total = sum(int(float(row.get("label_total") or 0)) for row in bucket)
        if total == 0:
            continue
        rows.append(
            {
                "source": source,
                "source_cn": SOURCE_LABELS[source],
                "amino_acid": amino,
                "n": n,
                "label_accuracy": correct / total,
                "exact_match_rate": sum(int(row.get("exact_match") or 0) for row in bucket) / n,
            }
        )
    return rows


def build_domain_coverage(
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    for seqname in gold_rows:
        meta = export_meta.get(seqname)
        if not meta:
            continue
        source = meta.get("source", "")
        if source in TYPICAL_SOURCES:
            by_source[source].append(meta)

    rows: list[dict[str, Any]] = []
    for source in TYPICAL_SOURCES:
        records = by_source[source]
        by_species: dict[str, set[str]] = defaultdict(set)
        aa_by_species: dict[str, set[str]] = defaultdict(set)
        for meta in records:
            species = meta.get("Organism", "") or "unknown"
            amino = meta.get("Amino Acid", "") or "unknown"
            anticodon = (meta.get("Anticodon", "") or "").replace("T", "U")
            by_species[species].add(f"{amino}-{anticodon}")
            aa_by_species[species].add(amino)
        iso_counts = [len(values) for values in by_species.values()]
        aa_counts = [len(values) for values in aa_by_species.values()]
        rows.append(
            {
                "source": source,
                "source_cn": SOURCE_LABELS[source],
                "n_records": len(records),
                "n_species": len(by_species),
                "mean_iso_types_per_species": (sum(iso_counts) / len(iso_counts)) if iso_counts else 0,
                "median_iso_types_per_species": sorted(iso_counts)[len(iso_counts) // 2] if iso_counts else 0,
                "mean_amino_acids_per_species": (sum(aa_counts) / len(aa_counts)) if aa_counts else 0,
            }
        )
    return rows


def binned_similarity(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bins = [(0.0, 0.5, "<0.50"), (0.5, 0.7, "0.50--0.69"), (0.7, 0.9, "0.70--0.89"), (0.9, 1.01, ">=0.90")]
    rows = []
    for source in TYPICAL_SOURCES:
        source_points = [p for p in points if p["source"] == source]
        for low, high, label in bins:
            bucket = [p for p in source_points if low <= float(p["template_similarity"]) < high]
            if not bucket:
                continue
            rows.append(
                {
                    "source": source,
                    "source_cn": SOURCE_LABELS[source],
                    "similarity_bin": label,
                    "n": len(bucket),
                    "mean_similarity": sum(float(p["template_similarity"]) for p in bucket) / len(bucket),
                    "label_accuracy": sum(float(p["label_accuracy"]) for p in bucket) / len(bucket),
                    "exact_match_rate": sum(int(p["exact_match"]) for p in bucket) / len(bucket),
                }
            )
    return rows


def build_comparison_tables(
    seqnames: list[str],
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    infernal_labels = run_infernal(seqnames, columns, gold_rows)
    trnascan_calls = run_trnascan(seqnames, columns, gold_rows)
    rows: list[dict[str, Any]] = []

    for seqname in seqnames:
        gold = gold_labels(columns, gold_rows[seqname])
        meta = export_meta.get(seqname, {})
        gold_iso = meta.get("Amino Acid", "")

        for method, run_dir in [("完整流程", FULL_RUN), ("第一版候选表", NO_AGENT_RUN)]:
            payload = load_case(run_dir, seqname)
            if not payload or payload.get("status") != "ok":
                rows.append({"seqname": seqname, "method": method, "status": "missing"})
                continue
            numbering, _template, sequence = parse_alignment(payload.get("csv_content", ""))
            pred = residue_labels(numbering, sequence)
            metrics = eval_metrics(gold, pred)
            pred_iso = parse_template_isotype(payload.get("template_name", ""))
            rows.append(
                {
                    "seqname": seqname,
                    "source": meta.get("source", ""),
                    "source_cn": SOURCE_LABELS.get(meta.get("source", ""), meta.get("source", "")),
                    "gold_isotype": gold_iso,
                    "method": method,
                    "status": "ok",
                    "isotype_applicable": 1,
                    "isotype_correct": int(pred_iso == gold_iso),
                    "tail_exact": int(bool(metrics["tail_exact"])),
                    "tail_accuracy": metrics["tail_accuracy"],
                    "n73_exact": int(bool(metrics["n73_exact"])),
                    "region_accuracy": metrics["region_accuracy"],
                    "label_accuracy": metrics["label_accuracy"],
                    "exact_match": int(bool(metrics["exact_match"])),
                    "pred_count": metrics["pred_count"],
                    "gold_count": metrics["gold_count"],
                }
            )

        pred = infernal_labels.get(seqname, [])
        metrics = eval_metrics(gold, pred)
        rows.append(
            {
                "seqname": seqname,
                "source": meta.get("source", ""),
                "source_cn": SOURCE_LABELS.get(meta.get("source", ""), meta.get("source", "")),
                "gold_isotype": gold_iso,
                "method": "Infernal编号模型",
                "status": "ok" if pred else "missing",
                "isotype_applicable": 0,
                "isotype_correct": "",
                "tail_exact": int(bool(metrics["tail_exact"])),
                "tail_accuracy": metrics["tail_accuracy"],
                "n73_exact": int(bool(metrics["n73_exact"])),
                "region_accuracy": metrics["region_accuracy"],
                "label_accuracy": metrics["label_accuracy"],
                "exact_match": int(bool(metrics["exact_match"])),
                "pred_count": metrics["pred_count"],
                "gold_count": metrics["gold_count"],
            }
        )

        rows.append(
            {
                "seqname": seqname,
                "source": meta.get("source", ""),
                "source_cn": SOURCE_LABELS.get(meta.get("source", ""), meta.get("source", "")),
                "gold_isotype": gold_iso,
                "method": "tRNAscan-SE分类",
                "status": "ok" if seqname in trnascan_calls else "missing",
                "isotype_applicable": 1,
                "isotype_correct": int(trnascan_calls.get(seqname, "") == gold_iso),
                "tail_exact": "",
                "tail_accuracy": "",
                "n73_exact": "",
                "region_accuracy": "",
                "label_accuracy": "",
                "exact_match": "",
                "pred_count": "",
                "gold_count": len(gold),
            }
        )

    summary = []
    methods = ["完整流程", "第一版候选表", "Infernal编号模型", "tRNAscan-SE分类"]
    for method in methods:
        bucket = [r for r in rows if r["method"] == method and r.get("status") == "ok"]
        if not bucket:
            continue
        n = len(bucket)

        def mean_numeric(key: str) -> float | None:
            vals = [float(r[key]) for r in bucket if r.get(key) not in ("", None)]
            return (sum(vals) / len(vals)) if vals else None

        summary.append(
            {
                "method": method,
                "n": n,
                "isotype_accuracy": mean_numeric("isotype_correct"),
                "tail_exact_rate": mean_numeric("tail_exact"),
                "tail_accuracy": mean_numeric("tail_accuracy"),
                "n73_exact_rate": mean_numeric("n73_exact"),
                "region_accuracy": mean_numeric("region_accuracy"),
                "label_accuracy": mean_numeric("label_accuracy"),
                "exact_match_rate": mean_numeric("exact_match"),
            }
        )
    return rows, summary


def classify_case_error(error_text: str) -> str:
    if "tRNAscan-SE did not detect a tRNA hit" in error_text:
        return "tRNAscan no hit"
    if "sequence does not show a standard cloverleaf structure" in error_text:
        return "non-canonical cloverleaf"
    if "base-preservation check" in error_text:
        return "base-preservation failure"
    return "unknown"


def summarize_case_bucket(
    seqnames: list[str],
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
) -> dict[str, Any]:
    stats = {
        "n_analyzed": len(seqnames),
        "n_ok": 0,
        "n_error": 0,
        "label_correct": 0,
        "label_total": 0,
        "exact": 0,
        "legality": 0,
    }
    for seqname in seqnames:
        payload = load_case(FULL_RUN, seqname)
        if not payload or payload.get("status") != "ok":
            stats["n_error"] += 1
            continue
        gold = gold_sequence_labels(columns, gold_rows[seqname])
        pred = predicted_sequence_labels(payload)
        metrics = eval_metrics(gold, pred)
        stats["n_ok"] += 1
        stats["label_correct"] += int(metrics["label_correct"])
        stats["label_total"] += int(metrics["label_total"])
        stats["exact"] += int(bool(metrics["exact_match"]))
        stats["legality"] += int(bool((payload.get("metrics") or {}).get("legality_passed")))
    n_ok = int(stats["n_ok"])
    return {
        "n_analyzed": stats["n_analyzed"],
        "n_ok": n_ok,
        "n_error": stats["n_error"],
        "residue_label_accuracy": (stats["label_correct"] / stats["label_total"]) if stats["label_total"] else None,
        "exact_match_rate": (stats["exact"] / n_ok) if n_ok else None,
        "legality_pass_rate": (stats["legality"] / n_ok) if n_ok else None,
    }


def build_low_similarity_summary(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    template_rows: dict[str, dict[str, str]],
) -> dict[str, Any]:
    stats = {
        "n_ok": 0,
        "label_correct": 0,
        "label_total": 0,
        "exact": 0,
        "legality": 0,
    }
    for seqname, gold_row in gold_rows.items():
        payload = load_case(FULL_RUN, seqname)
        if not payload or payload.get("status") != "ok":
            continue
        template = payload.get("template_name") or (payload.get("agent") or {}).get("best_template") or ""
        similarity = reconstruct_template_similarity(columns, gold_row, template_rows.get(template))
        if similarity is None or similarity >= 0.50:
            continue
        gold = gold_sequence_labels(columns, gold_row)
        pred = predicted_sequence_labels(payload)
        metrics = eval_metrics(gold, pred)
        stats["n_ok"] += 1
        stats["label_correct"] += int(metrics["label_correct"])
        stats["label_total"] += int(metrics["label_total"])
        stats["exact"] += int(bool(metrics["exact_match"]))
        stats["legality"] += int(bool((payload.get("metrics") or {}).get("legality_passed")))
    n_ok = int(stats["n_ok"])
    return {
        "n_ok": n_ok,
        "residue_label_accuracy": (stats["label_correct"] / stats["label_total"]) if stats["label_total"] else None,
        "exact_match_rate": (stats["exact"] / n_ok) if n_ok else None,
        "legality_pass_rate": (stats["legality"] / n_ok) if n_ok else None,
    }


def build_boundary_table(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
    template_rows: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    by_source: dict[str, list[str]] = defaultdict(list)
    animal_mito: list[str] = []
    errors: dict[str, int] = defaultdict(int)
    for seqname in gold_rows:
        meta = export_meta.get(seqname, {})
        source = meta.get("source", "UNKNOWN") or "UNKNOWN"
        by_source[source].append(seqname)
        if meta.get("Kingdom") == "MI ANI":
            animal_mito.append(seqname)
        payload = load_case(FULL_RUN, seqname)
        if not payload or payload.get("status") != "ok":
            errors[classify_case_error(str((payload or {}).get("error") or ""))] += 1
    rows = []
    for key in ["Mitochondrial", "Chloroplast", "Virus", "Cyanelle"]:
        row = summarize_case_bucket(by_source.get(key, []), columns, gold_rows)
        rows.append(
            {
                "category": SOURCE_LABELS[key],
                "n_analyzed": row["n_analyzed"],
                "n_ok": row["n_ok"],
                "n_error": row["n_error"],
                "residue_label_accuracy": row["residue_label_accuracy"],
                "exact_match_rate": row["exact_match_rate"],
                "legality_pass_rate": row["legality_pass_rate"],
                "note": "来源分层",
            }
        )
    animal = summarize_case_bucket(animal_mito, columns, gold_rows)
    rows.append(
        {
            "category": "动物线粒体",
            "n_analyzed": animal["n_analyzed"],
            "n_ok": animal["n_ok"],
            "n_error": animal["n_error"],
            "residue_label_accuracy": animal["residue_label_accuracy"],
            "exact_match_rate": animal["exact_match_rate"],
            "legality_pass_rate": animal["legality_pass_rate"],
            "note": "线粒体子集",
        }
    )
    low_sim = build_low_similarity_summary(columns, gold_rows, template_rows)
    rows.append(
        {
            "category": "模板相似度 <0.50",
            "n_analyzed": "",
            "n_ok": low_sim["n_ok"],
            "n_error": "",
            "residue_label_accuracy": low_sim["residue_label_accuracy"],
            "exact_match_rate": low_sim["exact_match_rate"],
            "legality_pass_rate": low_sim["legality_pass_rate"],
            "note": "低模板相似度",
        }
    )
    rows.append(
        {
            "category": "明确失败样本",
            "n_analyzed": sum(errors.values()),
            "n_ok": "",
            "n_error": sum(errors.values()),
            "residue_label_accuracy": "",
            "exact_match_rate": "",
            "legality_pass_rate": "",
            "note": f"{errors.get('tRNAscan no hit', 0)} 未检出；{errors.get('non-canonical cloverleaf', 0)} 非典型；{errors.get('base-preservation failure', 0)} 碱基保持失败",
        }
    )
    return rows


def panel_label(ax: Any, letter: str) -> None:
    ax.text(-0.08, 1.06, letter, transform=ax.transAxes, fontsize=10, weight="bold", va="bottom", fontfamily="Arial")


def draw_domain_figure(
    domain_rows: list[dict[str, Any]],
    bin_rows: list[dict[str, Any]],
    coverage_rows: list[dict[str, Any]],
    domain_points: list[dict[str, Any]],
    isotype_rows: list[dict[str, Any]],
) -> None:
    fig = plt.figure(figsize=(7.4, 4.95))
    gs = fig.add_gridspec(2, 6, height_ratios=[0.95, 1.08], hspace=0.56, wspace=0.60)
    ax_a = fig.add_subplot(gs[0, 0:3])
    ax_b = fig.add_subplot(gs[0, 3:6])
    c_axes = [fig.add_subplot(gs[1, 0:2]), fig.add_subplot(gs[1, 2:4]), fig.add_subplot(gs[1, 4:6])]
    domain_palette = {
        "Archaea": "#15807A",
        "Bacteria": "#3F6FA8",
        "Cytosolic Eukaryote": "#BF6B21",
    }
    figure_font = "Arial" if ARIAL_REGULAR_FONT.exists() else "TeX Gyre Heros" if HEROS_REGULAR_FONT.exists() else "DejaVu Sans"
    number_font = figure_font

    row_by_source = {row["source"]: row for row in domain_rows}

    rng = random.Random(17)

    def style_boxplot(ax: Any, box: dict[str, Any], colors: list[str]) -> None:
        for patch, color in zip(box["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.22)
            patch.set_edgecolor(color)
            patch.set_linewidth(1.0)
        for median in box["medians"]:
            median.set_color("#101828")
            median.set_linewidth(1.0)
        for whisker in box["whiskers"]:
            whisker.set_color("#98A2B3")
            whisker.set_linewidth(0.8)
        for cap in box["caps"]:
            cap.set_color("#98A2B3")
            cap.set_linewidth(0.8)

    def draw_jitter(ax: Any, x: float, values: list[float], color: str, *, limit: int = 120, size: float = 9.0, alpha: float = 0.42) -> None:
        sampled = values[:]
        if len(sampled) > limit:
            sampled = rng.sample(sampled, limit)
        xs = [x + rng.uniform(-0.085, 0.085) for _ in sampled]
        ax.scatter(xs, sampled, s=size, color=color, alpha=alpha, edgecolor="none", zorder=3)

    def draw_floor_markers(ax: Any, x: float, count: int, color: str, floor: float) -> None:
        if count <= 0:
            return
        xs = [x + rng.uniform(-0.065, 0.065) for _ in range(count)]
        ys = [floor + 0.65 for _ in range(count)]
        ax.scatter(xs, ys, marker="v", s=13.0, color=color, alpha=0.72, edgecolor="white", linewidth=0.25, zorder=4)

    def soften_color(color: str, white_mix: float) -> tuple[float, float, float]:
        rgb = matplotlib.colors.to_rgb(color)
        return tuple((1.0 - white_mix) * channel + white_mix for channel in rgb)

    def finish_axis(ax: Any, ylim: tuple[float, float], yticks: list[float], ylabel: str | None = None, *, left_axis: bool = True) -> None:
        ax.set_ylim(*ylim)
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=7.0, fontfamily=figure_font)
        ax.set_yticks(yticks)
        ax.tick_params(axis="both", labelsize=6.4, length=2.4, width=0.6, pad=2, color="#111827", labelcolor="#344054")
        for tick_label in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
            tick_label.set_fontfamily(figure_font)
        ax.grid(False)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#111827")
        ax.spines["bottom"].set_linewidth(0.65)
        ax.spines["left"].set_visible(left_axis)
        if left_axis:
            ax.spines["left"].set_color("#111827")
            ax.spines["left"].set_linewidth(0.65)
        else:
            ax.tick_params(axis="y", left=False, labelleft=False)

    panel_label(ax_a, "A")
    metric_specs = [
        ("residue_label_accuracy", "Per-base accuracy", "#5B95C8", -0.15),
        ("exact_match_rate", "Full-sequence match", "#C9874A", 0.15),
    ]
    x_positions = [1, 2, 3]
    for source_idx, source in enumerate(TYPICAL_SOURCES, start=1):
        row = row_by_source[source]
        for key, label, color, offset in metric_specs:
            rate = 100 * float(row[key])
            ax_a.bar(
                source_idx + offset,
                rate,
                width=0.26,
                color=soften_color(color, 0.42),
                edgecolor=soften_color(color, 0.18),
                linewidth=0.65,
                zorder=2,
            )
            ax_a.text(
                source_idx + offset,
                rate + 0.55,
                f"{rate:.1f}",
                ha="center",
                va="bottom",
                fontsize=5.6,
                color="#101828",
                fontfamily=number_font,
                clip_on=False,
            )
    legend_handles = [
        patches.Patch(facecolor=soften_color(color, 0.42), edgecolor=soften_color(color, 0.18), label=label)
        for _key, label, color, _offset in metric_specs
    ]
    ax_a.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.50, 1.17),
        ncol=2,
        frameon=False,
        prop={"family": figure_font, "size": 5.5},
        handlelength=1.4,
        borderaxespad=0,
    )
    ax_a.set_xticks(x_positions)
    ax_a.set_xticklabels(
        [f"{SOURCE_LABELS_EN[source]}\nn={int(row_by_source[source]['n_ok'])}" for source in TYPICAL_SOURCES],
        fontsize=6.0,
        fontfamily=figure_font,
    )
    ax_a.set_xlabel("Source", fontsize=6.8, labelpad=2, fontfamily=figure_font)
    finish_axis(ax_a, (75, 101.5), [75, 80, 85, 90, 95, 100], "Accuracy / match rate (%)")

    panel_label(ax_b, "B")
    isotype_values = [
        [100 * float(row["label_accuracy"]) for row in isotype_rows if row["source"] == source]
        for source in TYPICAL_SOURCES
    ]
    source_colors = [domain_palette[source] for source in TYPICAL_SOURCES]
    box_b = ax_b.boxplot(isotype_values, positions=[1, 2, 3], widths=0.48, patch_artist=True, showfliers=False)
    style_boxplot(ax_b, box_b, source_colors)
    for idx, (source, values) in enumerate(zip(TYPICAL_SOURCES, isotype_values), start=1):
        draw_jitter(ax_b, idx, values, domain_palette[source], limit=40, size=9.0, alpha=0.52)
        ax_b.text(idx, 80.35, f"n={len(values)}", ha="center", va="bottom", fontsize=5.5, color="#667085", fontfamily=number_font)
    ax_b.set_xticks([1, 2, 3])
    ax_b.set_xticklabels([SOURCE_LABELS_EN[source] for source in TYPICAL_SOURCES], fontsize=6.0, fontfamily=figure_font)
    ax_b.set_xlabel("Source", fontsize=6.8, labelpad=2, fontfamily=figure_font)
    finish_axis(ax_b, (80, 101.5), [80, 85, 90, 95, 100], "Per-base accuracy (%)")

    sim_bins = [(0.0, 0.5, "<0.50"), (0.5, 0.7, "0.50-\n0.69"), (0.7, 0.9, "0.70-\n0.89"), (0.9, 1.01, "≥0.90")]
    for axis_index, (ax_c, source) in enumerate(zip(c_axes, TYPICAL_SOURCES)):
        if axis_index == 0:
            panel_label(ax_c, "C")
        color = domain_palette[source]
        visible_bins: list[tuple[str, list[float]]] = []
        for low, high, _label in sim_bins:
            values = [
                100 * float(row["label_accuracy"])
                for row in domain_points
                if row["source"] == source and low <= float(row["template_similarity"]) < high
            ]
            if values:
                visible_bins.append((_label, values))
        box_positions = [idx + 1 for idx, (_label, values) in enumerate(visible_bins) if len(values) >= 10]
        box_values = [values for _label, values in visible_bins if len(values) >= 10]
        if box_values:
            box_c = ax_c.boxplot(box_values, positions=box_positions, widths=0.48, patch_artist=True, showfliers=False, whis=(10, 90))
            style_boxplot(ax_c, box_c, [color] * len(box_values))
        for idx, (_label, values) in enumerate(visible_bins, start=1):
            ax_c.text(
                idx,
                101.45,
                f"n={len(values)}",
                ha="center",
                va="bottom",
                fontsize=4.7,
                color="#667085" if values else "#B8C0CC",
                fontfamily=number_font,
                clip_on=False,
            )
            visible_values = [value for value in values if value >= 60.0]
            floor_count = len(values) - len(visible_values)
            if visible_values:
                draw_jitter(ax_c, idx, visible_values, color, limit=55, size=4.0 if len(values) >= 10 else 13.0, alpha=0.20 if len(values) >= 10 else 0.68)
            draw_floor_markers(ax_c, idx, floor_count, color, 60.0)
        ax_c.set_title(SOURCE_LABELS_EN[source].replace("\n", " "), fontsize=7.0, pad=12, color="#344054", fontfamily=figure_font)
        ax_c.set_xlim(0.55, len(visible_bins) + 0.45)
        ax_c.set_xticks(list(range(1, len(visible_bins) + 1)))
        ax_c.set_xticklabels([label for label, _values in visible_bins], fontsize=4.9, rotation=0, ha="center", fontfamily=figure_font)
        if axis_index == 1:
            ax_c.set_xlabel("Best template similarity", fontsize=6.9, labelpad=2, fontfamily=figure_font)
        ylabel = "Per-base accuracy (%)" if axis_index == 0 else None
        finish_axis(ax_c, (60, 103.5), [60, 70, 80, 90, 100], ylabel, left_axis=(axis_index == 0))

    fig.savefig(FIGURES_DIR / "fig02_domain_benchmark.pdf", bbox_inches="tight")
    fig.savefig(FIGURES_DIR / "fig02_domain_benchmark.png", dpi=600, bbox_inches="tight")
    plt.close(fig)

def draw_comparison_figure(summary: list[dict[str, Any]]) -> None:
    fig = plt.figure(figsize=(7.4, 4.05))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.32, 0.92], wspace=0.36)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    panel_label(ax_a, "A")

    methods = ["完整流程", "第一版候选表", "Infernal编号模型", "tRNAscan-SE分类"]
    metrics = [
        ("isotype_accuracy", "受体\n类型"),
        ("tail_accuracy", "CCA尾"),
        ("region_accuracy", "结构\n区段"),
        ("label_accuracy", "逐碱基\n编号"),
        ("exact_match_rate", "整条\n匹配"),
    ]
    row_by_method = {row["method"]: row for row in summary}
    colorscale = plt.get_cmap("Blues")
    for i, method in enumerate(methods):
        for j, (key, _label) in enumerate(metrics):
            value = row_by_method.get(method, {}).get(key)
            if value in ("", None):
                face = "#F2F4F7"
                text = "NA"
                color = "#667085"
            else:
                face = colorscale(0.2 + 0.75 * float(value))
                text = f"{100 * float(value):.1f}"
                color = "white" if float(value) > 0.72 else "#1D2939"
            ax_a.add_patch(patches.Rectangle((j, i), 1, 1, facecolor=face, edgecolor="white", linewidth=1.2))
            ax_a.text(j + 0.5, i + 0.5, text, ha="center", va="center", fontsize=6.7, color=color)
    ax_a.set_xlim(0, len(metrics))
    ax_a.set_ylim(0, len(methods))
    ax_a.invert_yaxis()
    ax_a.set_xticks([idx + 0.5 for idx in range(len(metrics))])
    ax_a.set_xticklabels([label for _, label in metrics], fontsize=6.7, rotation=0, ha="center")
    ax_a.set_yticks([idx + 0.5 for idx in range(len(methods))])
    ax_a.set_yticklabels(methods, fontsize=7.0)
    ax_a.tick_params(length=0)
    for spine in ax_a.spines.values():
        spine.set_visible(False)

    panel_label(ax_b, "B")
    full = row_by_method["完整流程"]
    no_agent = row_by_method["第一版候选表"]
    delta_metrics = [
        ("tail_accuracy", "CCA尾"),
        ("region_accuracy", "结构区段"),
        ("label_accuracy", "逐碱基编号"),
        ("exact_match_rate", "整条完全匹配"),
    ]
    deltas = [100 * (float(full[key]) - float(no_agent[key])) for key, _ in delta_metrics]
    labels = [label for _, label in delta_metrics]
    colors = ["#0F766E" if val >= 0 else "#B42318" for val in deltas]
    ax_b.barh(range(len(labels)), deltas, color=colors, height=0.48)
    ax_b.axvline(0, color="#667085", linewidth=0.8)
    for idx, val in enumerate(deltas):
        ax_b.text(val + (0.25 if val >= 0 else -0.25), idx, f"{val:+.1f}", va="center", ha="left" if val >= 0 else "right", fontsize=6.7)
    ax_b.set_yticks(range(len(labels)))
    ax_b.set_yticklabels(labels, fontsize=6.8)
    ax_b.set_xlabel("完整流程 - 第一版候选表（百分点）", fontsize=6.8)
    ax_b.grid(axis="x", color="#EAECF0", linewidth=0.7)
    for spine in ax_b.spines.values():
        spine.set_visible(False)

    fig.subplots_adjust(bottom=0.16)
    fig.savefig(FIGURES_DIR / "fig03_baseline_comparison.pdf", bbox_inches="tight")
    fig.savefig(FIGURES_DIR / "fig03_baseline_comparison.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def row_sequence(row: dict[str, str], columns: list[str]) -> str:
    return "".join(safe_base(row.get(col)) for col in columns if safe_base(row.get(col)) != "-")


def row_sequence_and_labels(row: dict[str, str], columns: list[str]) -> tuple[str, list[str]]:
    seq: list[str] = []
    labels: list[str] = []
    for col in columns:
        base = safe_base(row.get(col))
        if base == "-":
            continue
        seq.append(base)
        labels.append(col)
    return "".join(seq), labels


def parse_seqname_type(seqname: str) -> tuple[str, str]:
    match = re.search(r"-([A-Za-z][A-Za-z0-9]*)([ACGTU]{3})$", seqname or "")
    if not match:
        return "unknown", "---"
    return match.group(1), match.group(2)


def anticodon_from_row(row: dict[str, str]) -> str:
    return "".join(safe_base(row.get(col)) for col in ["34", "35", "36"])


def normalize_dna(value: str) -> str:
    return str(value or "").strip().upper().replace("U", "T")


def load_aylm_flat() -> tuple[list[str], list[dict[str, str]]]:
    path = AYLM_TSV if AYLM_TSV.exists() else TEMPLATE_TSV
    return read_tsv_with_header(path)


def load_gtrnadb_flat() -> tuple[list[str], dict[str, dict[str, str]]]:
    header, rows = read_tsv_with_header(TEMPLATE_TSV)
    return header[1:], {row["seqname"]: row for row in rows}


def run_mafft(records: list[tuple[str, str]]) -> dict[str, str]:
    mafft = str(MAFFT_BIN if MAFFT_BIN.exists() else "mafft")
    with tempfile.TemporaryDirectory(prefix="llm_trnaalign_mafft_") as tmp:
        fasta = Path(tmp) / "input.fa"
        with fasta.open("w", encoding="utf-8") as fout:
            for name, seq in records:
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
                fout.write(f">{safe_name}\n{seq}\n")
        proc = subprocess.run(
            [mafft, "--quiet", str(fasta)],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    aligned: dict[str, str] = {}
    current = ""
    for line in proc.stdout.splitlines():
        if line.startswith(">"):
            current = line[1:].strip()
            aligned[current] = ""
        elif current:
                aligned[current] += line.strip().upper().replace("T", "U")
    return aligned


def run_infernal_cmalign(records: list[tuple[str, str]]) -> dict[str, str]:
    if not RFAM_TRNA_CM.exists():
        raise FileNotFoundError(f"Infernal CM not found: {RFAM_TRNA_CM}")
    with tempfile.TemporaryDirectory(prefix="llm_trnaalign_cmalign_") as tmp:
        fasta = Path(tmp) / "input.fa"
        out = Path(tmp) / "aligned.afa"
        with fasta.open("w", encoding="utf-8") as fout:
            for name, seq in records:
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
                fout.write(f">{safe_name}\n{seq.upper().replace('U', 'T')}\n")
        subprocess.run(
            [
                "cmalign",
                "--cpu",
                "1",
                "--outformat",
                "afa",
                "-o",
                str(out),
                str(RFAM_TRNA_CM),
                str(fasta),
            ],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        lines = out.read_text(encoding="utf-8").splitlines()
    aligned: dict[str, str] = {}
    current = ""
    for line in lines:
        if line.startswith(">"):
            current = line[1:].strip()
            aligned[current] = ""
        elif current:
            aligned[current] += line.strip().replace(".", "-").replace("~", "-").upper().replace("T", "U")
    return aligned


def terminal_suffix_class(seq: str) -> str:
    for suffix in ["CCCCCA", "CCCCA", "CCCA", "CCA", "CAA", "CA"]:
        if seq.endswith(suffix):
            return suffix
    return "other"


def gold_tail_class(row: dict[str, str]) -> str:
    return "".join(safe_base(row.get(col)) for col in ["74", "75", "76"])


def select_cca_records(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
) -> list[tuple[str, dict[str, str], dict[str, str], str, str, str]]:
    # Curated to make the CCA-tail contrast visible: true 74--76 CCA records
    # that the model places correctly, followed by no-tail records whose raw
    # terminal suffix is CCA-like and therefore misleading in family alignments.
    preferred = [
        "tdbD00000025|DA1110",
        "tdbD00003417|DX1140",
        "tdbD00000976|DH0660",
        "tdbD00005802|GH0006650",
        "tdbD00000022|DA0940",
        "tdbD00003272|DW0650",
        "tdbD00001555|DL2150",
        "tdbD00001159|DI1600",
        "tdbD00002093|DP0680",
        "tdbD00001122|DI0660",
        "tdbD00003521|DY1510",
        "tdbD00003425|DX1480",
        "tdbD00000513|DE0340",
    ]
    selected: list[tuple[str, dict[str, str], dict[str, str], str, str, str]] = []
    for seqname in preferred:
        row = gold_rows.get(seqname)
        if row is None:
            continue
        meta = export_meta.get(seqname, {})
        seq = row_sequence(row, columns)
        selected.append((seqname, row, meta, gold_tail_class(row), terminal_suffix_class(seq), seq[-10:]))
    if len(selected) == len(preferred):
        return selected

    # Fallback: classify by gold 74--76, not by raw string suffix, because body
    # residues can also end in CCA-like strings.
    by_class: dict[str, list[tuple[str, dict[str, str], dict[str, str], str, str, str]]] = defaultdict(list)
    for seqname, row in gold_rows.items():
        meta = export_meta.get(seqname, {})
        seq = row_sequence(row, columns)
        gold_tail = gold_tail_class(row)
        raw_tail = terminal_suffix_class(seq)
        if gold_tail == "CCA" or (gold_tail == "---" and raw_tail in {"CA", "CAA", "CCA", "CCCA", "CCCCA", "CCCCCA"}):
            by_class[f"{gold_tail}:{raw_tail}"].append((seqname, row, meta, gold_tail, raw_tail, seq[-10:]))
    for key, quota in [
        ("CCA:CCCCA", 2),
        ("CCA:CCCA", 1),
        ("CCA:CCA", 3),
        ("---:CCCCCA", 3),
        ("---:CCCCA", 3),
        ("---:CCCA", 2),
        ("---:CCA", 1),
        ("---:CAA", 1),
        ("---:CA", 1),
    ]:
        bucket = sorted(by_class.get(key, []), key=lambda item: (abs(len(row_sequence(item[1], columns)) - 76), item[0]))
        selected.extend(bucket[:quota])
    return selected


def select_variable_records(header: list[str], rows: list[dict[str, str]]) -> list[dict[str, str]]:
    vcols = [col for col in header if col.startswith("V")]

    def v_count(row: dict[str, str]) -> int:
        return sum(1 for col in vcols if safe_base(row.get(col)) != "-")

    hg19 = [row for row in rows if row.get("seqname", "").startswith("hg19")]
    long_v = sorted([row for row in hg19 if v_count(row) >= 8], key=lambda row: (-v_count(row), row["seqname"]))
    controls = sorted([row for row in hg19 if v_count(row) == 0], key=lambda row: row["seqname"])
    return (long_v[:12] + controls[:6])[:18]


def select_iso_records(header: list[str], rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups = [("Asn", "GTT"), ("Gln", "CTG"), ("His", "GTG"), ("Lys", "CTT")]
    by_group: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if not row.get("seqname", "").startswith("hg19"):
            continue
        aa, anticodon = parse_seqname_type(row["seqname"])
        key = (aa, anticodon)
        if key in groups:
            by_group[key].append(row)
    selected: list[dict[str, str]] = []
    for key in groups:
        bucket = sorted(by_group[key], key=lambda row: row["seqname"])
        selected.extend(bucket[:6])
    return selected


def display_anticodon(value: str) -> str:
    return str(value or "---").upper().replace("T", "U")


def record_display_label(seqname: str, meta: dict[str, str]) -> str:
    sprinzl_id = seqname.split("|")[-1]
    aa = meta.get("Amino Acid") or "tRNA"
    anticodon = display_anticodon(meta.get("Anticodon", ""))
    return f"{aa}-{anticodon} {sprinzl_id}"


def gtrnadb_alias(seqname: str) -> str:
    alias_map = {
        "eschColi": "E.coli",
        "hg19": "hg19",
        "mm10": "mm10",
        "sacCer3": "S.cer",
        "araTha1": "A.thal",
        "methVulc1": "M.vulc",
        "droSim1": "D.sim",
        "carnMalt": "C.malt",
    }
    for raw_prefix, alias in alias_map.items():
        if seqname.startswith(raw_prefix):
            prefix = raw_prefix
            break
    else:
        prefix = re.split(r"_chr|_plasmid|_scaffold|_supercont|_NC_|_DF|_KI|_JH", seqname, maxsplit=1)[0]
        alias = prefix[:10]
    trna_match = re.search(r"\.trna(\d+)", seqname)
    trna_id = f"t{trna_match.group(1)}" if trna_match else "tRNA"
    return f"{alias}-{trna_id}"


def gtrnadb_display_label(
    seqname: str,
    row: dict[str, str],
    columns: list[str],
    *,
    include_v: bool = False,
    trnascan: dict[str, Any] | None = None,
) -> str:
    aa, anticodon = parse_seqname_type(seqname)
    parts = [gtrnadb_alias(seqname), f"{aa}-{display_anticodon(anticodon)}"]
    if include_v:
        parts.append(f"V{v_position_count(row, columns)}")
    if trnascan:
        start = anticodon_start_index(row, columns)
        codon = display_anticodon(str(trnascan.get("codon") or ""))
        parts.append(f"34@{start}")
        parts.append(f"tSE:{codon}")
    return " ".join(parts)


def variable_display_label(seqname: str, row: dict[str, str], columns: list[str], meta: dict[str, str]) -> str:
    sprinzl_id = seqname.split("|")[-1]
    aa = meta.get("Amino Acid") or "tRNA"
    anticodon = display_anticodon(meta.get("Anticodon", ""))
    return f"{aa}-{anticodon} V{v_position_count(row, columns)} {sprinzl_id}"


def anticodon_display_label(seqname: str, row: dict[str, str], columns: list[str], meta: dict[str, str]) -> str:
    sprinzl_id = seqname.split("|")[-1]
    aa = meta.get("Amino Acid") or "tRNA"
    anticodon = display_anticodon(meta.get("Anticodon", ""))
    return f"{aa}-{anticodon} 34@{anticodon_start_index(row, columns)} {sprinzl_id}"


def labels_correct_for(
    seqname: str,
    row: dict[str, str],
    columns: list[str],
    required_labels: set[str],
) -> bool:
    case = load_case(FULL_RUN, seqname)
    if not case or case.get("status") != "ok":
        return False
    gold = gold_sequence_labels(columns, row)
    pred = predicted_sequence_labels(case)
    if len(gold) != len(pred):
        return False
    for residue_idx, label in enumerate(gold):
        if label in required_labels and pred[residue_idx] != label:
            return False
    return True


def labels_fraction_for(
    seqname: str,
    row: dict[str, str],
    columns: list[str],
    required_labels: set[str],
) -> tuple[int, int]:
    case = load_case(FULL_RUN, seqname)
    if not case or case.get("status") != "ok":
        return 0, 0
    gold = gold_sequence_labels(columns, row)
    pred = predicted_sequence_labels(case)
    if len(gold) != len(pred):
        return 0, 0
    total = 0
    correct = 0
    for residue_idx, label in enumerate(gold):
        if label in required_labels:
            total += 1
            if pred[residue_idx] == label:
                correct += 1
    return correct, total


def labels_sufficient_for(
    seqname: str,
    row: dict[str, str],
    columns: list[str],
    required_labels: set[str],
    *,
    min_fraction: float,
) -> bool:
    correct, total = labels_fraction_for(seqname, row, columns, required_labels)
    return bool(total) and correct / total >= min_fraction


def v_position_count(row: dict[str, str], columns: list[str]) -> int:
    return sum(1 for col in columns if col.startswith("V") and safe_base(row.get(col)) != "-")


def select_variable_coordinate_records(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
    variable_cols: list[str],
) -> list[tuple[str, dict[str, str], dict[str, str]]]:
    # Formal benchmark examples selected to make the coordinate-table advantage
    # visible: long Ser/Leu variable arms plus short-variable-region controls.
    # The archived FULL_RUN output must be high quality in the displayed V/T
    # coordinates; otherwise a visually dramatic record is not used here.
    preferred = [
        "tdbD00009944|GS0004140",
        "tdbD00010004|GS0004900",
        "tdbD00009617|GS0000461",
        "tdbD00010197|GS0007068",
        "tdbD00007203|GL0007153",
        "tdbD00006624|GL0001770",
        "tdbD00007048|GL0005746",
        "tdbD00006460|GL0000261",
        "tdbD00002634|DS1280",
    ]
    required = set(variable_cols)
    t_required = {col for col in required if col.isdigit() and 46 <= int(col) <= 53}
    selected: list[tuple[str, dict[str, str], dict[str, str]]] = []
    for seqname in preferred:
        row = gold_rows.get(seqname)
        if row is None:
            continue
        if not labels_sufficient_for(seqname, row, columns, required, min_fraction=0.88):
            continue
        if not labels_sufficient_for(seqname, row, columns, t_required, min_fraction=0.85):
            continue
        selected.append((seqname, row, export_meta.get(seqname, {})))
    if len(selected) >= 7:
        return selected

    candidates: list[tuple[int, str, str, str, str]] = []
    for seqname, row in gold_rows.items():
        meta = export_meta.get(seqname, {})
        if meta.get("source") != "Bacteria":
            continue
        if meta.get("Amino Acid") not in {"Leu", "Ser"}:
            continue
        if not labels_sufficient_for(seqname, row, columns, required, min_fraction=0.88):
            continue
        candidates.append((-v_position_count(row, columns), meta.get("Amino Acid", ""), meta.get("Anticodon", ""), seqname, seqname))
    for _neg_v, _aa, _anticodon, seqname, _ in sorted(candidates):
        if seqname in {item[0] for item in selected}:
            continue
        selected.append((seqname, gold_rows[seqname], export_meta.get(seqname, {})))
        if len(selected) >= 9:
            break
    return selected


def anticodon_start_index(row: dict[str, str], columns: list[str]) -> int:
    labels = gold_sequence_labels(columns, row)
    try:
        return labels.index("34") + 1
    except ValueError:
        return 0


def load_fig03_model_cache() -> dict[str, dict[str, Any]]:
    if not FIG03_MODEL_CACHE.exists():
        return {}
    try:
        raw = json.loads(FIG03_MODEL_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}


def save_fig03_model_cache(cache: dict[str, dict[str, Any]]) -> None:
    FIG03_MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    FIG03_MODEL_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def fig03_cache_runtime_matches(cached: dict[str, Any]) -> bool:
    return (
        cached.get("llm_provider") == FIG03_LLM_PROVIDER
        and cached.get("llm_model") == FIG03_LLM_MODEL
        and cached.get("deepseek_thinking") == FIG03_LLM_THINKING
        and cached.get("use_llm") is True
        and cached.get("llm_reasoning") is False
        and cached.get("cache_version") == FIG03_MODEL_CACHE_VERSION
    )


def run_with_fig03_llm_runtime(callable_obj: Any, *args: Any, **kwargs: Any) -> Any:
    old_values = {key: os.environ.get(key) for key in FIG03_LLM_RUNTIME_OVERRIDES}
    try:
        for key, value in FIG03_LLM_RUNTIME_OVERRIDES.items():
            os.environ[key] = str(value)
        return callable_obj(*args, **kwargs)
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def model_case_for_external_record(
    seqname: str,
    row: dict[str, str],
    columns: list[str],
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    input_seq = row_sequence(row, columns)
    cached = cache.get(seqname)
    if (
        cached
        and cached.get("status") == "ok"
        and cached.get("input_seq") == input_seq
        and cached.get("csv_content")
        and fig03_cache_runtime_matches(cached)
    ):
        return {
            "status": "ok",
            "seqname": seqname,
            "input_seq": input_seq,
            "csv_content": cached.get("csv_content", ""),
            "template_name": cached.get("template_name", ""),
            "agent": {
                "route": cached.get("route", ""),
                "difficulty_tier": cached.get("difficulty_tier", ""),
                "best_template": cached.get("template_name", ""),
            },
        }

    from backend.application.alignment.alignment_wrapper import perform_full_alignment_with_meta

    template_name, csv_content, warnings, meta = run_with_fig03_llm_runtime(
        perform_full_alignment_with_meta,
        input_seq,
        "/dev/null",
        anticode="",
        use_llm=True,
        llm_reasoning=False,
        template_name=None,
    )
    agent = dict((meta or {}).get("agent_v2") or {}) if isinstance(meta, dict) else {}
    cache[seqname] = {
        "status": "ok",
        "input_seq": input_seq,
        "template_name": template_name,
        "csv_content": csv_content,
        "warnings": list(warnings or []),
        "route": agent.get("route", ""),
        "difficulty_tier": agent.get("difficulty_tier", ""),
        "selected_template_name": agent.get("selected_template_name", ""),
        "use_llm": True,
        "llm_reasoning": False,
        "llm_provider": FIG03_LLM_PROVIDER,
        "llm_model": FIG03_LLM_MODEL,
        "deepseek_thinking": FIG03_LLM_THINKING,
        "cache_version": FIG03_MODEL_CACHE_VERSION,
    }
    return {
        "status": "ok",
        "seqname": seqname,
        "input_seq": input_seq,
        "csv_content": csv_content,
        "template_name": template_name,
        "agent": agent,
    }


def labels_fraction_for_case(
    case: dict[str, Any],
    row: dict[str, str],
    columns: list[str],
    required_labels: set[str],
) -> tuple[int, int]:
    if not case or case.get("status") != "ok":
        return 0, 0
    gold = gold_sequence_labels(columns, row)
    pred = predicted_sequence_labels(case)
    if len(gold) != len(pred):
        return 0, 0
    total = 0
    correct = 0
    for residue_idx, label in enumerate(gold):
        if label in required_labels:
            total += 1
            if pred[residue_idx] == label:
                correct += 1
    return correct, total


def run_trnascan_general(records: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    if not records:
        return {}
    with tempfile.TemporaryDirectory(prefix="llm_trnaalign_trnascan_") as tmp:
        tmpdir = Path(tmp)
        fasta = tmpdir / "input.fa"
        out = tmpdir / "trnascan.out"
        struct = tmpdir / "trnascan.struct"
        with fasta.open("w", encoding="utf-8") as fout:
            for name, seq in records:
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
                fout.write(f">{safe_name}\n{seq.upper().replace('U', 'T')}\n")
        subprocess.run(
            ["tRNAscan-SE", "-G", "--score", "0", "-o", str(out), "-f", str(struct), str(fasta)],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        out_text = out.read_text(encoding="utf-8") if out.exists() else ""
        struct_text = struct.read_text(encoding="utf-8") if struct.exists() else ""

    safe_to_name = {re.sub(r"[^A-Za-z0-9_.-]", "_", name): name for name, _seq in records}
    hits: dict[str, dict[str, Any]] = {}
    for line in out_text.splitlines():
        if not line.strip() or line.startswith(("Sequence", "Name", "-")):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        safe_name = parts[0]
        original = safe_to_name.get(safe_name, safe_name)
        hits[original] = {
            "type": parts[4],
            "codon": normalize_dna(parts[5]),
            "score": parts[8],
        }
    current: str | None = None
    for line in struct_text.splitlines():
        header = re.match(r"^(\S+)\.trna\d+\s+\(", line)
        if header:
            current = safe_to_name.get(header.group(1), header.group(1))
            continue
        if current and "Anticodon:" in line:
            match = re.search(r"Anticodon:\s+([ACGTU]+)\s+at\s+(\d+)-(\d+)", line)
            if match:
                hit = hits.setdefault(current, {})
                hit["codon"] = normalize_dna(match.group(1))
                hit["anticodon_start"] = int(match.group(2))
                hit["anticodon_end"] = int(match.group(3))
    return hits


def select_gtrnadb_variable_records(
    columns: list[str],
    gtrnadb_rows: dict[str, dict[str, str]],
    cache: dict[str, dict[str, Any]],
    variable_cols: list[str],
) -> tuple[list[tuple[str, dict[str, str], dict[str, Any]]], dict[str, dict[str, Any]]]:
    preferred = [
        "eschColi_chr.trna64-SerGCT",
        "eschColi_chr.trna73-SerCGA",
        "eschColi_chr.trna84-SerGGA",
        "eschColi_chr.trna81-LeuTAA",
        "hg19_chr6.trna85-LeuTAA",
        "hg19_chr6.trna32-SerGCT",
        "araTha1_chr2.trna10-SerGGA",
        "sacCer3_chrXIV.trna9-LeuTAA",
    ]
    required = {col for col in variable_cols if col.startswith("V") or (col.isdigit() and 46 <= int(col) <= 53)}
    selected: list[tuple[str, dict[str, str], dict[str, Any]]] = []
    model_cases: dict[str, dict[str, Any]] = {}
    for seqname in preferred:
        row = gtrnadb_rows.get(seqname)
        if row is None:
            continue
        case = model_case_for_external_record(seqname, row, columns, cache)
        correct, total = labels_fraction_for_case(case, row, columns, required)
        if total and correct / total >= 0.85:
            selected.append((seqname, row, {"source_database": "GtRNAdb"}))
            model_cases[seqname] = case
    return selected, model_cases


def select_gtrnadb_anticodon_records(
    columns: list[str],
    gtrnadb_rows: dict[str, dict[str, str]],
    cache: dict[str, dict[str, Any]],
) -> tuple[list[tuple[str, dict[str, str], dict[str, Any]]], dict[str, dict[str, Any]]]:
    preferred = [
        "droSim1_chr2R.trna40-AlaCGC",
        "carnMalt_LMA28_chr.trna44-IleGAT",
        "eschColi_chr.trna63-GlyCCC",
        "eschColi_chr.trna31-PheGAA",
        "eschColi_chr.trna65-ArgACG",
        "sacCer3_chrIV.trna10-TyrGTA",
        "methVulc1_chr.trna11-iMetCAT",
        "methVulc1_chr.trna12-LeuTAA",
    ]
    rows = [(seqname, gtrnadb_rows[seqname]) for seqname in preferred if seqname in gtrnadb_rows]
    trnascan = run_trnascan_general([(seqname, row_sequence(row, columns)) for seqname, row in rows])
    selected: list[tuple[str, dict[str, str], dict[str, Any]]] = []
    model_cases: dict[str, dict[str, Any]] = {}
    for seqname, row in rows:
        evidence = dict(trnascan.get(seqname) or {})
        if not evidence:
            continue
        if normalize_dna(evidence.get("codon", "")) != normalize_dna(anticodon_from_row(row)):
            continue
        case = model_case_for_external_record(seqname, row, columns, cache)
        correct, total = labels_fraction_for_case(case, row, columns, {"34", "35", "36"})
        if total != 3 or correct != 3:
            continue
        aa, anticodon = parse_seqname_type(seqname)
        evidence.update(
            {
                "source_database": "GtRNAdb",
                "amino_acid": aa,
                "anticodon": display_anticodon(anticodon),
                "model_anticodon": anticodon_from_row(row),
                "model_anticodon_start": anticodon_start_index(row, columns),
            }
        )
        selected.append((seqname, row, evidence))
        model_cases[seqname] = case
    return selected, model_cases


def select_anticodon_coordinate_records(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
    anticodon_cols: list[str],
) -> list[tuple[str, dict[str, str], dict[str, str]]]:
    # Selected from the formal benchmark to cover different amino-acid and
    # anticodon types whose raw anticodon starts span several sequence indices.
    preferred = [
        "tdbD00004969|GF0001936",
        "tdbD00004070|GA0007081",
        "tdbD00004195|GC0002097",
        "tdbD00000539|DE1541",
        "tdbD00009299|GR0005159",
        "tdbD00001936|DN1150",
        "tdbD00002460|DR2002",
        "tdbD00010017|GS0005044",
    ]
    required = set(anticodon_cols)
    selected: list[tuple[str, dict[str, str], dict[str, str]]] = []
    for seqname in preferred:
        row = gold_rows.get(seqname)
        if row is None or not labels_correct_for(seqname, row, columns, required):
            continue
        selected.append((seqname, row, export_meta.get(seqname, {})))
    if len(selected) >= 8:
        return selected

    candidates: list[tuple[int, str, str, str]] = []
    used_groups: set[tuple[str, str]] = set()
    for seqname, row in gold_rows.items():
        meta = export_meta.get(seqname, {})
        if meta.get("source") != "Bacteria":
            continue
        if not labels_correct_for(seqname, row, columns, required):
            continue
        aa = meta.get("Amino Acid", "")
        anticodon = meta.get("Anticodon", "")
        group = (aa, anticodon)
        if group in used_groups:
            continue
        used_groups.add(group)
        candidates.append((anticodon_start_index(row, columns), aa, anticodon, seqname))
    for _start, _aa, _anticodon, seqname in sorted(candidates, key=lambda item: (item[0], item[1], item[2])):
        if seqname in {item[0] for item in selected}:
            continue
        selected.append((seqname, gold_rows[seqname], export_meta.get(seqname, {})))
        if len(selected) >= 10:
            break
    return selected


def alignment_window(aligned: dict[str, str], *, mode: str, width: int = 30) -> tuple[int, list[str]]:
    if not aligned:
        return 0, []
    seqs = list(aligned.values())
    length = len(seqs[0])
    width = min(width, length)
    if mode == "tail":
        start = max(0, length - width)
    else:
        lo = max(0, int(length * 0.25))
        hi = max(lo, min(length - width, int(length * 0.80)))
        best_start = lo
        best_score = -1
        for start_idx in range(lo, hi + 1):
            window = [seq[start_idx : start_idx + width] for seq in seqs]
            gap_count = sum(fragment.count("-") for fragment in window)
            transitions = sum(
                1
                for col in range(width)
                if len({fragment[col] for fragment in window if fragment[col] != "-"}) >= 3
            )
            score = gap_count + 0.5 * transitions
            if score > best_score:
                best_score = score
                best_start = start_idx
        start = best_start
    return start, [seq[start : start + width] for seq in seqs]


def draw_base_grid(
    ax: Any,
    matrix: list[list[str]],
    col_labels: list[str],
    *,
    row_labels: list[str] | None = None,
    title: str = "",
    highlight_cols: set[str] | None = None,
    group_labels: list[str] | None = None,
    left_extent: float | None = None,
) -> None:
    highlight_cols = highlight_cols or set()
    nrows = len(matrix)
    ncols = len(col_labels)
    if left_extent is None:
        left_extent = -3.8 if row_labels or group_labels else 0
    ax.set_xlim(left_extent, ncols)
    ax.set_ylim(nrows, -1.25)
    ax.set_aspect("auto")
    ax.tick_params(length=0)
    for row_idx, row in enumerate(matrix):
        for col_idx, base in enumerate(row):
            label = col_labels[col_idx]
            face = BASE_MATRIX_COLORS.get(safe_base(base), "#E8ECF2")
            edge = "#FFFFFF"
            lw = 0.30
            if label in highlight_cols:
                edge = "#2E7D6D"
                lw = 0.72
            ax.add_patch(patches.Rectangle((col_idx, row_idx), 1, 1, facecolor=face, edgecolor=edge, linewidth=lw))
            if ncols <= 30 and nrows <= 24:
                ax.text(col_idx + 0.5, row_idx + 0.53, safe_base(base), ha="center", va="center", fontsize=4.55, color="#101828")
    if row_labels:
        for idx, label in enumerate(row_labels):
            ax.text(-0.18, idx + 0.5, label, ha="right", va="center", fontsize=4.9, color="#344054")
    if group_labels:
        palette = ["#0F766E", "#2563A6", "#B45309", "#7C3AED", "#64748B"]
        unique = {name: palette[idx % len(palette)] for idx, name in enumerate(dict.fromkeys(group_labels))}
        marker_x = left_extent + 0.25
        text_x = left_extent + 0.55
        for idx, name in enumerate(group_labels):
            ax.add_patch(patches.Rectangle((marker_x, idx + 0.12), 0.22, 0.76, facecolor=unique[name], edgecolor="none"))
        last = None
        start = 0
        for idx, name in enumerate(group_labels + ["END"]):
            if last is None:
                last = name
            if name != last:
                y = (start + idx) / 2
                ax.text(text_x, y, last, ha="left", va="center", fontsize=5.1, color="#344054")
                if idx < len(group_labels):
                    ax.plot([marker_x, ncols], [idx, idx], color="#D0D5DD", linewidth=0.45)
                start = idx
                last = name
    ax.set_xticks([i + 0.5 for i in range(ncols)])
    ax.set_xticklabels(col_labels, fontsize=4.9, rotation=90)
    ax.xaxis.tick_top()
    ax.set_yticks([])
    if title:
        ax.text(0, -0.78, title, ha="left", va="center", fontsize=6.3, color="#101828", weight="bold")
    for spine in ax.spines.values():
        spine.set_visible(False)


def draw_msa_text_block(
    ax: Any,
    row_labels: list[str],
    sequences: list[str],
    *,
    title: str,
    note: str = "",
    highlight_ranges: dict[int, list[tuple[int, int, str]]] | None = None,
    row_label_styles: dict[int, dict[str, Any]] | None = None,
    row_step: float = 0.76,
    fontsize: float = 4.4,
    char_spacing: float = 1.08,
    column_window: tuple[int, int] | None = None,
    fade_cols: int = 0,
    fade_left: bool = True,
    fade_right: bool = True,
    ruler: list[tuple[int, str]] | None = None,
) -> None:
    """Render one aligned text block.

    ``column_window`` restricts the drawn character range to ``[start, end)`` so a
    narrow region of interest can be shown without the surrounding alignment.
    Highlight ranges are shifted into the new coordinate system. ``fade_cols``
    whitens that many columns at each cut edge to signal truncation, and ``ruler``
    draws ``(column, label)`` ticks above the block.
    """
    if column_window is not None:
        win_start, win_end = column_window
        sequences = [seq[win_start:win_end] for seq in sequences]
        shifted: dict[int, list[tuple[int, int, str]]] = {}
        for row_idx, spans in (highlight_ranges or {}).items():
            row_spans: list[tuple[int, int, str]] = []
            for span_start, span_end, kind in spans:
                lo = max(span_start, win_start)
                hi = min(span_end, win_end)
                if lo < hi:
                    row_spans.append((lo - win_start, hi - win_start, kind))
            if row_spans:
                shifted[row_idx] = row_spans
        highlight_ranges = shifted

    label_width = max((len(label) for label in row_labels), default=0)
    max_width = max((len(seq) for seq in sequences), default=0)
    seq_x = label_width + 2
    line_width = seq_x + max(0, max_width - 1) * char_spacing + 1
    y_title = -0.72
    ax.set_xlim(-1.0, line_width + 1.0)
    ax.set_ylim(max(0, len(sequences) - 1) * row_step + 0.72, -0.98)
    ax.set_aspect("auto")
    ax.axis("off")

    ax.text(0, y_title, title, ha="left", va="center", fontsize=6.5, color="#101828", weight="bold", fontfamily="Arial")
    if note:
        ax.text(28.0, y_title, note, ha="left", va="center", fontsize=5.1, color="#475467", fontfamily="Arial")

    for row_idx, (label, seq) in enumerate(zip(row_labels, sequences)):
        y = row_idx * row_step + 0.22
        row_style = (row_label_styles or {}).get(row_idx, {})
        prefix = row_style.get("prefix")
        if prefix:
            ax.text(
                -0.55,
                y,
                prefix,
                ha="right",
                va="center",
                fontsize=fontsize,
                color=row_style.get("prefix_color", row_style.get("color", "#344054")),
                fontfamily="DejaVu Sans Mono",
                fontweight=row_style.get("weight", "normal"),
                clip_on=False,
            )
        marker_color = row_style.get("marker_color")
        if marker_color:
            marker_height = min(0.50, row_step * 0.70)
            ax.add_patch(
                patches.Rectangle(
                    (-0.72, y - 0.50 * marker_height),
                    0.18,
                    marker_height,
                    facecolor=marker_color,
                    edgecolor="none",
                    linewidth=0,
                    zorder=1,
                    clip_on=False,
                )
            )
        ax.text(
            0,
            y,
            f"{label:<{label_width}}",
            ha="left",
            va="center",
            fontsize=fontsize,
            color=row_style.get("color", "#344054"),
            fontfamily="DejaVu Sans Mono",
            fontweight=row_style.get("weight", "normal"),
        )
        for start, end, kind in (highlight_ranges or {}).get(row_idx, []):
            style = HIGHLIGHT_STYLES.get(kind, HIGHLIGHT_STYLES["true_tail"])
            n_bases = max(1, end - start)
            highlight_height = min(0.56, row_step * 0.70)
            ax.add_patch(
                patches.Rectangle(
                    (seq_x + start * char_spacing - 0.50 * char_spacing, y - 0.50 * highlight_height),
                    n_bases * char_spacing,
                    highlight_height,
                    facecolor=style["face"],
                    edgecolor=style["edge"],
                    linewidth=0.45,
                    linestyle=style["line"],
                    zorder=0,
                )
            )
        highlighted_cols: dict[int, str] = {}
        for start, end, kind in (highlight_ranges or {}).get(row_idx, []):
            for col_idx in range(start, end):
                highlighted_cols[col_idx] = kind
        for col_idx, base in enumerate(seq):
            kind = highlighted_cols.get(col_idx)
            color = HIGHLIGHT_STYLES.get(kind, {}).get("text", "#344054") if kind else "#344054"
            ax.text(seq_x + col_idx * char_spacing, y, safe_base(base), ha="center", va="center", fontsize=fontsize, color=color, fontfamily="DejaVu Sans Mono", zorder=2)

    n_rows = len(sequences)
    block_top = 0.22 - 0.5 * row_step
    block_bottom = (n_rows - 1) * row_step + 0.22 + 0.5 * row_step

    if ruler:
        ruler_y = block_top - 0.30
        for col_idx, text in ruler:
            if col_idx < 0 or col_idx >= max_width:
                continue
            x = seq_x + col_idx * char_spacing
            ax.text(
                x, ruler_y, text, ha="center", va="bottom", fontsize=fontsize * 0.96,
                color="#667085", fontfamily="Arial", zorder=4,
            )
            ax.vlines(
                x, ruler_y + 0.06, block_top - 0.04,
                color="#D0D5DD", linewidth=0.4, zorder=1,
            )

    if fade_cols > 0:
        height = block_bottom - block_top
        for step in range(fade_cols):
            alpha = 0.80 * (1.0 - step / float(fade_cols + 1))
            candidates = []
            if fade_left:
                candidates.append(step)
            if fade_right:
                candidates.append(max_width - 1 - step)
            for col_idx in candidates:
                if col_idx < 0 or col_idx >= max_width:
                    continue
                ax.add_patch(
                    patches.Rectangle(
                        (seq_x + col_idx * char_spacing - 0.5 * char_spacing, block_top),
                        char_spacing,
                        height,
                        facecolor="white",
                        edgecolor="none",
                        linewidth=0,
                        alpha=alpha,
                        zorder=6,
                    )
                )


def draw_highlight_legend(
    ax: Any,
    legend_items: list[tuple[str, str]],
    *,
    x0: float = 0.58,
    y: float = 1.04,
    positions: list[float] | None = None,
    height: float = 0.08,
) -> None:
    """Legend swatches above the block.

    ``height`` is an axes fraction, so callers scale it to the block's row pitch;
    a fixed value ends up taller than the sequence rows in blocks with many rows.
    """
    x = x0
    for item_idx, (label, kind) in enumerate(legend_items):
        if positions and item_idx < len(positions):
            x = positions[item_idx]
        style = HIGHLIGHT_STYLES.get(kind, HIGHLIGHT_STYLES["true_tail"])
        ax.add_patch(
            patches.Rectangle(
                (x, y - 0.5 * height),
                0.032,
                height,
                facecolor=style["face"],
                edgecolor=style["edge"],
                linewidth=0.50,
                linestyle=style["line"],
                transform=ax.transAxes,
                clip_on=False,
                zorder=3,
            )
        )
        ax.text(x + 0.040, y, label, ha="left", va="center", fontsize=5.2, color="#344054", fontfamily="Arial", transform=ax.transAxes, clip_on=False, zorder=3)
        if not positions:
            x += 0.17 + 0.010 * len(label)


def draw_cca_tail_legend(ax: Any, *, height: float = 0.04) -> None:
    draw_highlight_legend(
        ax,
        [("Ref. CCA", "true_tail"), ("CCA-like end", "terminal_like")],
        x0=0.58,
        y=1.05,
        height=height,
    )


def coordinate_full_alignment_sequence(seq: str, labels: list[str], columns: list[str]) -> tuple[str, str]:
    coord_bases = {label: "-" for label in columns}
    for idx, label in enumerate(labels):
        if idx < len(seq) and label in coord_bases:
            coord_bases[label] = seq[idx]
    tail_text = "".join(coord_bases.get(label, "-") for label in ["74", "75", "76"])
    return "".join(coord_bases[label] for label in columns), tail_text


def remove_empty_alignment_columns(
    sequences: list[str],
    highlight_ranges: dict[int, list[tuple[int, int, str]]],
) -> tuple[list[str], dict[int, list[tuple[int, int, str]]]]:
    """Drop displayed coordinate columns that have no bases in any shown row."""
    if not sequences:
        return sequences, highlight_ranges
    width = max(len(seq) for seq in sequences)
    keep = [
        any(idx < len(seq) and seq[idx] != "-" for seq in sequences)
        for idx in range(width)
    ]
    index_map: dict[int, int] = {}
    for idx, kept in enumerate(keep):
        if kept:
            index_map[idx] = len(index_map)
    compressed_sequences = [
        "".join((seq[idx] if idx < len(seq) else "-") for idx, kept in enumerate(keep) if kept)
        for seq in sequences
    ]
    compressed_highlights: dict[int, list[tuple[int, int, str]]] = {}
    for row_idx, ranges in highlight_ranges.items():
        row_ranges: list[tuple[int, int, str]] = []
        for start, end, kind in ranges:
            mapped = [index_map[idx] for idx in range(start, end) if idx in index_map]
            row_ranges.extend(collapsed_ranges(mapped, kind))
        if row_ranges:
            compressed_highlights[row_idx] = row_ranges
    return compressed_sequences, compressed_highlights


def alignment_residue_columns(aligned_seq: str) -> list[int]:
    return [idx for idx, base in enumerate(aligned_seq) if base != "-"]


def kept_column_labels(sequences: list[str], columns: list[str]) -> list[str]:
    """Labels of the columns ``remove_empty_alignment_columns`` keeps, in order."""
    if not sequences:
        return []
    width = max(len(seq) for seq in sequences)
    return [
        columns[idx]
        for idx in range(min(width, len(columns)))
        if any(idx < len(seq) and seq[idx] != "-" for seq in sequences)
    ]


def window_ruler(
    labels: list[str],
    window: tuple[int, int],
    *,
    max_ticks: int = 8,
) -> list[tuple[int, str]]:
    """Evenly spaced ``(offset, label)`` ticks for the columns inside ``window``.

    Labelling every column collides at this type size, so ticks are thinned to at
    most ``max_ticks`` evenly spaced positions, always keeping the last column so
    the 3' end of the window stays readable.
    """
    start, end = window
    items = [
        (idx - start, label)
        for idx, label in enumerate(labels)
        if start <= idx < end
    ]
    if len(items) <= max_ticks:
        return items
    step = -(-len(items) // max_ticks)  # ceil division
    kept = items[::step]
    if kept and kept[-1] != items[-1]:
        kept.append(items[-1])
    return kept


def collapsed_ranges(indices: list[int], kind: str) -> list[tuple[int, int, str]]:
    if not indices:
        return []
    ranges: list[tuple[int, int, str]] = []
    start = indices[0]
    prev = indices[0]
    for idx in indices[1:]:
        if idx == prev + 1:
            prev = idx
            continue
        ranges.append((start, prev + 1, kind))
        start = idx
        prev = idx
    ranges.append((start, prev + 1, kind))
    return ranges


def coordinate_alignment_sequence_for_columns(seq: str, labels: list[str], display_cols: list[str]) -> str:
    coord_bases = {label: "-" for label in display_cols}
    for residue_idx, label in enumerate(labels):
        if residue_idx < len(seq) and label in coord_bases:
            coord_bases[label] = seq[residue_idx]
    return "".join(coord_bases[label] for label in display_cols)


def coordinate_highlights_for_sequence(
    sequence: str,
    display_cols: list[str],
    label_kinds: dict[str, set[str]],
) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    for kind, label_set in label_kinds.items():
        indices = [idx for idx, label in enumerate(display_cols) if label in label_set and idx < len(sequence) and sequence[idx] != "-"]
        ranges.extend(collapsed_ranges(indices, kind))
    return ranges


def alignment_window_from_positions(
    aligned: dict[str, str],
    positions: list[int],
    *,
    min_width: int,
    max_width: int,
    padding: int = 4,
) -> tuple[int, int]:
    if not aligned:
        return 0, 0
    length = len(next(iter(aligned.values())))
    if length == 0:
        return 0, 0
    if not positions:
        width = min(min_width, length)
        return 0, width
    left = max(0, min(positions) - padding)
    right = min(length - 1, max(positions) + padding)
    span = right - left + 1
    width = min(length, max(min_width, min(max_width, span)))
    center = (left + right) // 2
    start = max(0, min(length - width, center - width // 2))
    return start, width


def build_mafft_window_block(
    records: list[tuple[str, dict[str, str], dict[str, str]]],
    columns: list[str],
    label_kinds: dict[str, set[str]],
    *,
    prefix: str,
    min_width: int,
    max_width: int,
) -> tuple[list[str], dict[int, list[tuple[int, int, str]]], int]:
    mafft_records = [(f"{prefix}{idx:02d}", row_sequence(row, columns)) for idx, (_seqname, row, _meta) in enumerate(records, start=1)]
    aligned = run_mafft(mafft_records)
    global_highlights: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    all_positions: list[int] = []
    for row_idx, (align_name, _seq) in enumerate(mafft_records):
        _seqname, row, _meta = records[row_idx]
        _raw_seq, gold_labels = row_sequence_and_labels(row, columns)
        residue_cols = alignment_residue_columns(aligned[align_name])
        for residue_idx, label in enumerate(gold_labels):
            if residue_idx >= len(residue_cols):
                continue
            for kind, label_set in label_kinds.items():
                if label in label_set:
                    col_idx = residue_cols[residue_idx]
                    global_highlights[row_idx][kind].append(col_idx)
                    all_positions.append(col_idx)
    start, width = alignment_window_from_positions(aligned, all_positions, min_width=min_width, max_width=max_width)
    window_sequences = [aligned[name][start : start + width] for name, _seq in mafft_records]
    local_highlights: dict[int, list[tuple[int, int, str]]] = {}
    end = start + width
    for row_idx, kinds in global_highlights.items():
        ranges: list[tuple[int, int, str]] = []
        for kind, positions in kinds.items():
            local_positions = sorted(pos - start for pos in positions if start <= pos < end)
            ranges.extend(collapsed_ranges(local_positions, kind))
        if ranges:
            local_highlights[row_idx] = ranges
    return window_sequences, local_highlights, start


def build_infernal_full_block(
    records: list[tuple[str, dict[str, str], dict[str, str]]],
    columns: list[str],
    label_kinds: dict[str, set[str]],
    *,
    prefix: str,
) -> tuple[list[str], dict[int, list[tuple[int, int, str]]]]:
    infernal_records = [(f"{prefix}{idx:02d}", row_sequence(row, columns)) for idx, (_seqname, row, _meta) in enumerate(records, start=1)]
    aligned = run_infernal_cmalign(infernal_records)
    sequences = [aligned[name] for name, _seq in infernal_records]
    highlights: dict[int, list[tuple[int, int, str]]] = {}
    for row_idx, (align_name, _seq) in enumerate(infernal_records):
        _seqname, row, _meta = records[row_idx]
        _raw_seq, gold_labels = row_sequence_and_labels(row, columns)
        residue_cols = alignment_residue_columns(aligned[align_name])
        ranges: list[tuple[int, int, str]] = []
        for kind, label_set in label_kinds.items():
            positions = [
                residue_cols[residue_idx]
                for residue_idx, label in enumerate(gold_labels)
                if label in label_set and residue_idx < len(residue_cols)
            ]
            ranges.extend(collapsed_ranges(sorted(positions), kind))
        if ranges:
            highlights[row_idx] = ranges
    return sequences, highlights


def draw_msa_examples_figure(
    columns: list[str],
    gold_rows: dict[str, dict[str, str]],
    export_meta: dict[str, dict[str, str]],
) -> None:
    cca_records = select_cca_records(columns, gold_rows, export_meta)
    cca_names = [seqname.split("|")[-1] for seqname, _row, _meta, _gold_tail, _raw_tail, _terminal_seq in cca_records]
    cca_msa_labels = [
        f"{('+CCA' if gold_tail == 'CCA' else '-' + raw_tail):<7} {name}"
        for name, (_seqname, _row, _meta, gold_tail, raw_tail, _terminal_seq) in zip(cca_names, cca_records)
    ]
    coord_index = {label: idx for idx, label in enumerate(columns)}
    cca_model_sequences: list[str] = []
    cca_model_7476: dict[str, str] = {}
    cca_model_highlights: dict[int, list[tuple[int, int, str]]] = {}
    for row_idx, (seqname, row, _meta, gold_tail, raw_tail, _terminal_seq) in enumerate(cca_records):
        seq = row_sequence(row, columns)
        case = load_case(FULL_RUN, seqname)
        pred = predicted_sequence_labels(case) if case and case.get("status") == "ok" else []
        model_alignment, tail_text = coordinate_full_alignment_sequence(seq, pred, columns)
        cca_model_sequences.append(model_alignment)
        cca_model_7476[seqname] = tail_text
        if gold_tail == "CCA" and tail_text == "CCA":
            positions = [coord_index[label] for label in ["74", "75", "76"] if label in coord_index]
            cca_model_highlights[row_idx] = collapsed_ranges(positions, "true_tail")
        elif raw_tail in {"CA", "CAA", "CCA", "CCCA", "CCCCA", "CCCCCA"}:
            terminal_positions: list[int] = []
            for residue_idx in range(max(0, len(seq) - len(raw_tail)), len(seq)):
                if residue_idx < len(pred) and pred[residue_idx] in coord_index:
                    terminal_positions.append(coord_index[pred[residue_idx]])
            if not terminal_positions:
                start = model_alignment.rfind(raw_tail)
                if start >= 0:
                    terminal_positions = list(range(start, start + len(raw_tail)))
            cca_model_highlights[row_idx] = collapsed_ranges(sorted(terminal_positions), "terminal_like")
    cca_infernal_records = [(f"cca{idx:02d}", row_sequence(row, columns)) for idx, (_seqname, row, _meta, _gold_tail, _raw_tail, _terminal_seq) in enumerate(cca_records, start=1)]
    cca_aligned = run_infernal_cmalign(cca_infernal_records)
    cca_infernal_sequences = [cca_aligned[name] for name, _seq in cca_infernal_records]
    cca_infernal_highlights: dict[int, list[tuple[int, int, str]]] = {}
    for row_idx, (align_name, _seq) in enumerate(cca_infernal_records):
        _seqname, row, _meta, gold_tail, raw_tail, _terminal_seq = cca_records[row_idx]
        seq, gold_labels = row_sequence_and_labels(row, columns)
        residue_cols = alignment_residue_columns(cca_aligned[align_name])
        if gold_tail == "CCA":
            positions = [
                residue_cols[residue_idx]
                for residue_idx, label in enumerate(gold_labels)
                if label in {"74", "75", "76"} and residue_idx < len(residue_cols)
            ]
            cca_infernal_highlights[row_idx] = collapsed_ranges(sorted(positions), "true_tail")
        elif raw_tail in {"CA", "CAA", "CCA", "CCCA", "CCCCA", "CCCCCA"}:
            terminal_positions = [
                residue_cols[residue_idx]
                for residue_idx in range(max(0, len(seq) - len(raw_tail)), len(seq))
                if residue_idx < len(residue_cols)
            ]
            cca_infernal_highlights[row_idx] = collapsed_ranges(sorted(terminal_positions), "terminal_like")

    _gtrnadb_columns, gtrnadb_rows = load_gtrnadb_flat()
    fig03_model_cache = load_fig03_model_cache()

    variable_cols = [
        col
        for col in [
            "44",
            "45",
            "V11",
            "V12",
            "V13",
            "V14",
            "V15",
            "V16",
            "V17",
            "V1",
            "V2",
            "V3",
            "V4",
            "V5",
            "V27",
            "V26",
            "V25",
            "V24",
            "V23",
            "V22",
            "V21",
            "46",
            "47",
            "48",
            "49",
            "50",
            "51",
            "52",
            "53",
        ]
        if col in columns
    ]
    variable_stem_cols = {
        col
        for col in variable_cols
        if col.startswith("V") and col[1:].isdigit() and (11 <= int(col[1:]) <= 17 or 21 <= int(col[1:]) <= 27)
    }
    variable_loop_cols = {col for col in variable_cols if col in {"V1", "V2", "V3", "V4", "V5"}}
    variable_label_kinds = {
        "v_stem": variable_stem_cols,
        "v_loop": variable_loop_cols,
        "t_region": {col for col in variable_cols if col.isdigit() and 46 <= int(col) <= 53},
    }
    variable_records, variable_model_cases = select_gtrnadb_variable_records(
        columns,
        gtrnadb_rows,
        fig03_model_cache,
        variable_cols,
    )
    variable_labels = [gtrnadb_display_label(seqname, row, columns, include_v=True) for seqname, row, _meta in variable_records]
    variable_reference_style = {0: {"prefix": "Ref ->", "color": "#2563A6", "prefix_color": "#2563A6", "weight": "bold"}} if variable_labels else {}
    variable_model_sequences: list[str] = []
    variable_model_highlights: dict[int, list[tuple[int, int, str]]] = {}
    for row_idx, (seqname, row, _meta) in enumerate(variable_records):
        seq = row_sequence(row, columns)
        case = variable_model_cases.get(seqname, {})
        pred = predicted_sequence_labels(case) if case and case.get("status") == "ok" else []
        model_sequence, _tail_text = coordinate_full_alignment_sequence(seq, pred, columns)
        variable_model_sequences.append(model_sequence)
        ranges = coordinate_highlights_for_sequence(model_sequence, columns, variable_label_kinds)
        if ranges:
            variable_model_highlights[row_idx] = ranges
    variable_infernal_sequences, variable_infernal_highlights = build_infernal_full_block(
        variable_records,
        columns,
        variable_label_kinds,
        prefix="var",
    )

    anticodon_cols = [
        col
        for col in ["30", "31", "32", "33", "34", "35", "36", "37", "38", "39", "40", "41", "42", "43", "44", "45"]
        if col in columns
    ]
    anticodon_label_kinds = {"anticodon": {"34", "35", "36"}}
    anticodon_records, anticodon_model_cases = select_gtrnadb_anticodon_records(
        columns,
        gtrnadb_rows,
        fig03_model_cache,
    )
    save_fig03_model_cache(fig03_model_cache)
    anticodon_labels = [
        gtrnadb_display_label(seqname, row, columns, trnascan=meta)
        for seqname, row, meta in anticodon_records
    ]
    anticodon_model_sequences: list[str] = []
    anticodon_model_highlights: dict[int, list[tuple[int, int, str]]] = {}
    for row_idx, (seqname, row, _meta) in enumerate(anticodon_records):
        seq = row_sequence(row, columns)
        case = anticodon_model_cases.get(seqname, {})
        pred = predicted_sequence_labels(case) if case and case.get("status") == "ok" else []
        model_sequence, _tail_text = coordinate_full_alignment_sequence(seq, pred, columns)
        anticodon_model_sequences.append(model_sequence)
        ranges = coordinate_highlights_for_sequence(model_sequence, columns, anticodon_label_kinds)
        if ranges:
            anticodon_model_highlights[row_idx] = ranges
    anticodon_trnascan_sequences = [row_sequence(row, columns) for _seqname, row, _meta in anticodon_records]
    anticodon_trnascan_highlights: dict[int, list[tuple[int, int, str]]] = {}
    for row_idx, (_seqname, _row, meta) in enumerate(anticodon_records):
        start = int(meta.get("anticodon_start") or meta.get("model_anticodon_start") or 0)
        end = int(meta.get("anticodon_end") or (start + 2 if start else 0))
        if start and end >= start:
            anticodon_trnascan_highlights[row_idx] = [(start - 1, end, "anticodon")]

    cca_model_display_sequences, cca_model_display_highlights = remove_empty_alignment_columns(
        cca_model_sequences,
        cca_model_highlights,
    )
    variable_model_display_sequences, variable_model_display_highlights = remove_empty_alignment_columns(
        variable_model_sequences,
        variable_model_highlights,
    )
    anticodon_model_display_sequences, anticodon_model_display_highlights = remove_empty_alignment_columns(
        anticodon_model_sequences,
        anticodon_model_highlights,
    )

    # Cropped display windows: each panel shows only the region it discusses, with
    # surrounding context, so the highlighted columns stay legible. Indices are
    # character offsets into the block's own aligned string; the two blocks of a
    # panel share one window so the rows stay visually in step.
    panel_windows = {
        "cca": (74, 100),
        "variable": (38, 78),
        "anticodon": (22, 48),
    }
    # Sprinzl coordinate ruler for the SprinzlMap blocks: the displayed columns are
    # the full coordinate frame with all-gap columns dropped, so recover their labels
    # before slicing the window.
    model_rulers = {
        panel: window_ruler(kept_column_labels(seqs, columns), panel_windows[panel])
        for panel, seqs in (
            ("cca", cca_model_sequences),
            ("variable", variable_model_sequences),
            ("anticodon", anticodon_model_sequences),
        )
    }

    write_tsv(
        RESULTS_DIR / "msa_example_records.tsv",
        [
            {
                "panel": "CCA",
                "seqname": seqname,
                "group": name,
                "gold_tail": gold_tail,
                "raw_terminal_class": raw_tail,
                "terminal_sequence": terminal_seq,
                "model_74_76": cca_model_7476.get(seqname, ""),
                "model_alignment": cca_model_sequences[idx - 1],
                "infernal_alignment": cca_infernal_sequences[idx - 1],
            }
            for idx, (name, (seqname, _row, _meta, gold_tail, raw_tail, terminal_seq)) in enumerate(zip(cca_names, cca_records), start=1)
        ]
        + [
            {
                "panel": "Variable",
                "seqname": seqname,
                "group": variable_labels[idx],
                "source_database": meta.get("source_database", "GtRNAdb"),
                "amino_acid": parse_seqname_type(seqname)[0],
                "anticodon": display_anticodon(parse_seqname_type(seqname)[1]),
                "v_positions": v_position_count(row, columns),
                "displayed_correct": labels_fraction_for_case(variable_model_cases.get(seqname, {}), row, columns, set(variable_cols))[0],
                "displayed_total": labels_fraction_for_case(variable_model_cases.get(seqname, {}), row, columns, set(variable_cols))[1],
                "model_template": variable_model_cases.get(seqname, {}).get("template_name", ""),
                "model_alignment": variable_model_sequences[idx],
                "infernal_alignment": variable_infernal_sequences[idx],
            }
            for idx, (seqname, row, meta) in enumerate(variable_records)
        ]
        + [
            {
                "panel": "Anticodon",
                "seqname": seqname,
                "group": anticodon_labels[idx],
                "source_database": meta.get("source_database", "GtRNAdb"),
                "amino_acid": meta.get("amino_acid", ""),
                "anticodon": display_anticodon(meta.get("anticodon", "")),
                "anticodon_start": anticodon_start_index(row, columns),
                "trnascan_type": meta.get("type", ""),
                "trnascan_codon": display_anticodon(str(meta.get("codon", ""))),
                "trnascan_anticodon_start": meta.get("anticodon_start", ""),
                "displayed_correct": labels_fraction_for_case(anticodon_model_cases.get(seqname, {}), row, columns, {"34", "35", "36"})[0],
                "displayed_total": labels_fraction_for_case(anticodon_model_cases.get(seqname, {}), row, columns, {"34", "35", "36"})[1],
                "model_template": anticodon_model_cases.get(seqname, {}).get("template_name", ""),
                "model_alignment": anticodon_model_sequences[idx],
                "trnascan_sequence": anticodon_trnascan_sequences[idx],
            }
            for idx, (seqname, row, meta) in enumerate(anticodon_records)
        ],
        [
            "panel",
            "seqname",
            "group",
            "source",
            "source_database",
            "amino_acid",
            "anticodon",
            "gold_tail",
            "raw_terminal_class",
            "terminal_sequence",
            "model_74_76",
            "v_positions",
            "anticodon_start",
            "trnascan_type",
            "trnascan_codon",
            "trnascan_anticodon_start",
            "displayed_correct",
            "displayed_total",
            "model_template",
            "model_alignment",
            "infernal_alignment",
            "trnascan_sequence",
        ],
    )

    fig = plt.figure(figsize=(6.2, 7.6))
    outer = fig.add_gridspec(3, 1, height_ratios=[1.86, 1.18, 1.18], hspace=0.18)
    gs_a = outer[0].subgridspec(2, 1, height_ratios=[1.0, 1.0], hspace=0.12)
    gs_b = outer[1].subgridspec(2, 1, height_ratios=[1.0, 1.0], hspace=0.14)
    gs_c = outer[2].subgridspec(2, 1, height_ratios=[1.0, 1.0], hspace=0.14)
    ax_a_top = fig.add_subplot(gs_a[0, 0])
    ax_a_bottom = fig.add_subplot(gs_a[1, 0])
    ax_b_top = fig.add_subplot(gs_b[0, 0])
    ax_b_bottom = fig.add_subplot(gs_b[1, 0])
    ax_c_top = fig.add_subplot(gs_c[0, 0])
    ax_c_bottom = fig.add_subplot(gs_c[1, 0])

    panel_label(ax_a_top, "A")
    draw_msa_text_block(
        ax_a_top,
        cca_msa_labels,
        cca_model_display_sequences,
        title="SprinzlMap",
        highlight_ranges=cca_model_display_highlights,
        column_window=panel_windows["cca"],
        ruler=model_rulers["cca"],
        fade_cols=3,
        fade_right=False,
    )
    draw_cca_tail_legend(ax_a_top, height=0.039)
    draw_msa_text_block(
        ax_a_bottom,
        cca_msa_labels,
        cca_infernal_sequences,
        title="Infernal/RF00005",
        highlight_ranges=cca_infernal_highlights,
        column_window=panel_windows["cca"],
        fade_cols=3,
        fade_right=False,
    )

    panel_label(ax_b_top, "B")
    draw_msa_text_block(
        ax_b_top,
        variable_labels,
        variable_model_display_sequences,
        title="SprinzlMap",
        highlight_ranges=variable_model_display_highlights,
        column_window=panel_windows["variable"],
        ruler=model_rulers["variable"],
        fade_cols=3,
        row_label_styles=variable_reference_style,
        row_step=0.58,
        fontsize=3.95,
        char_spacing=1.08,
    )
    draw_highlight_legend(
        ax_b_top,
        [("V stem", "v_stem"), ("V loop", "v_loop"), ("T-adjacent", "t_region")],
        positions=[0.49, 0.64, 0.79],
        height=0.069,
    )
    draw_msa_text_block(
        ax_b_bottom,
        variable_labels,
        variable_infernal_sequences,
        title="Infernal/RF00005",
        highlight_ranges=variable_infernal_highlights,
        column_window=panel_windows["variable"],
        fade_cols=3,
        row_label_styles=variable_reference_style,
        row_step=0.58,
        fontsize=3.95,
        char_spacing=1.08,
    )

    panel_label(ax_c_top, "C")
    draw_msa_text_block(
        ax_c_top,
        anticodon_labels,
        anticodon_trnascan_sequences,
        title="tRNAscan-SE anticodon",
        highlight_ranges=anticodon_trnascan_highlights,
        column_window=panel_windows["anticodon"],
        fade_cols=3,
        row_step=0.58,
        fontsize=3.95,
        char_spacing=1.08,
    )
    draw_highlight_legend(ax_c_top, [("Anticodon", "anticodon")], x0=0.72, height=0.069)
    draw_msa_text_block(
        ax_c_bottom,
        anticodon_labels,
        anticodon_model_display_sequences,
        title="SprinzlMap",
        highlight_ranges=anticodon_model_display_highlights,
        column_window=panel_windows["anticodon"],
        ruler=model_rulers["anticodon"],
        fade_cols=3,
        row_step=0.58,
        fontsize=3.95,
        char_spacing=1.08,
    )

    fig.savefig(FIGURES_DIR / "fig05_msa_examples.pdf", bbox_inches="tight", pad_inches=0.025)
    fig.savefig(FIGURES_DIR / "fig05_msa_examples.png", dpi=300, bbox_inches="tight", pad_inches=0.025)
    plt.close(fig)


def draw_boundary_overview(ax_label: Any, ax_exact: Any, rows: list[dict[str, Any]]) -> None:
    panel_label(ax_label, "A")
    order = ["线粒体", "动物线粒体", "叶绿体", "病毒", "蓝小体", "模板相似度 <0.50"]
    row_by_category = {row["category"]: row for row in rows}
    plot_rows = [row_by_category[label] for label in order if label in row_by_category]
    labels = [row["category"] for row in plot_rows]
    y = list(range(len(labels)))[::-1]
    label_acc = [100 * float(row["residue_label_accuracy"]) for row in plot_rows]
    exact = [100 * float(row["exact_match_rate"]) for row in plot_rows]
    specs = [
        (ax_label, label_acc, "#4F79A7", "逐碱基编号"),
        (ax_exact, exact, "#C26A5A", "整条全对"),
    ]
    for idx, (ax, values, color, title) in enumerate(specs):
        ax.barh(y, values, height=0.56, color=color, edgecolor="none", zorder=2)
        for value, yy in zip(values, y):
            if value >= 14:
                x_text = value - 2.4
                ha = "right"
                text_color = "#FFFFFF"
            else:
                x_text = value + 2.0
                ha = "left"
                text_color = "#344054"
            ax.text(x_text, yy, f"{value:.1f}", fontsize=5.6, va="center", ha=ha, color=text_color)
        ax.set_xlim(0, 100)
        ax.set_ylim(min(y) - 0.6, max(y) + 0.6)
        ax.set_title(title, fontsize=6.7, color="#101828", pad=3)
        ax.tick_params(axis="both", length=2.2, width=0.55, color="#344054", labelsize=5.6, labelcolor="#344054")
        ax.set_xticks([0, 50, 100])
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(True)
        ax.spines["bottom"].set_visible(True)
        ax.spines["left"].set_linewidth(0.55)
        ax.spines["bottom"].set_linewidth(0.55)
        ax.spines["left"].set_color("#344054")
        ax.spines["bottom"].set_color("#344054")
    ax_label.set_yticks(y)
    ax_label.set_yticklabels(labels, fontsize=6.0)
    ax_exact.set_yticks(y)
    ax_exact.tick_params(labelleft=False)


def draw_accuracy_heatmap(ax: Any, position_rows: list[dict[str, Any]]) -> None:
    panel_label(ax, "B")
    positions = load_sprinzl_positions()
    row_by_position = {row["position"]: row for row in position_rows}
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "sprinzl_accuracy",
        ["#B42318", "#D98D2B", "#E9D8A6", "#8FC9B5", "#216E8A"],
    )
    norm = matplotlib.colors.Normalize(vmin=0.60, vmax=1.00)
    ax.set_aspect("equal")
    ax.axis("off")

    for left, right in PAIR_LINKS:
        if left in positions and right in positions:
            x1, y1 = positions[left]
            x2, y2 = positions[right]
            ax.plot([x1, x2], [y1, y2], color="#CBD5E1", linewidth=0.75, linestyle=(0, (2, 2)), zorder=0)

    visible = [label for label in SPRINZL_POSITIONS if label in positions]
    for left, right in zip(visible, visible[1:]):
        x1, y1 = positions[left]
        x2, y2 = positions[right]
        ax.plot([x1, x2], [y1, y2], color="#E2E8F0", linewidth=0.55, zorder=0)

    for label in visible:
        x, y = positions[label]
        row = row_by_position.get(label, {})
        total = int(row.get("total") or 0)
        accuracy = row.get("accuracy")
        if total == 0 or accuracy == "":
            face = "#F1F5F9"
            edge = "#CBD5E1"
            radius = 6.4
        else:
            acc = float(accuracy)
            face = cmap(norm(acc))
            edge = "#FFFFFF"
            radius = 6.4 + 4.2 * max(0.0, min(1.0, (acc - 0.60) / 0.40))
        ax.add_patch(patches.Circle((x, y), radius=radius, facecolor=face, edgecolor=edge, linewidth=0.55, zorder=2))

    for label, (dx, dy) in POSITION_LABEL_OFFSETS.items():
        if label not in positions:
            continue
        x, y = positions[label]
        ax.text(x + dx, y + dy, label, fontsize=6.2, ha="center", va="center", color="#334155")

    tail_pts = [positions[label] for label in ["74", "75", "76"] if label in positions]
    if tail_pts:
        xs_tail = [point[0] for point in tail_pts]
        y_tail = min(point[1] for point in tail_pts) - 24
        x0_tail, x1_tail = min(xs_tail), max(xs_tail)
        ax.plot([x0_tail, x1_tail], [y_tail, y_tail], color="#344054", linewidth=0.75, zorder=5)
        ax.plot([x0_tail, x0_tail], [y_tail, y_tail + 7], color="#344054", linewidth=0.75, zorder=5)
        ax.plot([x1_tail, x1_tail], [y_tail, y_tail + 7], color="#344054", linewidth=0.75, zorder=5)
        ax.text(
            (x0_tail + x1_tail) / 2,
            y_tail - 14,
            "74--76 CCA坐标",
            fontsize=6.2,
            ha="center",
            va="center",
            color="#344054",
            zorder=6,
        )

    for text, anchor, dx, dy, color in [
        ("反密码子", "35", -56, 54, "#B42318"),
        ("可变区", "V3", 48, 4, "#B54708"),
    ]:
        if anchor not in positions:
            continue
        x, y = positions[anchor]
        ax.annotate(
            text,
            xy=(x, y),
            xytext=(x + dx, y + dy),
            textcoords="data",
            fontsize=6.4,
            color=color,
            ha="center",
            va="center",
            arrowprops=dict(arrowstyle="-", lw=0.55, color=color, shrinkA=2, shrinkB=4),
        )

    xs = [point[0] for point in positions.values()]
    ys = [point[1] for point in positions.values()]
    ax.set_xlim(min(xs) - 38, max(xs) + 84)
    ax.set_ylim(max(ys) + 52, min(ys) - 54)
    sm = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, fraction=0.040, pad=0.012)
    cbar.ax.tick_params(labelsize=5.8, length=0)
    cbar.set_label("逐碱基编号准确率", fontsize=6.2)


def draw_variable_error_example(ax: Any, example: dict[str, Any]) -> None:
    panel_label(ax, "C")
    ax.axis("off")
    positions = load_sprinzl_positions()
    entries = list(example["entries"])
    local_order = [
        "26",
        "27",
        "28",
        "29",
        "30",
        "31",
        "32",
        "33",
        "34",
        "35",
        "36",
        "37",
        "38",
        "39",
        "40",
        "41",
        "42",
        "43",
        "44",
        "45",
        "V11",
        "V12",
        "V13",
        "V14",
        "V15",
        "V1",
        "V2",
        "V3",
        "V4",
        "V5",
        "V25",
        "V24",
        "V23",
        "V22",
        "V21",
        "46",
        "47",
        "48",
    ]
    pair_indices = [tuple(pair) for pair in example.get("pair_indices", [])]
    variable_labels = {
        "44",
        "45",
        "46",
        "47",
        "48",
        "V11",
        "V12",
        "V13",
        "V14",
        "V15",
        "V16",
        "V17",
        "V1",
        "V2",
        "V3",
        "V4",
        "V5",
        "V27",
        "V26",
        "V25",
        "V24",
        "V23",
        "V22",
        "V21",
    }

    def insertion_rank(label: str) -> int:
        match = re.search(r"i(\d+)$", label)
        return int(match.group(1)) if match else 0

    def build_local_positions(labels: list[str]) -> dict[str, tuple[float, float]]:
        local_positions = {label: positions[label] for label in local_order if label in positions}
        std_xs = [point[0] for point in local_positions.values()]
        center_x = sum(std_xs) / len(std_xs)
        for label in labels:
            if label in local_positions:
                continue
            match = re.match(r"^(\d+)i(\d+)$", label)
            if not match:
                continue
            anchor = match.group(1)
            rank = int(match.group(2))
            next_label = str(int(anchor) + 1)
            if anchor not in positions or next_label not in positions:
                continue
            x1, y1 = positions[anchor]
            x2, y2 = positions[next_label]
            dx = x2 - x1
            dy = y2 - y1
            dist = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux = -dy / dist
            uy = dx / dist
            if (x1 + x2) / 2 < center_x and ux > 0:
                ux, uy = -ux, -uy
            if (x1 + x2) / 2 > center_x and ux < 0:
                ux, uy = -ux, -uy
            local_positions[label] = ((x1 + x2) / 2 + ux * 23 * rank, (y1 + y2) / 2 + uy * 23 * rank)
        return local_positions

    all_labels = local_order + [str(entry["gold_label"]) for entry in entries] + [str(entry["pred_label"]) for entry in entries]
    local_positions = build_local_positions(all_labels)
    x_values = [point[0] for point in local_positions.values()]
    y_values = [point[1] for point in local_positions.values()]
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(y_values), max(y_values)

    def map_point(area: tuple[float, float, float, float], label: str) -> tuple[float, float]:
        x0, y0, width, height = area
        px, py = local_positions[label]
        x = x0 + (px - x_min) / (x_max - x_min) * width
        y = y0 + (y_max - py) / (y_max - y_min) * height
        return x, y

    base_path_labels = [label for label in local_order if label in local_positions]
    def panel_path_labels(field: str) -> list[str]:
        labels = list(base_path_labels)
        occupied_insertions = sorted(
            {str(entry[field]) for entry in entries if re.match(r"^\d+i\d+$", str(entry[field]))},
            key=lambda label: (int(label.split("i", 1)[0]), insertion_rank(label)),
        )
        by_anchor: dict[str, list[str]] = defaultdict(list)
        for label in occupied_insertions:
            by_anchor[label.split("i", 1)[0]].append(label)
        for anchor, anchor_insertions in sorted(by_anchor.items(), key=lambda item: int(item[0])):
            if anchor in labels:
                insert_at = labels.index(anchor) + 1
                labels = labels[:insert_at] + anchor_insertions + labels[insert_at:]
            else:
                labels = anchor_insertions + labels
        return labels

    def draw_panel(area: tuple[float, float, float, float], title: str, field: str) -> None:
        occupied = {str(entry[field]) for entry in entries}
        label_by_residue = {int(entry["residue_index"]): str(entry[field]) for entry in entries}
        path_labels = panel_path_labels(field)
        ax.text(
            area[0] + area[2] / 2,
            area[1] + area[3] + 0.045,
            title,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=6.4,
            color="#101828",
        )

        variable_points = [map_point(area, label) for label in variable_labels if label in local_positions]
        if variable_points:
            vx_values = [point[0] for point in variable_points]
            vy_values = [point[1] for point in variable_points]
            pad_x = 0.014
            pad_y = 0.030
            x0 = min(vx_values) - pad_x
            y0 = min(vy_values) - pad_y
            width = max(vx_values) - min(vx_values) + 2 * pad_x
            height = max(vy_values) - min(vy_values) + 2 * pad_y
            ax.add_patch(
                patches.FancyBboxPatch(
                    (x0, y0),
                    width,
                    height,
                    transform=ax.transAxes,
                    boxstyle="round,pad=0.002,rounding_size=0.006",
                    facecolor="#FFF7ED",
                    edgecolor="#D08A3E",
                    linewidth=0.55,
                    linestyle=(0, (2.0, 2.0)),
                    alpha=0.78,
                    zorder=0,
                    clip_on=False,
                )
            )
            ax.text(
                x0 + width - 0.006,
                y0 + height + 0.012,
                "可变区",
                transform=ax.transAxes,
                ha="right",
                va="bottom",
                fontsize=5.4,
                color="#9A4B12",
                zorder=5,
            )

        for left_idx, right_idx in pair_indices:
            left = label_by_residue.get(int(left_idx), "")
            right = label_by_residue.get(int(right_idx), "")
            if left not in local_positions or right not in local_positions:
                continue
            x1, y1 = map_point(area, left)
            x2, y2 = map_point(area, right)
            ax.plot(
                [x1, x2],
                [y1, y2],
                transform=ax.transAxes,
                color="#9AA7B8",
                linewidth=0.75,
                linestyle=(0, (2.0, 2.2)),
                alpha=0.78,
                zorder=1,
            )
        for left, right in zip(path_labels, path_labels[1:]):
            x1, y1 = map_point(area, left)
            x2, y2 = map_point(area, right)
            ax.plot(
                [x1, x2],
                [y1, y2],
                transform=ax.transAxes,
                color="#D5DBE5",
                linewidth=0.58,
                zorder=1,
            )
        for label in path_labels:
            x, y = map_point(area, label)
            is_v = str(label).startswith("V")
            ax.scatter(
                [x],
                [y],
                s=22 if label not in occupied else 30,
                marker="o",
                transform=ax.transAxes,
                facecolors="#F8FAFC",
                edgecolors="#D0D5DD" if not is_v else "#D1A06C",
                linewidths=0.45,
                zorder=2,
            )
            if label not in occupied:
                continue
            ax.text(
                x,
                y - 0.030,
                label,
                transform=ax.transAxes,
                ha="center",
                va="top",
                fontsize=4.0,
                color="#667085",
                zorder=5,
                bbox=dict(facecolor="#FFFFFF", edgecolor="none", alpha=0.80, pad=0.04),
            )
        for entry in entries:
            label = str(entry[field])
            if label not in local_positions:
                continue
            x, y = map_point(area, label)
            base = safe_base(entry["base"])
            correct = bool(entry["correct"])
            edge = "#64748B" if field == "gold_label" else ("#2E8B57" if correct else "#B42318")
            linewidth = 0.65 if field == "gold_label" else (0.75 if correct else 0.95)
            ax.scatter(
                [x],
                [y],
                s=78,
                marker="o",
                transform=ax.transAxes,
                facecolors=BASE_MATRIX_COLORS.get(base, "#E8ECF2"),
                edgecolors=edge,
                linewidths=linewidth,
                zorder=4,
                clip_on=False,
            )
            ax.text(
                x,
                y,
                base,
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=5.3,
                color="#101828",
                zorder=5,
            )

    ax.text(
        0.03,
        0.98,
        f"代表样本 E1：长可变区坐标偏移；整体逐碱基 {100 * float(example['label_accuracy']):.1f}%",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=6.0,
        color="#344054",
    )
    gold_area = (0.055, 0.10, 0.425, 0.76)
    pred_area = (0.555, 0.10, 0.425, 0.76)
    draw_panel(gold_area, "人工校对金标准", "gold_label")
    draw_panel(pred_area, "系统输出", "pred_label")


def draw_boundary_figure(rows: list[dict[str, Any]], position_rows: list[dict[str, Any]], variable_example: dict[str, Any]) -> None:
    fig = plt.figure(figsize=(7.4, 7.25))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.05, 1.05], width_ratios=[0.92, 1.28], hspace=0.20, wspace=0.20)
    gs_a = gs[0, 0].subgridspec(1, 2, wspace=0.10)
    ax_a_label = fig.add_subplot(gs_a[0, 0])
    ax_a_exact = fig.add_subplot(gs_a[0, 1], sharey=ax_a_label)
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, :])
    draw_boundary_overview(ax_a_label, ax_a_exact, rows)
    draw_accuracy_heatmap(ax_b, position_rows)
    draw_variable_error_example(ax_c, variable_example)
    fig.savefig(FIGURES_DIR / "fig05_boundary_sources.pdf", bbox_inches="tight")
    fig.savefig(FIGURES_DIR / "fig05_boundary_sources.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    configure_style()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    columns, gold_rows = load_gold()
    export_meta = load_export_meta()
    template_rows = load_template_rows()
    seqnames = ensure_comparison_seqnames(columns, gold_rows, export_meta)

    missing_no_agent = [name for name in seqnames if load_case(NO_AGENT_RUN, name) is None]
    if missing_no_agent:
        print(f"comparison_seqnames={len(seqnames)}")
        print(f"missing_no_agent={len(missing_no_agent)}")
        print(
            "run_no_agent_command="
            f"python benchmark/scripts/run_numbering_benchmark.py --run-id {NO_AGENT_RUN_ID} "
            f"--seqnames-file {SEQNAME_FILE} --no-llm --provider deepseek --model deepseek-chat "
            "--max-workers 32 --progress-every 25"
        )
        return

    domain_rows, similarity_points = build_domain_similarity_tables(columns, gold_rows, export_meta, template_rows)
    isotype_rows = build_isotype_summary(similarity_points)
    domain_coverage = build_domain_coverage(gold_rows, export_meta)
    similarity_bins = binned_similarity(similarity_points)
    comparison_rows, comparison_summary = build_comparison_tables(seqnames, columns, gold_rows, export_meta)
    boundary_rows = build_boundary_table(columns, gold_rows, export_meta, template_rows)
    position_rows = build_position_accuracy(columns, gold_rows)
    variable_example = build_variable_error_example(columns, gold_rows)

    write_tsv(
        RESULTS_DIR / "domain_summary_revised.tsv",
        domain_rows,
        ["source", "source_cn", "n_analyzed", "n_ok", "n_error", "residue_label_accuracy", "exact_match_rate", "region_accuracy", "legality_pass_rate"],
    )
    write_tsv(
        RESULTS_DIR / "domain_coverage_revised.tsv",
        domain_coverage,
        ["source", "source_cn", "n_records", "n_species", "mean_iso_types_per_species", "median_iso_types_per_species", "mean_amino_acids_per_species"],
    )
    write_tsv(
        RESULTS_DIR / "domain_similarity_points.tsv",
        similarity_points,
        ["seqname", "source", "source_cn", "amino_acid", "template_similarity", "label_accuracy", "label_correct", "label_total", "exact_match", "region_accuracy", "legality_passed"],
    )
    write_tsv(
        RESULTS_DIR / "domain_isotype_accuracy.tsv",
        isotype_rows,
        ["source", "source_cn", "amino_acid", "n", "label_accuracy", "exact_match_rate"],
    )
    write_tsv(
        RESULTS_DIR / "domain_similarity_bins.tsv",
        similarity_bins,
        ["source", "source_cn", "similarity_bin", "n", "mean_similarity", "label_accuracy", "exact_match_rate"],
    )
    write_tsv(
        RESULTS_DIR / "baseline_comparison_cases.tsv",
        comparison_rows,
        ["seqname", "source", "source_cn", "gold_isotype", "method", "status", "isotype_applicable", "isotype_correct", "tail_exact", "tail_accuracy", "n73_exact", "region_accuracy", "label_accuracy", "exact_match", "pred_count", "gold_count"],
    )
    write_tsv(
        RESULTS_DIR / "baseline_comparison_summary.tsv",
        comparison_summary,
        ["method", "n", "isotype_accuracy", "tail_exact_rate", "tail_accuracy", "n73_exact_rate", "region_accuracy", "label_accuracy", "exact_match_rate"],
    )
    write_tsv(
        RESULTS_DIR / "boundary_summary_revised.tsv",
        boundary_rows,
        ["category", "n_analyzed", "n_ok", "n_error", "residue_label_accuracy", "exact_match_rate", "legality_pass_rate", "note"],
    )
    write_tsv(
        RESULTS_DIR / "position_accuracy_revised.tsv",
        position_rows,
        ["position", "region", "correct", "total", "accuracy"],
    )
    write_tsv(
        RESULTS_DIR / "variable_error_example.tsv",
        [
            {
                "seqname": variable_example["seqname"],
                "residue_index": row["residue_index"],
                "base": row["base"],
                "gold_label": row["gold_label"],
                "pred_label": row["pred_label"],
                "correct": row["correct"],
                    "label_accuracy": variable_example["label_accuracy"],
                    "variable_accuracy": variable_example["variable_accuracy"],
                    "variable_total": variable_example["variable_total"],
                    "annotation_source": variable_example.get("annotation_source", ""),
                    "variable_struct": variable_example.get("variable_struct", ""),
                }
                for row in variable_example["entries"]
            ],
        [
            "seqname",
            "residue_index",
            "base",
            "gold_label",
            "pred_label",
            "correct",
            "label_accuracy",
            "variable_accuracy",
            "variable_total",
            "annotation_source",
            "variable_struct",
        ],
    )

    draw_domain_figure(domain_rows, similarity_bins, domain_coverage, similarity_points, isotype_rows)
    draw_msa_examples_figure(columns, gold_rows, export_meta)
    draw_boundary_figure(boundary_rows, position_rows, variable_example)

    print(f"comparison_seqnames={len(seqnames)}")
    print(f"domain_points={len(similarity_points)}")
    print(f"comparison_rows={len(comparison_rows)}")


if __name__ == "__main__":
    main()
