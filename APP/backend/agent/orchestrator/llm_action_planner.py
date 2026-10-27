from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

from backend.domain.alignment.runtime_helpers import runtime_int
from backend.domain.alignment.sprinzl_numbering_manual import manual_text, planner_brief
from ..contracts import AlignmentRequest, CandidateAction
from backend.infra.llm.json_client import (
    JsonCompletionError,
    JsonTruncatedError,
    StructuredJsonLLMClient,
)
from backend.infra.llm.provider_config import MAX_COMPLETION_TOKENS
from .progress import emit_progress


_OUTCOMES = {"accept", "investigate", "abstain"}
_REASON_CODE_RE = re.compile(r"[^a-z0-9_:-]+")

# A planner answer is three short fields.  The gateway thinks before it answers,
# so an uncapped budget lets that reasoning run to a few thousand tokens for a
# decision that needs none of it; 2048 keeps every baseline call that ever
# answered and bounds the tail.
DEFAULT_PLANNER_MAX_TOKENS = 6144


class PlannerProtocolError(ValueError):
    """The model answered, but the answer is not a decision the Harness offers.

    Carrying the payload separately matters: a protocol slip is worth one
    corrective retry, whereas a transport failure is not, and the raw answer is
    the only thing that says which of the two happened.
    """

    def __init__(self, message: str, *, payload: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.payload = dict(payload or {})


_POLICY_PROMPT = (
    "You are the constrained tool-selection policy inside a scientific tRNA "
    "alignment Harness. Choose exactly one listed action_id or one allowed "
    "terminal outcome. Never invent a tool, Sprinzl coordinate, sequence edit, "
    "residue mapping, or hidden action. Hard guards and final acceptance belong "
    "to the Harness. Return one JSON object only. Do not provide chain-of-thought."
)


@dataclass(frozen=True)
class PlannerDecision:
    decision: str
    action_id: str = ""
    reason_code: str = ""
    planner_source: str = ""
    llm_called: bool = False
    provider: str = ""
    model: str = ""
    elapsed_sec: float | None = None
    fallback_reason: str = ""

    def trace(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "action_id": self.action_id,
            "reason_code": self.reason_code,
            "planner_source": self.planner_source,
            "llm_called": self.llm_called,
            "provider": self.provider,
            "model": self.model,
            "elapsed_sec": self.elapsed_sec,
            "fallback_reason": self.fallback_reason,
        }


class ConstrainedLLMActionPlanner:
    """Select one Harness-allowed action or terminal disposition."""

    def __init__(self, client: StructuredJsonLLMClient | None = None) -> None:
        self.client = client or StructuredJsonLLMClient()

    def decide(
        self,
        *,
        request: AlignmentRequest,
        phase: str,
        observation: Mapping[str, Any],
        actions: Iterable[CandidateAction],
        allowed_outcomes: Iterable[str] = (),
    ) -> PlannerDecision:
        candidates = list(actions)
        outcomes = [item for item in allowed_outcomes if item in _OUTCOMES]
        prefer_safe_outcome = str(phase or "") == "soft_risk_review"
        if not request.use_llm:
            return self._fallback(
                candidates,
                outcomes,
                source="deterministic_ablation",
                reason="llm_disabled_by_request",
                llm_called=False,
                prefer_outcome=prefer_safe_outcome,
            )

        try:
            attempts = self._protocol_attempts(request)
            correction = ""
            token_budget = runtime_int(
                "AGENT_PLANNER_MAX_TOKENS",
                DEFAULT_PLANNER_MAX_TOKENS,
                128,
                MAX_COMPLETION_TOKENS,
                runtime_values=dict(request.runtime_values_override or {}),
            )
            for attempt in range(1, attempts + 1):
                try:
                    completion = self.client.complete(
                        messages=self._messages(
                            phase=phase,
                            observation=observation,
                            actions=candidates,
                            allowed_outcomes=outcomes,
                            manual_mode=self._numbering_manual_mode(request),
                            correction=correction,
                        ),
                        runtime_values_override={
                            **dict(request.runtime_values_override or {}),
                            "_status_callback": request.status_callback,
                        },
                        source="agent_planner",
                        model_name=self._planner_model(request),
                        max_tokens=token_budget,
                    )
                except JsonTruncatedError as truncated:
                    # Running out of completion budget is a technical failure,
                    # and the fix is the thing that failed: the answer needs
                    # more room. Losing the case here would blame the model for
                    # the gateway's stop reason.
                    if attempt >= attempts:
                        raise
                    emit_progress(
                        request,
                        {
                            "stage": "llm_truncated",
                            "attempt": attempt,
                            "completion_budget": token_budget,
                            "error": str(truncated)[:240],
                        },
                    )
                    token_budget = min(
                        MAX_COMPLETION_TOKENS,
                        max(1024, token_budget * 3),
                    )
                    continue
                try:
                    decision = self._validated_decision(
                        completion.payload,
                        actions=candidates,
                        allowed_outcomes=outcomes,
                    )
                except PlannerProtocolError as protocol_exc:
                    if attempt >= attempts:
                        raise
                    # Tell the model what it may actually answer.  The rejected
                    # answer goes in the progress stream, so the trace shows the
                    # retry was earned rather than silently swallowed.
                    emit_progress(
                        request,
                        {
                            "stage": "llm_protocol_retry",
                            "attempt": attempt,
                            "error": str(protocol_exc),
                            "answered": _compact_payload(protocol_exc.payload),
                        },
                    )
                    correction = str(protocol_exc)
                    continue
                return PlannerDecision(
                    decision=decision["decision"],
                    action_id=decision.get("action_id", ""),
                    reason_code=decision.get("reason_code", ""),
                    planner_source="llm",
                    llm_called=True,
                    provider=completion.provider,
                    model=completion.model,
                    elapsed_sec=round(float(completion.elapsed_sec), 4),
                )
            raise AssertionError("unreachable planner loop")
        except (JsonCompletionError, ValueError, TypeError, KeyError) as exc:
            # A truncated answer is reported as itself: it is a budget problem,
            # not a model that chose a tool the Harness never offered.
            stage = (
                "llm_truncated"
                if isinstance(exc, JsonTruncatedError)
                else "llm_decision_failed"
            )
            emit_progress(request, {"stage": stage, "error_type": type(exc).__name__})
            if (request.runtime_values_override or {}).get("AGENT_STRICT_LLM_FAILURES"):
                raise JsonCompletionError(
                    f"Strict planner failure: {type(exc).__name__}: {exc}"
                ) from exc
            return self._fallback(
                candidates,
                outcomes,
                source="deterministic_fallback",
                reason=f"{type(exc).__name__}:{exc}",
                llm_called=True,
                prefer_outcome=prefer_safe_outcome,
            )

    @staticmethod
    def _messages(
        *,
        phase: str,
        observation: Mapping[str, Any],
        actions: List[CandidateAction],
        allowed_outcomes: List[str],
        manual_mode: str = "brief",
        correction: str = "",
    ) -> List[Dict[str, str]]:
        action_payload = [
            {
                "action_id": action.action_id,
                "tool": action.action_type,
                "scope": action.scope,
                "target_region": action.target_region,
                "reason": action.reason,
                "success_condition": action.success_condition,
                "rollback_condition": action.rollback_condition,
                "budget": {
                    "llm_calls_reserved": action.budget_cost.llm_calls_reserved,
                    "expected_wall_time_sec": action.budget_cost.expected_wall_time_sec,
                },
            }
            for action in actions
        ]
        user_payload = {
            "phase": str(phase or "planning"),
            "observation": dict(observation or {}),
            "allowed_actions": action_payload,
            "allowed_outcomes": allowed_outcomes,
            # State only the outcomes this round can end in. Listing all three
            # while allowed_outcomes holds one of them is what made the model
            # answer with an outcome the Harness had not offered.
            "response_schema": {
                "decision": " | ".join(["tool", *allowed_outcomes]),
                "action_id": "required only when decision=tool",
                "reason_code": "short machine-readable code",
            },
        }
        messages = [
            {
                "role": "system",
                # The reference block is the one stable part of the prompt: it
                # states the coordinate system and the rules the Harness applies
                # to whichever action is chosen.  ``full`` repeats the whole
                # manual, ``brief`` gives the digest a choice actually needs,
                # ``off`` reproduces the bare tool-selection policy.
                "content": (
                    f"{_POLICY_PROMPT}\n\n{manual_text()}"
                    if manual_mode == "full"
                    else (
                        f"{_POLICY_PROMPT}\n\n{planner_brief()}"
                        if manual_mode == "brief"
                        else _POLICY_PROMPT
                    )
                ),
            },
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=True, sort_keys=True),
            },
        ]
        if correction:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Rejected answer: {correction}. Reply with one JSON "
                        "object whose decision is exactly one of: "
                        + ", ".join(["tool", *allowed_outcomes])
                        + "."
                    ),
                }
            )
        return messages

    @classmethod
    def _validated_decision(
        cls,
        payload: Mapping[str, Any],
        *,
        actions: List[CandidateAction],
        allowed_outcomes: List[str],
    ) -> Dict[str, str]:
        raw_decision = str(payload.get("decision") or "").strip().lower()
        aliases = {"execute": "tool", "action": "tool", "stop_accept": "accept"}
        decision = aliases.get(raw_decision, raw_decision)
        reason_code = cls._reason_code(payload.get("reason_code"))
        if decision == "tool":
            action_id = str(payload.get("action_id") or "").strip()
            allowed_ids = {action.action_id for action in actions}
            if not action_id or action_id not in allowed_ids:
                raise PlannerProtocolError(
                    "LLM selected an action outside the Harness allowlist",
                    payload=payload,
                )
            return {
                "decision": "tool",
                "action_id": action_id,
                "reason_code": reason_code or "selected_allowed_tool",
            }
        if decision not in allowed_outcomes:
            raise PlannerProtocolError(
                "LLM selected a terminal outcome outside the Harness allowlist"
                f" (answered {decision or raw_decision or 'empty'};"
                f" allowed: {', '.join(allowed_outcomes) or 'none'})",
                payload=payload,
            )
        return {
            "decision": decision,
            "action_id": "",
            "reason_code": reason_code or f"selected_{decision}",
        }

    @classmethod
    def _fallback(
        cls,
        actions: List[CandidateAction],
        allowed_outcomes: List[str],
        *,
        source: str,
        reason: str,
        llm_called: bool,
        prefer_outcome: bool = False,
    ) -> PlannerDecision:
        if prefer_outcome and allowed_outcomes:
            outcome = allowed_outcomes[0]
            return PlannerDecision(
                decision=outcome,
                reason_code=f"harness_{outcome}",
                planner_source=source,
                llm_called=llm_called,
                fallback_reason=str(reason or ""),
            )
        if actions:
            return PlannerDecision(
                decision="tool",
                action_id=actions[0].action_id,
                reason_code="harness_first_allowed_action",
                planner_source=source,
                llm_called=llm_called,
                fallback_reason=str(reason or ""),
            )
        outcome = allowed_outcomes[0] if allowed_outcomes else "abstain"
        return PlannerDecision(
            decision=outcome,
            reason_code=f"harness_{outcome}",
            planner_source=source,
            llm_called=llm_called,
            fallback_reason=str(reason or ""),
        )

    @staticmethod
    def _reason_code(raw: Any) -> str:
        value = str(raw or "").strip().lower().replace(" ", "_")
        value = _REASON_CODE_RE.sub("", value)
        return value[:96]

    @staticmethod
    def _protocol_attempts(request: AlignmentRequest) -> int:
        """How many answers the planner may give before the Harness gives up."""
        values = dict(request.runtime_values_override or {})
        raw = values.get("AGENT_PLANNER_PROTOCOL_ATTEMPTS", 2)
        try:
            return max(1, min(int(raw), 3))
        except (TypeError, ValueError):
            return 2

    @staticmethod
    def _planner_model(request: AlignmentRequest) -> str:
        values = dict(request.runtime_values_override or {})
        explicit = str(values.get("AGENT_PLANNER_MODEL") or "").strip()
        if explicit:
            return explicit
        if request.llm_reasoning:
            reasoning_model = str(values.get("MINIMAX_REASONING_MODEL") or "").strip()
            if reasoning_model:
                return reasoning_model
        return ""

    @staticmethod
    def _numbering_manual_mode(request: AlignmentRequest) -> str:
        # How much of the coordinate reference the planner sees is part of the
        # recorded method, so an ablation can state it explicitly.
        values = dict(request.runtime_values_override or {})
        raw = values.get("AGENT_NUMBERING_MANUAL")
        if isinstance(raw, bool):
            return "full" if raw else "off"
        text = str(raw if raw is not None else "brief").strip().lower()
        if text in {"1", "true", "yes", "on", "full", "all"}:
            return "full"
        if text in {"0", "false", "no", "off", "none"}:
            return "off"
        return "brief"
class FixedActionPlanner:
    """Deterministic selector used as the same-toolbox planner ablation."""

    def decide(
        self,
        *,
        request: AlignmentRequest,
        phase: str,
        observation: Mapping[str, Any],
        actions: Iterable[CandidateAction],
        allowed_outcomes: Iterable[str] = (),
    ) -> PlannerDecision:
        candidates = list(actions)
        outcomes = [item for item in allowed_outcomes if item in _OUTCOMES]
        if candidates:
            return PlannerDecision(
                decision="tool",
                action_id=candidates[0].action_id,
                reason_code="fixed_first_allowed_action",
                planner_source="fixed_policy",
                llm_called=False,
            )
        outcome = outcomes[0] if outcomes else "abstain"
        return PlannerDecision(
            decision=outcome,
            reason_code=f"fixed_{outcome}",
            planner_source="fixed_policy",
            llm_called=False,
        )


def _compact_payload(payload: Mapping[str, Any], *, limit: int = 240) -> str:
    """Short, single-line form of a rejected answer, for the trace."""
    try:
        text = json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(payload)
    return text[:limit]


__all__ = [
    "ConstrainedLLMActionPlanner",
    "FixedActionPlanner",
    "PlannerProtocolError",
    "PlannerDecision",
]
