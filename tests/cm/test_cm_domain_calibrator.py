from __future__ import annotations

import json

import pytest

from backend.domain.alignment.calibration import (
    DEFAULT_CALIBRATION_ARTIFACT,
    CMDomainCalibrator,
)
from backend.domain.alignment.cm_numbering import explicit_model_group_for_hint


def _candidates(*, wrong_hash: bool = False):
    artifact = json.loads(DEFAULT_CALIBRATION_ARTIFACT.read_text(encoding="utf-8"))
    hashes = artifact["input_hashes"]["cm_models"]
    scores = {"arch": 80.0, "bact": 52.0, "euk": 48.0, "all": 71.0}
    return [
        {
            "group": group,
            "status": "ok",
            "model": {"sha256": "wrong" if wrong_hash and group == "arch" else hashes[group]},
            "bit_score": scores[group],
            "average_posterior": 0.98,
        }
        for group in ("arch", "bact", "euk", "all")
    ]


def test_frozen_cm_calibration_artifact_excludes_test_split():
    artifact = json.loads(DEFAULT_CALIBRATION_ARTIFACT.read_text(encoding="utf-8"))

    assert artifact["training_protocol"]["split"] == "calibration"
    assert artifact["training_protocol"]["test_split_used"] is False
    assert artifact["training_protocol"]["deduplication_key"] == "sequence_sha256"
    assert artifact["validation_summary"]["decision_policy"]["coverage"] > 0
    assert artifact["validation_summary"]["decision_policy"]["accepted_accuracy"] > 0


def test_cm_domain_calibrator_returns_probabilities_and_abstains_on_hash_mismatch():
    calibrator = CMDomainCalibrator()
    prediction = calibrator.predict(_candidates(), sequence_length=72)
    mismatch = calibrator.predict(_candidates(wrong_hash=True), sequence_length=72)

    assert prediction["status"] == "calibrated"
    assert set(prediction["probabilities"]) == {"arch", "bact", "euk"}
    assert sum(prediction["probabilities"].values()) == pytest.approx(1.0)
    assert mismatch["status"] == "incompatible"
    assert mismatch["disposition"] == "investigate"


def test_special_sources_are_not_coerced_to_canonical_all_model():
    assert explicit_model_group_for_hint("mitochondrial") is None
    assert explicit_model_group_for_hint("virus") is None
