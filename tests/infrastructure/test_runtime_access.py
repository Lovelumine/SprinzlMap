from __future__ import annotations

import os
import unittest
from unittest import mock

from backend.infra.config.runtime_settings import coerce_runtime_updates
from backend.infra.config.runtime_access import (
    get_runtime_int,
    get_runtime_str,
    get_runtime_value,
)


class RuntimeAccessTest(unittest.TestCase):
    # An integer runtime setting that still exists in RUNTIME_SETTINGS_SCHEMA (default 3).
    INT_KEY = "AGENT_ACCEPTOR_TAIL_MIN_REFERENCE_SUPPORT"

    def test_get_runtime_value_uses_schema_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_runtime_value(self.INT_KEY), 3)

    def test_get_runtime_value_coerces_env_value(self) -> None:
        with mock.patch.dict(os.environ, {self.INT_KEY: "8"}, clear=True):
            self.assertEqual(get_runtime_value(self.INT_KEY), 8)

    def test_get_runtime_int_applies_bounds(self) -> None:
        with mock.patch.dict(os.environ, {self.INT_KEY: "8"}, clear=True):
            self.assertEqual(get_runtime_int(self.INT_KEY, minimum=2, maximum=5), 5)

    def test_get_runtime_str_uses_fallback_when_schema_default_is_blank(self) -> None:
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=True):
            self.assertEqual(get_runtime_str("DEEPSEEK_API_KEY", fallback="fallback-model"), "fallback-model")

    def test_optional_agent_llm_component_flags_are_coerced(self) -> None:
        values = coerce_runtime_updates(
            {
                "AGENT_ENABLE_LLM_POSTCHECK": "false",
                "AGENT_ENABLE_LLM_BOUNDARY_REFINEMENT": "false",
                "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT": "true",
            }
        )
        self.assertFalse(values["AGENT_ENABLE_LLM_POSTCHECK"])
        self.assertFalse(values["AGENT_ENABLE_LLM_BOUNDARY_REFINEMENT"])
        self.assertTrue(values["AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT"])


if __name__ == "__main__":
    unittest.main()
