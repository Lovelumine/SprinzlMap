from __future__ import annotations

import unittest

from backend.domain.alignment.postcheck.variable_t_boundary_repair import (
    repair_variable_t_boundary_shift,
)


class VariableTBoundaryRepairTest(unittest.TestCase):
    def test_repair_shifts_unsupported_v1_into_leading_t_gap(self) -> None:
        blocks = [
            {
                "region": "Variable loop",
                "lines": [
                    ["44", "45", "V1", "46", "47", "48"],
                    ["A", "G", "-", "G", "U", "C"],
                    ["A", "G", "G", "A", "A", "G"],
                ],
                "meta": {"input_struct": ""},
            },
            {
                "region": "T loop + T stem",
                "lines": [
                    ["49", "50", "51", "52", "53", "54", "55", "56", "57", "58", "59", "60", "61", "62", "63", "64", "65"],
                    ["G", "A", "U", "G", "G", "A", "U", "C", "G", "A", "A", "A", "C", "C", "A", "U", "G"],
                    ["-", "A", "U", "G", "G", "A", "U", "C", "A", "A", "A", "C", "C", "A", "U", "G", "C"],
                ],
                "meta": {},
            },
        ]

        repaired = repair_variable_t_boundary_shift(blocks)
        var_nums, _, var_targ = repaired[0]["lines"]
        t_nums, _, t_targ = repaired[1]["lines"]

        self.assertEqual(var_targ[var_nums.index("V1")], "-")
        self.assertEqual(var_targ[var_nums.index("46")], "G")
        self.assertEqual(var_targ[var_nums.index("47")], "A")
        self.assertEqual(var_targ[var_nums.index("48")], "A")
        self.assertEqual(t_targ[t_nums.index("49")], "G")
        self.assertEqual(t_targ[t_nums.index("50")], "A")
        self.assertIn("boundary_phase_repair", repaired[0]["meta"])

    def test_repair_clears_unsupported_v1_and_t_insertion(self) -> None:
        blocks = [
            {
                "region": "Variable loop",
                "lines": [
                    ["44", "45", "V1", "46", "47", "48"],
                    ["A", "G", "-", "G", "U", "C"],
                    ["A", "G", "G", "A", "A", "-"],
                ],
                "meta": {"input_struct": ""},
            },
            {
                "region": "T loop + T stem",
                "lines": [
                    ["49", "50", "51", "52", "53", "54", "55", "56", "57", "58", "59", "60", "61", "62", "63", "64", "65", "65i1"],
                    ["U", "A", "U", "G", "G", "A", "U", "C", "C", "A", "A", "C", "C", "A", "U", "G", "C", "-"],
                    ["U", "A", "U", "G", "G", "A", "U", "C", "C", "-", "A", "C", "C", "A", "U", "G", "C", "A"],
                ],
                "meta": {},
            },
        ]

        repaired = repair_variable_t_boundary_shift(blocks)
        var_nums, _, var_targ = repaired[0]["lines"]
        t_nums, _, t_targ = repaired[1]["lines"]

        self.assertEqual(var_targ[var_nums.index("V1")], "-")
        self.assertEqual(var_targ[var_nums.index("46")], "G")
        self.assertEqual(t_targ[t_nums.index("58")], "A")
        self.assertEqual(t_targ[t_nums.index("65i1")], "-")
        self.assertIn("boundary_phase_repair", repaired[0]["meta"])

    def test_repair_skips_long_variable_arm_supported_case(self) -> None:
        blocks = [
            {
                "region": "Variable loop",
                "lines": [
                    ["44", "45", "V11", "V21", "V1", "46", "47", "48"],
                    ["A", "G", "C", "G", "-", "G", "U", "C"],
                    ["A", "G", "C", "G", "A", "-", "U", "C"],
                ],
                "meta": {"input_struct": "><"},
            },
            {
                "region": "T loop + T stem",
                "lines": [
                    ["49", "50", "51", "52", "53", "54", "55", "56", "57", "58", "59", "60", "61", "62", "63", "64", "65", "65i1"],
                    ["U", "A", "U", "G", "G", "A", "U", "C", "C", "A", "A", "C", "C", "A", "U", "G", "C", "-"],
                    ["U", "A", "U", "G", "G", "A", "U", "C", "C", "-", "A", "C", "C", "A", "U", "G", "C", "A"],
                ],
                "meta": {},
            },
        ]

        repaired = repair_variable_t_boundary_shift(blocks)
        self.assertEqual(repaired, blocks)

    def test_repair_skips_when_t_insertion_pattern_is_missing(self) -> None:
        blocks = [
            {
                "region": "Variable loop",
                "lines": [
                    ["44", "45", "V1", "46", "47", "48"],
                    ["A", "G", "-", "G", "U", "C"],
                    ["A", "G", "G", "A", "A", "-"],
                ],
                "meta": {"input_struct": ""},
            },
            {
                "region": "T loop + T stem",
                "lines": [
                    ["49", "50", "51", "52", "53", "54", "55", "56", "57", "58", "59", "60", "61", "62", "63", "64", "65"],
                    ["U", "A", "U", "G", "G", "A", "U", "C", "C", "A", "A", "C", "C", "A", "U", "G", "C"],
                    ["U", "A", "U", "G", "G", "A", "U", "C", "C", "-", "A", "C", "C", "A", "U", "G", "C"],
                ],
                "meta": {},
            },
        ]

        repaired = repair_variable_t_boundary_shift(blocks)
        self.assertEqual(repaired, blocks)


if __name__ == "__main__":
    unittest.main()
