from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.domain.alignment.postcheck import variable_loop
from backend.domain.alignment.postcheck.variable_loop import (
    _alloc_deterministic,
    _check_pairing,
    _check_var_target_shape_and_order,
    rebuild_variable_block_topology,
)


class VariableLoopPriorityTest(unittest.TestCase):
    def test_non_stream_openai_envelope_is_unwrapped_before_var_target_parsing(self) -> None:
        nums = ["44", "45", "46", "47", "48"]
        target = ["A", "U", "G", "-", "C"]
        expected = ["A", "U", "G", "-", "C"]
        envelope = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"var_target": expected}),
                        }
                    }
                ]
            }
        )
        cfg = {
            "mode": "openai",
            "provider": "mimo",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model_name": "mimo-v2.5-pro",
            "streaming": False,
            "max_tokens": 128,
            "max_retries": 0,
            "hard_budget_s": 10.0,
            "backoff_base_s": 0.0,
            "varloop_system_prompt": "",
            "varloop_user_prompt_suffix": "",
        }
        attempts = []

        with patch.object(
            variable_loop,
            "_http_post_json",
            return_value=(True, envelope),
        ) as http_post:
            result = variable_loop._llm_propose_var_target_with_feedback(
                nums,
                target,
                pair_hint=0,
                var_struct_tokens=["."] * len(nums),
                global_struct_summary=None,
                cfg=cfg,
                attempt_logs=attempts,
            )

        self.assertEqual(expected, result)
        self.assertEqual(1, http_post.call_count)
        self.assertEqual("accepted", attempts[0]["status"])

    def test_unpaired_structure_uses_deterministic_rule_without_llm_call(self) -> None:
        nums = ["44", "45", "V11", "V1", "V21", "46", "47", "48"]
        target = ["A", "U", "G", "C", "U", "A", "G", "C"]
        block = {
            "region": "Variable loop",
            "lines": [nums, ["-"] * len(nums), target],
            "meta": {"input_struct": "." * len(target)},
        }

        with patch.object(
            variable_loop,
            "_llm_propose_var_target_with_feedback",
            side_effect=AssertionError("LLM must not run for pair_hint=0"),
        ) as proposer:
            result = variable_loop.postcheck_variable_loop_block(
                block,
                runtime_values={
                    "LLM_POSTCHECK": True,
                    "LLM_VARLOOP_SYSTEM_PROMPT": "Return JSON.",
                },
            )

        trace = result["meta"]["llm_runtime_trace"]
        self.assertEqual(0, proposer.call_count)
        self.assertEqual("deterministic_structure_rule", trace["raw_status"])
        self.assertEqual("accepted_deterministic", trace["final_status"])
        self.assertFalse(trace["llm_attempted"])

    def test_shape_check_rejects_v_extension_before_core_labels(self) -> None:
        nums = ["44", "45", "46", "47", "48", "V1"]
        targ_before = ["A", "U", "G", "C", "-", "-"]
        var_target = ["A", "-", "U", "G", "-", "C"]

        ok, err = _check_var_target_shape_and_order(var_target, nums, targ_before)

        self.assertFalse(ok)
        self.assertIn("standard variable-loop labels", err)

    def test_deterministic_allocator_fills_core_before_v_loop(self) -> None:
        nums = ["44", "45", "46", "47", "48", "V1", "V2"]
        targ_before = ["A", "-", "-", "-", "-", "U", "G"]

        targ_after = _alloc_deterministic(nums, targ_before)

        self.assertEqual(targ_after, ["A", "-", "U", "-", "G", "-", "-"])

    def test_shape_check_allows_v_loop_when_supported_by_filled_v_stem_pairs(self) -> None:
        nums = ["44", "45", "V11", "V1", "V21", "46", "47", "48"]
        targ_before = ["A", "-", "U", "G", "C", "-", "-", "-"]
        var_target = ["A", "-", "U", "G", "C", "-", "-", "-"]

        ok, err = _check_var_target_shape_and_order(var_target, nums, targ_before)

        self.assertTrue(ok, err)

    def test_shape_check_rejects_left_stem_when_unpaired_region_can_use_later_labels(self) -> None:
        nums = ["44", "45", "V11", "V21", "V1", "V2", "V27", "46", "47", "48"]
        targ_before = ["A", "U", "G", "C", "-", "-", "-", "-", "-", "-"]
        var_target = ["A", "U", "G", "C", "-", "-", "-", "-", "-", "-"]

        ok, err = _check_var_target_shape_and_order(
            var_target,
            nums,
            targ_before,
            pair_hint=0,
        )

        self.assertFalse(ok)
        self.assertIn("exhaust V1..V5 before stem extension", err)

    def test_deterministic_allocator_keeps_long_variable_arm_exception(self) -> None:
        nums = ["44", "45", "V11", "V1", "V21", "46", "47", "48"]
        targ_before = ["A", "-", "U", "G", "C", "-", "-", "-"]

        targ_after = _alloc_deterministic(nums, targ_before, pair_hint=1)

        self.assertEqual(targ_after, ["A", "-", "U", "G", "C", "-", "-", "-"])

    def test_deterministic_allocator_uses_balanced_mirror_shells_when_unpaired_extension_is_needed(self) -> None:
        nums = ["44", "45", "V11", "V12", "V1", "V2", "V22", "V21", "46", "47", "48"]
        targ_before = ["A", "U", "G", "C", "U", "A", "G", "C", "A", "A", "C"]

        targ_after = _alloc_deterministic(nums, targ_before, pair_hint=0)

        occupied = [label for label, base in zip(nums, targ_after) if base != "-"]
        self.assertEqual(occupied, ["44", "45", "V11", "V12", "V1", "V2", "V22", "V21", "46", "47", "48"])

    def test_shape_check_rejects_right_heavy_unpaired_extension(self) -> None:
        nums = ["44", "45", "V11", "V12", "V13", "V14", "V1", "V2", "V24", "V23", "V22", "V21", "46", "47", "48"]
        targ_before = ["A", "U", "-", "-", "-", "-", "G", "C", "U", "A", "G", "C", "A", "A", "C"]
        var_target = ["A", "U", "-", "-", "-", "-", "G", "C", "U", "A", "G", "C", "A", "A", "C"]

        ok, err = _check_var_target_shape_and_order(var_target, nums, targ_before, pair_hint=0)

        self.assertFalse(ok)
        self.assertIn("stem extension must stay left/right balanced", err)

    def test_pairing_check_allows_one_sided_gap_on_existing_mirror_pair(self) -> None:
        nums = ["44", "45", "V14", "V24", "46", "47", "48"]
        targ = ["-", "-", "A", "-", "-", "-", "-"]

        ok, errs = _check_pairing(nums, targ)

        self.assertTrue(ok)
        self.assertEqual(errs, [])

    def test_topology_rebuild_can_remove_variable_insertions_and_keep_fixed_pairs(self) -> None:
        nums = [
            "44", "45", "V11", "V12", "V13", "V14", "V15", "V16", "V17",
            "V1", "V2", "V3", "V4", "V5", "V27", "V26", "V25", "V24", "V23", "V22", "V21",
            "46", "47", "47i1", "47i2", "48",
        ]
        templ = ["-"] * len(nums)
        targ = [
            "A", "G", "G", "G", "-", "G", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "C", "U", "C", "C", "G", "C", "C", "U", "C",
        ]

        rebuilt = rebuild_variable_block_topology(
            nums=nums,
            templ=templ,
            targ=targ,
            raw_struct="..>>>>...<<<<.",
        )

        self.assertIsNotNone(rebuilt)
        new_nums, _, new_targ = rebuilt
        self.assertNotIn("47i1", new_nums)
        self.assertNotIn("47i2", new_nums)
        self.assertEqual(
            [label for label, base in zip(new_nums, new_targ) if base != "-"],
            ["44", "45", "V11", "V12", "V13", "V14", "V1", "V2", "V3", "V24", "V23", "V22", "V21", "48"],
        )


if __name__ == "__main__":
    unittest.main()
