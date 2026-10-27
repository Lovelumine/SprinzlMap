from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from backend.domain.alignment.engine import regional_dp
from backend.domain.alignment.engine.scoring import (
    alignment_score_and_str,
    required_alignment_backend_provenance,
)
from backend.domain.alignment.preparation import template_retrieval


def test_required_parasail_backend_fails_closed_when_missing() -> None:
    with patch.dict(sys.modules, {"parasail": None}):
        with pytest.raises(RuntimeError, match="Required alignment backend"):
            alignment_score_and_str(
                "ACGU",
                "ACGU",
                2.0,
                -1.0,
                -2.0,
                -0.5,
                use_edlib=False,
                use_parasail=True,
            )


def test_required_backend_provenance_is_versioned() -> None:
    provenance = required_alignment_backend_provenance()

    assert provenance["name"] == "parasail.nw_trace_scan_16"
    assert provenance["package"] == "parasail"
    assert provenance["version"] == "1.3.4"
    assert provenance["required"] is True
    assert provenance["available"] is True


def test_regional_dp_requires_parasail() -> None:
    with patch.object(
        regional_dp,
        "alignment_score_and_str",
        return_value=(8.0, "target ACGU\n||||\nquery  ACGU"),
    ) as mocked:
        result = regional_dp.align_with_dp(
            ["1", "2", "3", "4"],
            "ACGU",
            "ACGU",
        )

    assert result["aligned_sequence"] == list("ACGU")
    assert mocked.call_args.kwargs["use_parasail"] is True
    assert mocked.call_args.kwargs["use_edlib"] is False


def test_template_ranking_requires_parasail() -> None:
    with patch.object(
        template_retrieval,
        "alignment_score_and_str",
        return_value=(8.0, "alignment"),
    ) as mocked:
        score, seqname, *_ = template_retrieval._score_one(
            ("template-a", "ACGU", {}),
            "ACGU",
            2.0,
            -1.0,
            -2.0,
            -0.5,
        )

    assert score == 8.0
    assert seqname == "template-a"
    assert mocked.call_args.kwargs["use_parasail"] is True
