from __future__ import annotations

import unittest

from backend.domain.alignment.postcheck import aminoacyl_joint, anticodon, common


class PostcheckKeepCurrentTest(unittest.TestCase):
    def test_standard_loop_stops_on_keep_current(self) -> None:
        calls = {"http": 0, "evaluate": 0}
        messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}]
        attempt_logs = []

        def _http_post_json(_url: str, _payload: dict, _cfg: dict):
            calls["http"] += 1
            return True, '{"decision":"keep_current","reason":"already optimal"}'

        def _parse_candidate(_raw: str, _expected_len: int):
            return None, "keep_current:already optimal"

        def _evaluate_candidate(_candidate):
            calls["evaluate"] += 1
            return {"accepted": False, "status": "not_improved"}

        candidate = common.run_standard_target_candidate_loop(
            cfg={
                "max_retries": 6,
                "mode": "openai",
                "model_name": "deepseek-chat",
                "base_url": "https://example.com",
                "max_tokens": 128,
                "streaming": False,
                "hard_budget_s": 10.0,
                "backoff_base_s": 0.01,
            },
            runtime_values=None,
            messages=messages,
            expected_len=3,
            attempt_logs=attempt_logs,
            http_post_json=_http_post_json,
            parse_candidate=_parse_candidate,
            evaluate_candidate=_evaluate_candidate,
            parse_feedback_builder=lambda status: f"bad:{status}",
            log_trunc=lambda text, _limit: text,
            fallback_model_resolver=lambda _runtime_values: "fallback",
        )

        self.assertIsNone(candidate)
        self.assertEqual(1, calls["http"])
        self.assertEqual(0, calls["evaluate"])
        self.assertEqual(2, len(messages))
        self.assertEqual("keep_current", attempt_logs[0]["status"])
        self.assertEqual("already optimal", attempt_logs[0]["message"])

    def test_normalize_trace_maps_keep_current_to_pass_through(self) -> None:
        block = {
            "region": "T loop + T stem",
            "lines": [
                ["49", "50", "51"],
                ["A", "U", "G"],
                ["A", "U", "G"],
            ],
            "meta": {},
        }
        trace = common.normalize_runtime_trace(
            {
                "status": "keep_current",
                "message": "already optimal",
                "llm_attempts": [{"attempt": 1, "status": "keep_current", "message": "already optimal"}],
            },
            region="T loop + T stem",
            input_block=block,
            output_block=block,
            run_llm=True,
            enabled=True,
        )

        self.assertEqual("keep_current", trace["raw_status"])
        self.assertEqual("pass_through_no_improvement", trace["final_status"])
        self.assertEqual(1, trace["attempts"])
        self.assertTrue(trace["llm_attempted"])
        self.assertEqual([], trace["rejection_reasons"])

    def test_anticodon_parser_accepts_keep_current_json(self) -> None:
        candidate, status = anticodon._parse_target_tokens(
            '{"decision":"keep_current","reason":"already optimal"}',
            3,
        )
        self.assertIsNone(candidate)
        self.assertEqual("keep_current:already optimal", status)

    def test_aminoacyl_joint_parser_accepts_keep_current_json(self) -> None:
        candidate, status = aminoacyl_joint._parse_joint_targets(
            '{"decision":"keep_current","reason":"already optimal"}',
            3,
            3,
        )
        self.assertIsNone(candidate)
        self.assertEqual("keep_current:already optimal", status)


if __name__ == "__main__":
    unittest.main()
