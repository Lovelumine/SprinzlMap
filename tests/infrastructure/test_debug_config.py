from __future__ import annotations

import importlib
import os
import unittest
from unittest import mock

import backend.domain.alignment.debug_config as debug_config


class DebugConfigTest(unittest.TestCase):
    def test_debug_flags_are_opt_in_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            module = importlib.reload(debug_config)
        self.assertFalse(module.DEBUG_ALIGN)
        self.assertFalse(module.DEBUG_ALIGN_FULL)
        self.assertFalse(module.DEBUG_ANNOTATE)
        self.assertFalse(module.DEBUG_ANNOTATE_FULL)
        self.assertFalse(module.DEBUG_ANNOTATE_SUMMARY)

    def test_env_flag_respects_truthy_values(self) -> None:
        with mock.patch.dict(os.environ, {"TRNAALIGN_DEBUG_ALIGN": "1"}, clear=True):
            module = importlib.reload(debug_config)
        self.assertTrue(module.DEBUG_ALIGN)


if __name__ == "__main__":
    unittest.main()
