from __future__ import annotations

import unittest

from backend.domain.alignment.postcheck.d_region import (
    _deterministic_local_search_candidate,
    _score_d_block,
)


class DRegionPriorityTest(unittest.TestCase):
    def test_score_penalizes_suffix_before_unsuffixed_when_template_family_empty(self) -> None:
        nums = ["16", "17", "17a", "18"]
        templ = ["U", "-", "-", "G"]
        targ = ["U", "-", "A", "G"]

        score, reasons = _score_d_block(nums, targ, templ)

        self.assertGreaterEqual(score, 2)
        self.assertIn("canonical_before_suffix:17<17a", reasons)

    def test_deterministic_search_prefers_17_before_17a(self) -> None:
        nums = ["16", "17", "17a", "18"]
        templ = ["U", "-", "-", "G"]
        targ_before = ["U", "-", "A", "G"]

        targ_after = _deterministic_local_search_candidate(nums, templ, targ_before)

        self.assertEqual(targ_after, ["U", "A", "-", "G"])

    def test_deterministic_search_prefers_20_then_20a_then_20b(self) -> None:
        nums = ["19", "20", "20a", "20b", "21"]
        templ = ["G", "-", "-", "-", "A"]
        targ_before = ["G", "-", "-", "U", "A"]

        targ_after = _deterministic_local_search_candidate(nums, templ, targ_before)

        self.assertEqual(targ_after, ["G", "U", "-", "-", "A"])


if __name__ == "__main__":
    unittest.main()
