from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.domain.alignment.align import _align_with_dp, perform_full_alignment, validate_alignment
from backend.domain.alignment.engine import regional_dp


class AlignmentPipelineRegressionTest(unittest.TestCase):
    def test_aa3_allows_non_cca_terminal_residue_as_73_insertion(self) -> None:
        with patch.object(
            regional_dp,
            "alignment_score_and_str",
            return_value=(0.0, "target ACU\n||| \nquery  AC-"),
        ):
            result = regional_dp.align_with_dp(
                ["72", "73"],
                "AC",
                "ACU",
                region="Aminoacyl arm 3' end",
            )
        self.assertEqual(result["aligned_numbering"][-1], "73i1")
        self.assertEqual(result["aligned_sequence"][-1], "U")

    def test_aa3_non_cca_keeps_reserved_tail_columns_unoccupied(self) -> None:
        template_num = [str(i) for i in range(66, 77)]
        template_seq = "UCUCUCCGCCA"
        target_seq = "CCGUACUA"

        result = _align_with_dp(
            template_num=template_num,
            template_seq=template_seq,
            target_seq=target_seq,
            region="Aminoacyl arm 3' end",
        )

        self.assertTrue(validate_alignment(target_seq, result["aligned_sequence"]))
        observed = dict(zip(result["aligned_numbering"], result["aligned_sequence"]))
        self.assertEqual("-", observed["74"])
        self.assertEqual("-", observed["75"])
        self.assertEqual("-", observed["76"])
        self.assertFalse(
            any(
                label.startswith(("73i", "74i", "75i", "76i"))
                and base != "-"
                for label, base in zip(
                    result["aligned_numbering"],
                    result["aligned_sequence"],
                )
            )
        )

    def test_aa3_complete_cca_is_projected_only_to_74_76(self) -> None:
        template_num = [str(i) for i in range(66, 77)]
        result = _align_with_dp(
            template_num=template_num,
            template_seq="UCUCUCCGCCA",
            target_seq="CCGUACUACCA",
            region="Aminoacyl arm 3' end",
        )

        observed = dict(zip(result["aligned_numbering"], result["aligned_sequence"]))
        self.assertEqual("C", observed["74"])
        self.assertEqual("C", observed["75"])
        self.assertEqual("A", observed["76"])
        self.assertTrue(validate_alignment("CCGUACUACCA", result["aligned_sequence"]))

    def test_aa3_partial_terminal_prefix_never_occupies_74_76(self) -> None:
        template_num = [str(i) for i in range(66, 77)]
        result = _align_with_dp(
            template_num=template_num,
            template_seq="UCUCUCCGCCA",
            target_seq="CCGUACUACC",
            region="Aminoacyl arm 3' end",
        )

        observed = dict(zip(result["aligned_numbering"], result["aligned_sequence"]))
        for label in ("74", "75", "76"):
            self.assertEqual(observed[label], "-")
        self.assertFalse(
            any(
                label.startswith(("74i", "75i", "76i")) and base != "-"
                for label, base in zip(
                    result["aligned_numbering"], result["aligned_sequence"]
                )
            )
        )
        self.assertTrue(validate_alignment("CCGUACUACC", result["aligned_sequence"]))

    def test_aa3_terminal_cca_columns_remain_unoccupied_for_non_cca(self) -> None:
        template_num = [
            "66", "67", "68", "69", "70", "71", "72", "73",
            "73i1", "74", "75", "76", "76i1",
        ]
        result = _align_with_dp(
            template_num=template_num,
            template_seq="UCUCUCCGCCA--",
            target_seq="CCGUACUA",
            region="Aminoacyl arm 3' end",
        )

        observed = dict(zip(result["aligned_numbering"], result["aligned_sequence"]))
        for label in ("74", "75", "76", "76i1"):
            self.assertEqual("-", observed[label])
        self.assertTrue(validate_alignment("CCGUACUA", result["aligned_sequence"]))

    def test_compaction_breakage_rolls_back_to_valid_rows(self) -> None:
        target_seq = "AUGC"
        fake_cmp = {
            "template_name": "dummy-template",
            "annotation_meta": {
                "empty_count": 0,
                "empty_regions": [],
                "warnings": [],
                "trnascan_hit": True,
                "secondary_structure": "",
                "annotation_source": "",
            },
            "regions": {},
        }
        fake_blocks = [
            {
                "region": "Dummy",
                "lines": [
                    ["1", "2", "3", "4"],
                    ["A", "U", "G", "C"],
                    ["A", "U", "G", "C"],
                ],
            }
        ]

        def _break_compaction(num_row, seq_row, template_row=None):
            seq_row[0] = "-"
            return True

        with tempfile.TemporaryDirectory() as tmpdir:
            output_csv_path = Path(tmpdir) / "aligned.csv"
            with patch("backend.domain.alignment.align.compare_input_template", return_value=fake_cmp), patch(
                "backend.domain.alignment.align.build_global_struct_summary", return_value={}
            ), patch(
                "backend.domain.alignment.align.build_alignment_blocks", return_value=fake_blocks
            ), patch(
                "backend.domain.alignment.align.compact_and_prune_insertion_columns",
                side_effect=_break_compaction,
            ):
                template_name, csv_content, warnings = perform_full_alignment(
                    target_seq=target_seq,
                    output_csv_path=str(output_csv_path),
                    use_llm=False,
                )

        self.assertEqual("dummy-template", template_name)
        self.assertIn(
            "Insertion-column canonicalization rollback applied after breaking base preservation.",
            warnings,
        )
        self.assertIn("A,U,G,C", csv_content.replace("\r\n", "\n"))

    def test_invalid_postcheck_rolls_back_to_pre_postcheck_blocks(self) -> None:
        target_seq = "AUGC"
        fake_cmp = {
            "template_name": "dummy-template",
            "annotation_meta": {
                "empty_count": 0,
                "empty_regions": [],
                "warnings": [],
                "trnascan_hit": True,
                "secondary_structure": "",
                "annotation_source": "",
            },
            "regions": {},
        }
        fake_blocks = [
            {
                "region": "Dummy",
                "lines": [
                    ["1", "2", "3", "4"],
                    ["A", "U", "G", "C"],
                    ["A", "U", "G", "C"],
                ],
            }
        ]
        broken_blocks = [
            {
                "region": "Dummy",
                "lines": [
                    ["1", "2", "3", "4"],
                    ["A", "U", "G", "C"],
                    ["A", "-", "G", "C"],
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_csv_path = Path(tmpdir) / "aligned.csv"
            with patch("backend.domain.alignment.align.compare_input_template", return_value=fake_cmp), patch(
                "backend.domain.alignment.align.build_global_struct_summary", return_value={}
            ), patch(
                "backend.domain.alignment.align.build_alignment_blocks", return_value=fake_blocks
            ), patch(
                "backend.domain.alignment.align.postcheck_alignment",
                return_value=broken_blocks,
            ):
                template_name, csv_content, warnings = perform_full_alignment(
                    target_seq=target_seq,
                    output_csv_path=str(output_csv_path),
                    use_llm=True,
                    llm_reasoning=False,
                )

        self.assertEqual("dummy-template", template_name)
        self.assertIn(
            "LLM postcheck rollback applied after breaking base preservation.",
            warnings,
        )
        self.assertIn("A,U,G,C", csv_content.replace("\r\n", "\n"))


if __name__ == "__main__":
    unittest.main()
