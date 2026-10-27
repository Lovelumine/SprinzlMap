#!/usr/bin/env python3
"""Fit the canonical-domain CM selector on the frozen calibration split only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.alignment.calibration.cm_domain_calibrator import (  # noqa: E402
    CALIBRATED_GROUPS,
    FEATURE_NAMES,
    build_calibration_features,
)
from backend.domain.alignment.cm_numbering import CmalignNumberingService  # noqa: E402


SOURCE_TO_GROUP = {
    "archaea": "arch",
    "bacteria": "bact",
    "cyanelle": "bact",
    "cytosolic_eukaryote": "euk",
    "chloroplast": "euk",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case-manifest",
        type=Path,
        default=Path("benchmark/manifests/agentic_v1/cases.jsonl"),
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=PROJECT_ROOT / ".runtime" / "gold" / "mature_benchmark.curated.tsv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "backend/domain/alignment/calibration/cm_domain_calibration.v1.json"
        ),
    )
    parser.add_argument("--workers", type=int, default=min(12, os.cpu_count() or 4))
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-confidence", type=float, default=0.65)
    parser.add_argument("--min-probability-margin", type=float, default=0.10)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_calibration_contract(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: dict[str, dict[str, Any]] = {}
    excluded: dict[str, int] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if not record.get("eligible"):
                continue
            if record.get("splits", {}).get("unique_sequence") != "calibration":
                continue
            source = str(record.get("metadata", {}).get("broad_source") or "")
            group = SOURCE_TO_GROUP.get(source)
            if group is None:
                excluded[source or "missing"] = excluded.get(source or "missing", 0) + 1
                continue
            digest = str(record.get("sequence_sha256") or "")
            current = selected.get(digest)
            if current and current["group"] != group:
                raise ValueError(f"Conflicting labels for sequence digest {digest}")
            selected.setdefault(
                digest,
                {
                    "seqname": str(record.get("seqname") or ""),
                    "sequence_sha256": digest,
                    "group": group,
                    "source": source,
                },
            )
    return list(selected.values()), excluded


def load_sequences(path: Path, seqnames: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader)
        if not header or header[0] != "seqname":
            raise ValueError(f"Unexpected gold header: {path}")
        for row in reader:
            if not row or row[0] not in seqnames:
                continue
            found[row[0]] = "".join(
                str(base or "").strip().upper().replace("T", "U")
                for base in row[1:]
                if str(base or "").strip() not in {"", "-"}
            )
    return found


def score_case(record: dict[str, Any], sequence: str) -> dict[str, Any]:
    portfolio = CmalignNumberingService(cpu=1).align_portfolio(
        sequence,
        sequence_id=record["seqname"],
        domain_hint="",
    )
    candidates = portfolio.candidates
    return {
        **record,
        "sequence_length": len(sequence),
        "features": build_calibration_features(candidates, len(sequence)),
        "model_hashes": {
            str(item.get("group") or ""): str((item.get("model") or {}).get("sha256") or "")
            for item in candidates
            if item.get("status") == "ok"
        },
    }


def reliability_bins(y_true: np.ndarray, probabilities: np.ndarray) -> list[dict[str, Any]]:
    predictions = probabilities.argmax(axis=1)
    confidence = probabilities.max(axis=1)
    correct = predictions == y_true
    bins: list[dict[str, Any]] = []
    for index in range(10):
        lower = index / 10.0
        upper = (index + 1) / 10.0
        mask = (confidence >= lower) & (confidence < upper if index < 9 else confidence <= upper)
        count = int(mask.sum())
        bins.append(
            {
                "lower": lower,
                "upper": upper,
                "count": count,
                "mean_confidence": round(float(confidence[mask].mean()), 8) if count else None,
                "accuracy": round(float(correct[mask].mean()), 8) if count else None,
            }
        )
    return bins


def expected_calibration_error(bins: list[dict[str, Any]], total: int) -> float:
    return sum(
        (int(item["count"]) / total)
        * abs(float(item["accuracy"]) - float(item["mean_confidence"]))
        for item in bins
        if item["count"]
    )


def main() -> None:
    args = parse_args()
    manifest_path = (PROJECT_ROOT / args.case_manifest).resolve()
    gold_path = (PROJECT_ROOT / args.gold).resolve()
    output_path = (PROJECT_ROOT / args.output).resolve()
    records, excluded = load_calibration_contract(manifest_path)
    if args.limit > 0:
        records = records[: args.limit]
    sequences = load_sequences(gold_path, {record["seqname"] for record in records})
    missing = [record["seqname"] for record in records if record["seqname"] not in sequences]
    if missing:
        raise ValueError(f"Missing {len(missing)} calibration sequences; first={missing[0]}")

    scored: list[dict[str, Any]] = []
    workers = max(1, min(int(args.workers), 32))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cm-calibration") as pool:
        futures = {
            pool.submit(score_case, record, sequences[record["seqname"]]): record
            for record in records
        }
        for index, future in enumerate(as_completed(futures), start=1):
            scored.append(future.result())
            if index % 100 == 0 or index == len(futures):
                print(f"scored {index}/{len(futures)}", flush=True)
    scored.sort(key=lambda item: item["sequence_sha256"])

    x = np.asarray([item["features"] for item in scored], dtype=float)
    y = np.asarray([CALIBRATED_GROUPS.index(item["group"]) for item in scored], dtype=int)
    scaler = StandardScaler().fit(x)
    x_scaled = scaler.transform(x)
    estimator = LogisticRegression(max_iter=5000, solver="lbfgs")
    folds = max(3, min(int(args.folds), int(np.bincount(y).min())))
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=20260713)
    oof_estimator = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=5000, solver="lbfgs"),
    )
    oof_probabilities = cross_val_predict(
        oof_estimator,
        x,
        y,
        cv=splitter,
        method="predict_proba",
    )
    estimator.fit(x_scaled, y)

    predictions = oof_probabilities.argmax(axis=1)
    confidence = oof_probabilities.max(axis=1)
    sorted_probabilities = np.sort(oof_probabilities, axis=1)
    probability_margin = sorted_probabilities[:, -1] - sorted_probabilities[:, -2]
    accepted_mask = (
        (confidence >= float(args.min_confidence))
        & (probability_margin >= float(args.min_probability_margin))
    )
    bins = reliability_bins(y, oof_probabilities)
    class_counts = {
        group: int((y == index).sum())
        for index, group in enumerate(CALIBRATED_GROUPS)
    }
    brier_by_class = {
        group: round(
            float(brier_score_loss((y == index).astype(int), oof_probabilities[:, index])),
            8,
        )
        for index, group in enumerate(CALIBRATED_GROUPS)
    }
    model_hashes = scored[0]["model_hashes"]
    if any(item["model_hashes"] != model_hashes for item in scored):
        raise ValueError("CM model hashes changed during calibration")

    artifact: dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_type": "canonical_cm_domain_probability_calibrator",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classes": list(CALIBRATED_GROUPS),
        "feature_names": list(FEATURE_NAMES),
        "training_protocol": {
            "case_protocol": "unique_sequence",
            "split": "calibration",
            "deduplication_key": "sequence_sha256",
            "included_sources": SOURCE_TO_GROUP,
            "excluded_source_counts": excluded,
            "test_split_used": False,
            "random_state": 20260713,
            "cross_validation": f"StratifiedKFold(n_splits={folds}, shuffle=True)",
        },
        "input_hashes": {
            "case_manifest": sha256_file(manifest_path),
            "gold": sha256_file(gold_path),
            "cm_models": model_hashes,
        },
        "sample_summary": {
            "calibration_rows_after_sequence_deduplication": len(scored),
            "class_counts": class_counts,
        },
        "scaler": {
            "type": "standard_scaler",
            "mean": [round(float(value), 12) for value in scaler.mean_],
            "scale": [round(float(value), 12) for value in scaler.scale_],
        },
        "model": {
            "type": "multinomial_logistic_regression",
            "solver": "lbfgs",
            "coefficients": [
                [round(float(value), 12) for value in row]
                for row in estimator.coef_
            ],
            "intercept": [round(float(value), 12) for value in estimator.intercept_],
        },
        "decision_thresholds": {
            "min_confidence": float(args.min_confidence),
            "min_probability_margin": float(args.min_probability_margin),
            "below_threshold_disposition": "investigate",
        },
        "validation_summary": {
            "scope": "out_of_fold_on_calibration_split",
            "accuracy": round(float(accuracy_score(y, predictions)), 8),
            "macro_f1": round(float(f1_score(y, predictions, average="macro")), 8),
            "log_loss": round(float(log_loss(y, oof_probabilities)), 8),
            "multiclass_brier_mean": round(float(np.mean(list(brier_by_class.values()))), 8),
            "brier_by_class": brier_by_class,
            "ece_10_bin": round(float(expected_calibration_error(bins, len(y))), 8),
            "decision_policy": {
                "accepted_count": int(accepted_mask.sum()),
                "coverage": round(float(accepted_mask.mean()), 8),
                "accepted_accuracy": round(
                    float((predictions[accepted_mask] == y[accepted_mask]).mean()),
                    8,
                ) if accepted_mask.any() else None,
                "abstained_count": int((~accepted_mask).sum()),
            },
            "confusion_matrix": confusion_matrix(y, predictions).tolist(),
            "reliability_bins": bins,
        },
        "tool_provenance": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "sklearn": __import__("sklearn").__version__,
            "infernal": "\n".join(
                (subprocess.run(["cmalign", "-h"], capture_output=True, text=True).stdout).splitlines()[:3]
            ),
            "generator": str(Path(__file__).relative_to(PROJECT_ROOT)),
        },
    }
    canonical = json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    artifact["artifact_sha256"] = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="ascii",
    )
    print(json.dumps(artifact["validation_summary"], indent=2), flush=True)
    print(f"wrote {output_path}", flush=True)


if __name__ == "__main__":
    main()
