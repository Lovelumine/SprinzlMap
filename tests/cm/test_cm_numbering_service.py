from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.domain.alignment.cm_numbering import CmalignNumberingService, model_group_for_source
from backend.domain.alignment.sprinzl_coordinates import (
    SPRINZL_MATCH_ORDER,
    label_kind,
    labels_are_monotonic,
    labels_from_consensus_structure,
)


ARCH_MODEL_SHA256 = "e9d86b32b2f5dbf690b32eb083546fb100ea66aa1f990bacd752bd0d0bfc67b8"
MODEL_SHA256 = {
    "all": "55a17fb2a3f62cff92f01f084c3347c080a0023cee9bb47e6d110f14deda73bf",
    "arch": ARCH_MODEL_SHA256,
    "bact": "48f36f67e0380ea7172bec173f3b977e6e9695c6a78658449505248c6b2a5624",
    "euk": "2e6ee14930be8e537e0d5054cd86216a1c135901cf3beb6d8fe974c0d2a0349b",
}
ARCH_SEQUENCE = "GGGCTCATCGCTCAGCGGTAGAGTGCCTCCTTTGCAAGGAGGATGCCCTGGGTTCGAATCCCAGTGAGTCCA"
ARCH_EXPECTED_LABELS = [
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14",
    "15", "16", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28",
    "29", "30", "31", "32", "33", "34", "35", "36", "37", "38", "39", "40", "41",
    "42", "43", "44", "45", "46", "47", "48", "49", "50", "51", "52", "53", "54",
    "55", "56", "57", "58", "59", "60", "61", "62", "63", "64", "65", "66", "67",
    "68", "69", "70", "71", "72", "73",
]


def test_consensus_projection_tracks_match_and_insertion_states():
    labels = labels_from_consensus_structure("." + ("_" * len(SPRINZL_MATCH_ORDER)) + "..")

    assert labels[0] == "-1"
    assert labels[1] == "1"
    assert labels[-3:] == ("76", "76i1", "76i2")
    assert label_kind("V11") == "variable-dialect"
    assert label_kind("37i1") == "insertion"
    assert labels_are_monotonic(labels)


def test_consensus_projection_rejects_wrong_match_state_count():
    with pytest.raises(ValueError, match="match-state count"):
        labels_from_consensus_structure("_" * (len(SPRINZL_MATCH_ORDER) - 1))


def test_model_group_for_source_uses_conservative_domain_fallbacks():
    assert model_group_for_source("archaea") == "arch"
    assert model_group_for_source("bacteria") == "bact"
    assert model_group_for_source("cyanelle") == "bact"
    assert model_group_for_source("cytosolic_eukaryote") == "euk"
    assert model_group_for_source("chloroplast") == "euk"
    assert model_group_for_source("mitochondrial") == "all"
    assert model_group_for_source("virus") == "all"


def test_vendored_numbering_model_hashes_are_frozen():
    service = CmalignNumberingService()

    assert {group: service.model(group).sha256 for group in MODEL_SHA256} == MODEL_SHA256


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_archaeal_cm_service_preserves_bases_and_recovers_reference_labels():
    result = CmalignNumberingService().align(
        ARCH_SEQUENCE,
        model_group="arch",
        sequence_id="reference-archaea",
    )

    assert result.model.sha256 == ARCH_MODEL_SHA256
    assert result.base_preserved
    assert result.labels == ARCH_EXPECTED_LABELS
    assert result.score.bit_score is not None
    assert result.score.average_posterior == pytest.approx(1.0)
    assert len(result.structure_pairs) == 21
    assert labels_are_monotonic(result.labels)
    assert result.trace_events[-1]["base_preserved"] is True


@pytest.mark.skipif(shutil.which("cmalign") is None, reason="Infernal cmalign is not installed")
def test_cm_portfolio_records_uncalibrated_model_selection_evidence():
    portfolio = CmalignNumberingService().align_portfolio(
        ARCH_SEQUENCE,
        domain_hint="archaea",
        sequence_id="portfolio-archaea",
    )

    assert portfolio.selected.model.group == "arch"
    assert portfolio.selection_policy == "explicit_domain_hint"
    assert portfolio.score_margin_calibrated is False
    assert len(portfolio.candidates) == 4
    assert {item["group"] for item in portfolio.candidates} == {"arch", "bact", "euk", "all"}
    assert all(item["status"] == "ok" for item in portfolio.candidates)
    assert portfolio.selected.base_preserved
    assert portfolio.selected.portfolio_evidence["selected_group"] == "arch"
    assert portfolio.selected.portfolio_evidence["score_margin_calibrated"] is False
    assert portfolio.selected.trace_events[0]["tool"] == "score_numbering_models"
