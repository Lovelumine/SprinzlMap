from __future__ import annotations

import unittest

from backend.domain.alignment.postcheck import aminoacyl_joint, anticodon, d_region, t_region
from backend.domain.alignment.engine.boundary_refinement import postcheck_runtime_overrides


class PostcheckRuntimeCfgTest(unittest.TestCase):
    def test_t_region_load_cfg_reads_runtime_prompt(self) -> None:
        cfg = t_region._load_cfg({"LLM_T_SYSTEM_PROMPT": "t prompt"})
        self.assertEqual(cfg["t_system_prompt"], "t prompt")

    def test_d_region_load_cfg_reads_runtime_prompt(self) -> None:
        cfg = d_region._load_cfg({"LLM_D_SYSTEM_PROMPT": "d prompt"})
        self.assertEqual(cfg["d_system_prompt"], "d prompt")

    def test_anticodon_load_cfg_reads_runtime_prompt(self) -> None:
        cfg = anticodon._load_cfg({"LLM_ANTICODON_SYSTEM_PROMPT": "anti prompt"})
        self.assertIn("anti prompt", cfg["anticodon_system_prompt"])

    def test_aminoacyl_joint_load_cfg_reads_runtime_prompts(self) -> None:
        cfg = aminoacyl_joint._load_cfg(
            {
                "LLM_AA5_SYSTEM_PROMPT": "aa5 prompt",
                "LLM_AA3_SYSTEM_PROMPT": "aa3 prompt",
            }
        )
        self.assertEqual(cfg["aa5_system_prompt"], "aa5 prompt")
        self.assertEqual(cfg["aa3_system_prompt"], "aa3 prompt")

    def test_reasoning_mode_does_not_replace_mimo_with_deepseek(self) -> None:
        overrides = postcheck_runtime_overrides(
            True,
            {
                "LLM_PROVIDER": "mimo",
                "MIMO_MODEL": "mimo-v2.5-pro",
            },
        )

        self.assertEqual(overrides, {})


if __name__ == "__main__":
    unittest.main()
