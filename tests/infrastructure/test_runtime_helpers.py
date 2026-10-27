from __future__ import annotations

import os
import unittest
from unittest import mock

from backend.domain.alignment import runtime_helpers


class RuntimeHelpersTest(unittest.TestCase):
    def test_merge_runtime_values_prefers_override(self) -> None:
        merged = runtime_helpers.merge_runtime_values(
            {"A": 1, "B": 2},
            {"B": 3, "C": 4},
        )
        self.assertEqual(merged, {"A": 1, "B": 3, "C": 4})

    def test_load_runtime_values_merges_effective_settings(self) -> None:
        with mock.patch.object(
            runtime_helpers,
            "get_effective_runtime_settings",
            return_value={"LLM_POSTCHECK": True, "TRNAALIGN_MAX_WORKERS": 4},
        ):
            values = runtime_helpers.load_runtime_values({"TRNAALIGN_MAX_WORKERS": 7})
        self.assertEqual(values["TRNAALIGN_MAX_WORKERS"], 7)
        self.assertTrue(values["LLM_POSTCHECK"])

    def test_runtime_readers_use_runtime_values_and_clamp(self) -> None:
        values = {
            "FLAG": "yes",
            "COUNT": "99",
            "RATIO": "0.001",
            "TEXT": "  hello  ",
        }
        self.assertTrue(runtime_helpers.runtime_bool("FLAG", False, runtime_values=values))
        self.assertEqual(runtime_helpers.runtime_int("COUNT", 1, 1, 10, runtime_values=values), 10)
        self.assertEqual(runtime_helpers.runtime_float("RATIO", 0.5, 0.1, 2.0, runtime_values=values), 0.1)
        self.assertEqual(runtime_helpers.runtime_str("TEXT", runtime_values=values), "hello")

    def test_runtime_readers_fall_back_to_env_and_defaults(self) -> None:
        with mock.patch.dict(os.environ, {"FLAG": "off", "COUNT": "bad"}, clear=True):
            self.assertFalse(runtime_helpers.runtime_bool("FLAG", True))
            self.assertEqual(runtime_helpers.runtime_int("COUNT", 5, 1, 9), 5)
            self.assertEqual(runtime_helpers.deepseek_fallback_model(), "deepseek-chat")


if __name__ == "__main__":
    unittest.main()
