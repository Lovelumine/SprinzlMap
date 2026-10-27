from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_CALIBRATION_ARTIFACT = Path(__file__).resolve().with_name(
    "cm_domain_calibration.v1.json"
)
CALIBRATED_GROUPS = ("arch", "bact", "euk")
FEATURE_NAMES = (
    "arch_bit_score",
    "bact_bit_score",
    "euk_bit_score",
    "all_bit_score",
    "arch_average_posterior",
    "bact_average_posterior",
    "euk_average_posterior",
    "all_average_posterior",
    "top_domain_bit_score_margin",
    "all_minus_top_domain_bit_score",
    "sequence_length",
)


def _finite_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def build_calibration_features(
    candidates: Sequence[Mapping[str, Any]],
    sequence_length: int,
) -> list[float]:
    by_group = {
        str(item.get("group") or ""): item
        for item in candidates
        if str(item.get("status") or "") == "ok"
    }
    bit_scores = {
        group: _finite_float((by_group.get(group) or {}).get("bit_score"), -100.0)
        for group in ("arch", "bact", "euk", "all")
    }
    posteriors = {
        group: _finite_float(
            (by_group.get(group) or {}).get("average_posterior"),
            0.0,
        )
        for group in ("arch", "bact", "euk", "all")
    }
    ranked_domain_scores = sorted(
        (bit_scores[group] for group in CALIBRATED_GROUPS), reverse=True
    )
    top_domain = ranked_domain_scores[0]
    runner_up = ranked_domain_scores[1]
    return [
        bit_scores["arch"],
        bit_scores["bact"],
        bit_scores["euk"],
        bit_scores["all"],
        posteriors["arch"],
        posteriors["bact"],
        posteriors["euk"],
        posteriors["all"],
        top_domain - runner_up,
        bit_scores["all"] - top_domain,
        float(max(0, int(sequence_length))),
    ]


class CMDomainCalibrator:
    """Apply the frozen, JSON-encoded canonical-domain score calibrator."""

    def __init__(self, artifact_path: Path | str = DEFAULT_CALIBRATION_ARTIFACT) -> None:
        self.artifact_path = Path(artifact_path)
        self._artifact: dict[str, Any] | None = None
        self._load_error = ""
        try:
            payload = json.loads(self.artifact_path.read_text(encoding="utf-8"))
            if tuple(payload.get("feature_names") or ()) != FEATURE_NAMES:
                raise ValueError("calibration feature contract mismatch")
            if tuple(payload.get("classes") or ()) != CALIBRATED_GROUPS:
                raise ValueError("calibration class contract mismatch")
            self._artifact = payload
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self._artifact is not None

    def predict(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        sequence_length: int,
    ) -> dict[str, Any]:
        if self._artifact is None:
            return {
                "status": "unavailable",
                "disposition": "investigate",
                "reason": self._load_error or "calibration artifact unavailable",
                "artifact_path": str(self.artifact_path),
            }

        hash_check = self._validate_model_hashes(candidates)
        if hash_check:
            return {
                "status": "incompatible",
                "disposition": "investigate",
                "reason": hash_check,
                "artifact_path": str(self.artifact_path),
                "artifact_sha256": self._artifact.get("artifact_sha256"),
            }

        features = build_calibration_features(candidates, sequence_length)
        scaler = dict(self._artifact.get("scaler") or {})
        means = list(scaler.get("mean") or [])
        scales = list(scaler.get("scale") or [])
        model = dict(self._artifact.get("model") or {})
        coefficients = list(model.get("coefficients") or [])
        intercept = list(model.get("intercept") or [])
        if not (
            len(features) == len(means) == len(scales)
            and len(coefficients) == len(intercept) == len(CALIBRATED_GROUPS)
        ):
            return {
                "status": "incompatible",
                "disposition": "investigate",
                "reason": "calibration parameter dimensions do not match the feature contract",
                "artifact_path": str(self.artifact_path),
            }

        standardized = [
            (value - float(mean)) / (float(scale) if float(scale) != 0.0 else 1.0)
            for value, mean, scale in zip(features, means, scales)
        ]
        logits = [
            float(bias) + sum(float(weight) * value for weight, value in zip(row, standardized))
            for row, bias in zip(coefficients, intercept)
        ]
        probabilities = self._softmax(logits)
        ranked = sorted(
            zip(CALIBRATED_GROUPS, probabilities),
            key=lambda item: item[1],
            reverse=True,
        )
        selected_group, confidence = ranked[0]
        probability_margin = confidence - ranked[1][1]
        thresholds = dict(self._artifact.get("decision_thresholds") or {})
        min_confidence = float(thresholds.get("min_confidence", 0.65))
        min_margin = float(thresholds.get("min_probability_margin", 0.1))
        accepted = confidence >= min_confidence and probability_margin >= min_margin
        return {
            "status": "calibrated",
            "disposition": "accepted" if accepted else "investigate",
            "predicted_group": selected_group,
            "probabilities": {
                group: round(float(probability), 8)
                for group, probability in zip(CALIBRATED_GROUPS, probabilities)
            },
            "confidence": round(float(confidence), 8),
            "probability_margin": round(float(probability_margin), 8),
            "thresholds": {
                "min_confidence": min_confidence,
                "min_probability_margin": min_margin,
            },
            "artifact_path": str(self.artifact_path),
            "artifact_sha256": self._artifact.get("artifact_sha256"),
            "training_protocol": dict(self._artifact.get("training_protocol") or {}),
            "validation_summary": dict(self._artifact.get("validation_summary") or {}),
        }

    def _validate_model_hashes(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> str:
        expected = dict((self._artifact or {}).get("input_hashes", {}).get("cm_models") or {})
        observed = {
            str(item.get("group") or ""): str((item.get("model") or {}).get("sha256") or "")
            for item in candidates
            if str(item.get("status") or "") == "ok"
        }
        missing = [group for group in ("arch", "bact", "euk", "all") if not observed.get(group)]
        if missing:
            return f"portfolio missing calibrated CM groups: {','.join(missing)}"
        mismatched = [
            group
            for group in ("arch", "bact", "euk", "all")
            if str(expected.get(group) or "") != observed[group]
        ]
        if mismatched:
            return f"CM model hash mismatch: {','.join(mismatched)}"
        return ""

    @staticmethod
    def _softmax(values: Sequence[float]) -> list[float]:
        if not values:
            return []
        pivot = max(values)
        numerators = [math.exp(value - pivot) for value in values]
        denominator = sum(numerators)
        return [value / denominator for value in numerators]


__all__ = [
    "CALIBRATED_GROUPS",
    "DEFAULT_CALIBRATION_ARTIFACT",
    "FEATURE_NAMES",
    "CMDomainCalibrator",
    "build_calibration_features",
]
