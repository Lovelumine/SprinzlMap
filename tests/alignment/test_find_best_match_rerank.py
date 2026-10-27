from __future__ import annotations

import os
import time
import unittest
from itertools import permutations
from unittest.mock import patch

from backend.domain.alignment.preparation import template_retrieval
from backend.domain.alignment.find_best_match import (
    _parse_template_seqname_metadata,
    _rerank_scored_candidates,
)


class FindBestMatchRerankTest(unittest.TestCase):
    def test_parse_template_seqname_metadata(self) -> None:
        iso, anti = _parse_template_seqname_metadata("procMari_MIT_9313_MIT9313_chr.trna26-SerGCT")
        self.assertEqual(iso, "ser")
        self.assertEqual(anti, "GCU")

    def test_rerank_prefers_matching_anticodon_and_isotype_within_margin(self) -> None:
        scored = [
            (79.0, "procMari_MIT_9313_MIT9313_chr.trna26-SerGCT", "SEQ1", {}, "aln1"),
            (77.0, "soraCell_SO_CE56_SO_CE_56_chr.trna33-AsnGTT", "SEQ2", {}, "aln2"),
        ]
        with patch.dict(
            os.environ,
            {
                "TRNAALIGN_TEMPLATE_RERANK_MARGIN": "4.0",
                "TRNAALIGN_TEMPLATE_ANTICODON_BONUS": "4.0",
                "TRNAALIGN_TEMPLATE_ISOTYPE_BONUS": "2.0",
            },
            clear=False,
        ):
            best = _rerank_scored_candidates(
                scored,
                preferred_isotype="asn",
                preferred_anticodon="GUU",
            )
        self.assertIsNotNone(best)
        self.assertEqual(best[1], "soraCell_SO_CE56_SO_CE_56_chr.trna33-AsnGTT")

    def test_rerank_keeps_raw_best_when_gap_is_large(self) -> None:
        scored = [
            (79.0, "procMari_MIT_9313_MIT9313_chr.trna26-SerGCT", "SEQ1", {}, "aln1"),
            (70.0, "soraCell_SO_CE56_SO_CE_56_chr.trna33-AsnGTT", "SEQ2", {}, "aln2"),
        ]
        with patch.dict(
            os.environ,
            {
                "TRNAALIGN_TEMPLATE_RERANK_MARGIN": "4.0",
                "TRNAALIGN_TEMPLATE_ANTICODON_BONUS": "4.0",
                "TRNAALIGN_TEMPLATE_ISOTYPE_BONUS": "2.0",
            },
            clear=False,
        ):
            best = _rerank_scored_candidates(
                scored,
                preferred_isotype="asn",
                preferred_anticodon="GUU",
            )
        self.assertIsNotNone(best)
        self.assertEqual(best[1], "procMari_MIT_9313_MIT9313_chr.trna26-SerGCT")

    def test_rerank_ties_are_independent_of_completion_order(self) -> None:
        tied = [
            (79.0, "template-z", "SEQ1", {}, "aln1"),
            (79.0, "template-a", "SEQ2", {}, "aln2"),
            (79.0, "template-m", "SEQ3", {}, "aln3"),
        ]

        selected = {
            _rerank_scored_candidates(list(order), preferred_isotype="", preferred_anticodon="")[1]
            for order in permutations(tied)
        }

        self.assertEqual(selected, {"template-a"})

    def test_top_matches_ties_use_seqname_as_final_sort_key(self) -> None:
        candidates = [
            ("template-z", "AAA", {}),
            ("template-a", "AAA", {}),
            ("template-m", "AAA", {}),
        ]

        with (
            patch.object(template_retrieval, "_load_tsv_if_needed"),
            patch.dict(
                template_retrieval._CACHE,
                {"slim_rows": candidates, "size": len(candidates)},
                clear=False,
            ),
            patch.object(
                template_retrieval,
                "_fast_prefilter_candidates",
                return_value=list(reversed(candidates)),
            ),
            patch.object(template_retrieval, "_decide_workers", return_value=1),
            patch.object(
                template_retrieval,
                "_score_one",
                side_effect=lambda pack, *_args: (1.0, pack[0], pack[1], pack[2], "aln"),
            ),
        ):
            ranked = template_retrieval._score_ranked_matches(
                input_seq="AAA",
                tsv_path="unused.tsv",
                match=2.0,
                mismatch=-1.0,
                gap_open=-2.0,
                gap_extend=-0.5,
                preferred_isotype="",
                preferred_anticodon="",
            )

        self.assertEqual(
            [item["seqname"] for item in ranked],
            ["template-a", "template-m", "template-z"],
        )

    def test_top_matches_ignore_parallel_future_completion_order(self) -> None:
        candidates = [
            ("template-a", "AAA", {}),
            ("template-m", "AAA", {}),
            ("template-z", "AAA", {}),
        ]

        def delayed_score(pack, *_args):
            delay = {"template-a": 0.03, "template-m": 0.02, "template-z": 0.0}
            time.sleep(delay[pack[0]])
            return 1.0, pack[0], pack[1], pack[2], "aln"

        with (
            patch.object(template_retrieval, "_load_tsv_if_needed"),
            patch.dict(
                template_retrieval._CACHE,
                {"slim_rows": candidates, "size": len(candidates)},
                clear=False,
            ),
            patch.object(
                template_retrieval,
                "_fast_prefilter_candidates",
                return_value=candidates,
            ),
            patch.object(template_retrieval, "_decide_workers", return_value=3),
            patch.object(template_retrieval, "_score_one", side_effect=delayed_score),
        ):
            ranked = template_retrieval._score_ranked_matches(
                input_seq="AAA",
                tsv_path="unused.tsv",
                match=2.0,
                mismatch=-1.0,
                gap_open=-2.0,
                gap_extend=-0.5,
                preferred_isotype="",
                preferred_anticodon="",
            )

        self.assertEqual(
            [item["seqname"] for item in ranked],
            ["template-a", "template-m", "template-z"],
        )


if __name__ == "__main__":
    unittest.main()
