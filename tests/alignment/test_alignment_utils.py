from __future__ import annotations

import unittest

from backend.domain.alignment.dataloader import _norm_u, _patch_tail_cca, _safe_int
from backend.domain.alignment.extract import extract_amino_acid_arm


class AlignmentUtilsTest(unittest.TestCase):
    def test_norm_u_normalizes_thymine(self) -> None:
        self.assertEqual(_norm_u("Ttac"), "UUAC")

    def test_safe_int_returns_default_for_invalid_value(self) -> None:
        self.assertEqual(_safe_int("bad", 7), 7)

    def test_patch_tail_cca_appends_valid_suffix(self) -> None:
        self.assertEqual(_patch_tail_cca("AUGGCCA", "AUGG"), "AUGGCCA")

    def test_patch_tail_cca_ignores_non_cca_suffix(self) -> None:
        self.assertEqual(_patch_tail_cca("AUGGGG", "AUGG"), "AUGG")

    def test_patch_tail_cca_respects_input_evidence_gate(self) -> None:
        self.assertEqual(
            _patch_tail_cca("AUGGCCA", "AUGG", allow_terminal_cca=False),
            "AUGG",
        )

    def test_extract_logs_and_returns_none_for_invalid_structure(self) -> None:
        with self.assertLogs("backend.domain.alignment.extract", level="WARNING") as logs:
            result = extract_amino_acid_arm("ACGU", "....")
        self.assertIsNone(result)
        self.assertTrue(any("Failed to extract amino acid arm" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
