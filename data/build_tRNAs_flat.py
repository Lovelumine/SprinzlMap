#!/usr/bin/env python3
"""Rebuild data/tRNAs_flat.tsv from the tRNAviz numbered TSV tables.

This restores the historical data/trnavizdata.py transform, but locks the
output to the current repository schema:
- positions 44/45 are placed before the variable-arm block
- positions 74/75/76 are retained explicitly
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd


DATA_DIR = Path(__file__).resolve().parent
DEFAULT_INPUTS = [
    DATA_DIR / "archaea" / "tRNAs-111618-112435.tsv",
    DATA_DIR / "bacteria" / "tRNAs-111618-112536.tsv",
    DATA_DIR / "eukaryotes" / "tRNAs-111618-112505.tsv",
]
DEFAULT_OUTPUT = DATA_DIR / "tRNAs_flat.tsv"
CURRENT_SCHEMA = [
    "seqname",
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


def _read_numbered_tables(paths: Iterable[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Missing input TSV: {path}")
        frames.append(pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False))
    return pd.concat(frames, ignore_index=True)


def _build_pair_cache(df: pd.DataFrame) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[pd.Series, pd.Series]]]:
    mapping: dict[str, tuple[str, str]] = {}
    split_cache: dict[str, tuple[pd.Series, pd.Series]] = {}
    for column in df.columns:
        if ":" not in column:
            continue
        left_name, right_name = column.split(":", 1)
        mapping[left_name] = (column, "left")
        mapping[right_name] = (column, "right")
        parts = df[column].fillna("").astype(str).str.split(":", n=1, expand=True)
        left = parts[0].fillna("")
        if parts.shape[1] > 1:
            right = parts[1].fillna("")
        else:
            right = pd.Series("", index=df.index, dtype=object)
        split_cache[column] = (left, right)
    return mapping, split_cache


def build_flat_table(df: pd.DataFrame) -> pd.DataFrame:
    if "seqname" not in df.columns:
        raise ValueError("Input tables must contain a 'seqname' column")

    pair_mapping, split_cache = _build_pair_cache(df)
    out = pd.DataFrame(index=df.index)
    out["seqname"] = df["seqname"]
    out["-1"] = "-"

    for column in CURRENT_SCHEMA:
        if column in ("seqname", "-1"):
            continue
        if column in df.columns:
            out[column] = df[column].fillna("")
            continue
        if column in pair_mapping:
            source_column, side = pair_mapping[column]
            left, right = split_cache[source_column]
            out[column] = left if side == "left" else right
            continue
        out[column] = ""

    return out


def compare_tables(generated: pd.DataFrame, reference_path: Path) -> int:
    reference = pd.read_csv(reference_path, sep="\t", dtype=str, keep_default_na=False)
    if list(generated.columns) != list(reference.columns):
        raise ValueError(
            "Column mismatch between generated table and reference file: "
            f"{reference_path}"
        )
    return int((generated != reference).to_numpy().sum())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output TSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--check-against",
        type=Path,
        default=None,
        help="Optional TSV to compare against after building",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="Optional input TSVs. Defaults to the three tRNAviz domain tables.",
    )
    args = parser.parse_args()

    input_paths = args.inputs or DEFAULT_INPUTS
    merged = _read_numbered_tables(input_paths)
    flat = build_flat_table(merged)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    flat.to_csv(args.output, sep="\t", index=False)
    print(f"Wrote {len(flat)} rows x {len(flat.columns)} columns to {args.output}")

    if args.check_against is not None:
        diff_count = compare_tables(flat, args.check_against)
        print(f"Cell diffs vs {args.check_against}: {diff_count}")


if __name__ == "__main__":
    main()
