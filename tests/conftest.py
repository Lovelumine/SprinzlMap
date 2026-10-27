"""Test-wide fixtures.

Two hazards this removes:

* The gold-curation service caches the answer table and the review queues, and
  those caches are process-wide, so a fake row set from one test would be
  visible to the next and make results depend on file order.
* The repository is configured in the developer ``.env``, so a test that flips
  ``enabled()`` would otherwise read the live MySQL used by the deployment.
"""
from __future__ import annotations

import os

import pytest

from backend.domain.gold_curation import service as gold_service
from backend.infra.storage import sequence_repository


def _clear_gold_caches() -> None:
    gold_service.clear_gold_caches()
    gold_service._list_cases_cached.cache_clear()
    gold_service._run_results_by_seqname.cache_clear()


@pytest.fixture(autouse=True)
def fresh_gold_caches():
    _clear_gold_caches()
    yield
    _clear_gold_caches()


@pytest.fixture(autouse=True)
def no_live_sequence_repository(monkeypatch):
    """Only ``TRNA_DB_INTEGRATION_TEST=1`` tests may touch the deployed database."""
    if os.getenv("TRNA_DB_INTEGRATION_TEST") == "1":
        return

    def refuse():
        raise AssertionError(
            "a test tried to open the deployed sequence repository; "
            "patch the repository functions or mark the test as an integration test"
        )

    monkeypatch.setattr(sequence_repository, "connection", refuse)
