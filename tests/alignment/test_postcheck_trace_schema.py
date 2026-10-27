from __future__ import annotations

import unittest
from unittest import mock

from backend.domain.alignment import llm_postcheck


def _make_block(region: str = "D loop + D stem") -> dict:
    return {
        "region": region,
        "lines": [
            ["10", "11", "12"],
            ["A", "U", "G"],
            ["A", "-", "G"],
        ],
        "meta": {},
    }


class PostcheckTraceSchemaTest(unittest.TestCase):
    def test_use_llm_false_normalizes_disabled_trace(self) -> None:
        block = _make_block()
        out = llm_postcheck.postcheck_alignment([block], use_llm=False)
        trace = out[0]["meta"]["llm_runtime_trace"]

        self.assertEqual("skipped_use_llm_off", trace["raw_status"])
        self.assertEqual("pass_through_disabled", trace["final_status"])
        self.assertEqual(0, trace["attempts"])
        self.assertFalse(trace["llm_attempted"])
        self.assertIsNone(trace["fallback_used"])
        self.assertIsNone(trace["accepted_candidate"])
        self.assertEqual(["A", "-", "G"], trace["input_block"]["target_tokens"])

    def test_fallback_chat_acceptance_is_normalized(self) -> None:
        block = _make_block()

        def _handler(_blk: dict) -> dict:
            return {
                "region": _blk["region"],
                "lines": [
                    list(_blk["lines"][0]),
                    list(_blk["lines"][1]),
                    ["A", "U", "G"],
                ],
                "meta": {
                    "llm_runtime_trace": {
                        "region": _blk["region"],
                        "status": "accepted",
                        "message": "Applied improved TARGET from LLM.",
                        "llm_attempts": [{"attempt": "fallback_chat", "status": "accepted"}],
                    }
                },
            }

        with mock.patch.dict(llm_postcheck._REGION_HANDLERS, {"D loop + D stem": _handler}, clear=False):
            out = llm_postcheck.postcheck_alignment(
                [block],
                use_llm=True,
                runtime_values_override={"AGENT_ENABLE_LLM_POSTCHECK": True},
            )

        trace = out[0]["meta"]["llm_runtime_trace"]
        self.assertEqual("accepted", trace["raw_status"])
        self.assertEqual("accepted_fallback_chat", trace["final_status"])
        self.assertEqual("fallback_chat", trace["fallback_used"])
        self.assertEqual(1, trace["attempts"])
        self.assertTrue(trace["llm_attempted"])
        self.assertEqual(["A", "U", "G"], trace["accepted_candidate"]["target_tokens"])

    def test_deterministic_acceptance_is_normalized(self) -> None:
        block = _make_block()

        def _handler(_blk: dict) -> dict:
            return {
                "region": _blk["region"],
                "lines": [
                    list(_blk["lines"][0]),
                    list(_blk["lines"][1]),
                    ["A", "U", "G"],
                ],
                "meta": {
                    "llm_runtime_trace": {
                        "region": _blk["region"],
                        "status": "fallback_deterministic",
                        "message": "Deterministic allocator applied.",
                        "llm_attempts": [],
                    }
                },
            }

        with mock.patch.dict(llm_postcheck._REGION_HANDLERS, {"D loop + D stem": _handler}, clear=False):
            out = llm_postcheck.postcheck_alignment(
                [block],
                use_llm=True,
                runtime_values_override={"AGENT_ENABLE_LLM_POSTCHECK": True},
            )

        trace = out[0]["meta"]["llm_runtime_trace"]
        self.assertEqual("fallback_deterministic", trace["raw_status"])
        self.assertEqual("accepted_deterministic", trace["final_status"])
        self.assertEqual("deterministic", trace["fallback_used"])
        self.assertEqual(0, trace["attempts"])
        self.assertFalse(trace["llm_attempted"])
        self.assertEqual(["A", "U", "G"], trace["accepted_candidate"]["target_tokens"])

    def test_handler_error_is_normalized(self) -> None:
        block = _make_block()

        def _boom(_blk: dict) -> dict:
            raise RuntimeError("boom")

        with mock.patch.dict(llm_postcheck._REGION_HANDLERS, {"D loop + D stem": _boom}, clear=False):
            out = llm_postcheck.postcheck_alignment(
                [block],
                use_llm=True,
                runtime_values_override={"AGENT_ENABLE_LLM_POSTCHECK": True},
            )

        trace = out[0]["meta"]["llm_runtime_trace"]
        self.assertEqual("handler_error_pass_through", trace["raw_status"])
        self.assertEqual("pass_through_handler_error", trace["final_status"])
        self.assertEqual("handler_error", trace["fallback_used"])
        self.assertIn("boom", " ".join(trace["rejection_reasons"]))
        self.assertIsNone(trace["accepted_candidate"])

    def test_handler_owned_final_status_is_preserved(self) -> None:
        block = _make_block()

        def _handler(_blk: dict) -> dict:
            return {
                "region": _blk["region"],
                "lines": [
                    list(_blk["lines"][0]),
                    list(_blk["lines"][1]),
                    ["A", "U", "G"],
                ],
                "meta": {
                    "llm_runtime_trace": {
                        "region": _blk["region"],
                        "status": "accepted",
                        "raw_status": "accepted",
                        "final_status": "accepted_fallback_chat",
                        "fallback_used": "fallback_chat",
                        "attempts": 1,
                        "llm_attempted": True,
                        "rejection_reasons": [],
                        "accepted_candidate": {
                            "region": _blk["region"],
                            "ids": ["10", "11", "12"],
                            "template_tokens": ["A", "U", "G"],
                            "target_tokens": ["A", "U", "G"],
                        },
                        "output_changed": True,
                        "message": "Applied improved TARGET from fallback chat.",
                        "llm_attempts": [{"attempt": "fallback_chat", "status": "accepted"}],
                    }
                },
            }

        with mock.patch.dict(llm_postcheck._REGION_HANDLERS, {"D loop + D stem": _handler}, clear=False):
            out = llm_postcheck.postcheck_alignment(
                [block],
                use_llm=True,
                runtime_values_override={"AGENT_ENABLE_LLM_POSTCHECK": True},
            )

        trace = out[0]["meta"]["llm_runtime_trace"]
        self.assertEqual("accepted", trace["raw_status"])
        self.assertEqual("accepted_fallback_chat", trace["final_status"])
        self.assertEqual("fallback_chat", trace["fallback_used"])
        self.assertEqual(1, trace["attempts"])
        self.assertTrue(trace["llm_attempted"])
        self.assertTrue(trace["output_changed"])


if __name__ == "__main__":
    unittest.main()
