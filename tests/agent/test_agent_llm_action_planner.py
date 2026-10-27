from __future__ import annotations

import json

import pytest

from backend.agent.contracts import (
    AgentWorkState,
    AlignmentRequest,
    CandidateAction,
    DiagnosticsReport,
    DifficultyReport,
    RootSnapshot,
    RouteReport,
)
from backend.agent.orchestrator.llm_action_planner import (
    ConstrainedLLMActionPlanner,
    FixedActionPlanner,
    PlannerDecision,
)
from backend.agent.orchestrator.service import AgentAlignmentService
from backend.agent.evaluation.hard_guards import HardGuardResult
from backend.infra.llm.json_client import JsonCompletion, JsonCompletionError


def _action(action_id: str, action_type: str) -> CandidateAction:
    return CandidateAction(
        action_id=action_id,
        action_type=action_type,
        reason=f"test:{action_type}",
        success_condition="candidate_is_valid",
        rollback_condition="candidate_is_rejected",
    )


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return JsonCompletion(
            payload=dict(self.payload),
            provider="test-provider",
            model="test-model",
            status_code=200,
            elapsed_sec=0.01,
            usage={"total_tokens": 12},
        )


class _FailingClient:
    def complete(self, **kwargs):
        raise JsonCompletionError("simulated provider failure")


def _cm_work_state(
    *,
    posterior: float = 0.999,
    insertion_count: int = 0,
    model_group: str = "bact",
) -> AgentWorkState:
    position_provenance = [
        {"label": f"53i{idx + 1}", "label_kind": "insertion"}
        for idx in range(insertion_count)
    ]
    snapshot = RootSnapshot(
        template_name=f"{model_group}-num",
        csv_content="",
        numbering_row=["1", "2", "3", "4"],
        template_row=["A", "C", "G", "U"],
        sequence_row=["A", "C", "G", "U"],
        raw_meta={
            "numbering_backend": "cmalign",
            "numbering_model": {"group": model_group},
            "cm_score": {"average_posterior": posterior},
            "position_provenance": position_provenance,
        },
    )
    diagnostics = DiagnosticsReport()
    difficulty = DifficultyReport(
        difficulty_score=0.1,
        tier="easy",
        hard_case=False,
    )
    route = RouteReport(route="canonical", out_of_scope=False)
    return AgentWorkState.from_root(
        input_sequence="ACGU",
        request_flags={"use_llm": True},
        snapshot=snapshot,
        diagnostics=diagnostics,
        difficulty=difficulty,
        route=route,
        valid=True,
        reasons=[],
        score=10.0,
    )


def test_llm_planner_selects_only_an_allowed_tool():
    client = _FakeClient(
        {
            "decision": "tool",
            "action_id": "tool:legacy",
            "reason_code": "compare_reference_evidence",
        }
    )
    planner = ConstrainedLLMActionPlanner(client=client)
    actions = [
        _action("tool:cm", "align_numbering_cm"),
        _action("tool:legacy", "align_legacy_reference"),
    ]

    decision = planner.decide(
        request=AlignmentRequest(target_seq="ACGU", use_llm=True),
        phase="initial_tool_selection",
        observation={"input_type": "mature"},
        actions=actions,
    )

    assert decision.decision == "tool"
    assert decision.action_id == "tool:legacy"
    assert decision.planner_source == "llm"
    assert decision.llm_called is True
    assert decision.provider == "test-provider"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Never invent" in prompt
    assert "Sprinzl coordinate" in prompt


def test_out_of_allowlist_llm_action_is_rejected_and_falls_back():
    planner = ConstrainedLLMActionPlanner(
        client=_FakeClient(
            {
                "decision": "tool",
                "action_id": "tool:rewrite_all_coordinates",
                "reason_code": "unbounded_edit",
            }
        )
    )
    actions = [_action("tool:cm", "align_numbering_cm")]

    decision = planner.decide(
        request=AlignmentRequest(target_seq="ACGU", use_llm=True),
        phase="initial_tool_selection",
        observation={},
        actions=actions,
    )

    assert decision.action_id == "tool:cm"
    assert decision.planner_source == "deterministic_fallback"
    assert decision.llm_called is True
    assert "outside the Harness allowlist" in decision.fallback_reason


def test_planner_output_budget_is_configurable_without_changing_allowed_decisions():
    for budget in (512, 2048, 256 * 1024):
        client = _FakeClient({"decision": "accept", "reason_code": "evidence_sufficient"})
        decision = ConstrainedLLMActionPlanner(client=client).decide(
            request=AlignmentRequest(
                target_seq="ACGU", use_llm=True,
                runtime_values_override={"AGENT_PLANNER_MAX_TOKENS": budget},
            ),
            phase="soft_risk_review", observation={}, actions=[], allowed_outcomes=["accept"],
        )
        assert decision.decision == "accept"
        assert client.calls[0]["max_tokens"] == budget


def test_soft_risk_provider_failure_preserves_the_current_legal_candidate():
    planner = ConstrainedLLMActionPlanner(client=_FailingClient())
    actions = [_action("tool:legacy", "align_legacy_reference")]

    decision = planner.decide(
        request=AlignmentRequest(target_seq="ACGU", use_llm=True),
        phase="soft_risk_review",
        observation={"current_candidate": {"valid": True}},
        actions=actions,
        allowed_outcomes=["accept"],
    )

    assert decision.decision == "accept"
    assert decision.action_id == ""
    assert decision.planner_source == "deterministic_fallback"
    assert decision.llm_called is True
    assert "simulated provider failure" in decision.fallback_reason


def test_dual_trigger_soft_risk_evidence_uses_only_runtime_observations():
    request = AlignmentRequest(
        target_seq="ACGU",
        use_llm=True,
        runtime_values_override={"AGENT_LLM_TRIAGE_MODE": "dual_trigger"},
    )

    low_posterior = AgentAlignmentService._soft_risk_evidence(
        request,
        _cm_work_state(posterior=0.994),
    )
    insertion = AgentAlignmentService._soft_risk_evidence(
        request,
        _cm_work_state(posterior=0.999, insertion_count=1),
    )
    broad_cm = AgentAlignmentService._soft_risk_evidence(
        request,
        _cm_work_state(posterior=0.999, model_group="all"),
    )
    clean = AgentAlignmentService._soft_risk_evidence(
        request,
        _cm_work_state(posterior=0.999),
    )

    assert [item["code"] for item in low_posterior] == [
        "cm_average_posterior_below_threshold"
    ]
    assert [item["code"] for item in insertion] == [
        "cm_insertion_state_present"
    ]
    assert [item["code"] for item in broad_cm] == [
        "cross_domain_cm_requires_agent_review"
    ]
    assert clean == []


def test_soft_risk_review_adds_one_independent_reference_action():
    state = _cm_work_state(posterior=0.99)
    evidence = [{"code": "cm_average_posterior_below_threshold"}]

    actions = AgentAlignmentService._with_soft_risk_actions(
        [],
        work_state=state,
        evidence=evidence,
    )

    assert len(actions) == 1
    assert actions[0].action_type == "align_legacy_reference"
    assert actions[0].rollback_condition == (
        "retain_original_legal_candidate_on_failure_or_no_improvement"
    )
    assert actions[0].success_condition == (
        "candidate_passes_shared_hard_guards_and_exceeds_"
        "the_frozen_cross_backend_evidence_margin"
    )
    assert actions[0].params["soft_risk_reason_codes"] == [
        "cm_average_posterior_below_threshold"
    ]


def test_llm_disabled_is_a_zero_call_deterministic_ablation():
    client = _FakeClient({"decision": "accept"})
    planner = ConstrainedLLMActionPlanner(client=client)

    decision = planner.decide(
        request=AlignmentRequest(target_seq="ACGU", use_llm=False),
        phase="evidence_review",
        observation={},
        actions=[],
        allowed_outcomes=["accept"],
    )

    assert decision.decision == "accept"
    assert decision.planner_source == "deterministic_ablation"
    assert decision.llm_called is False
    assert client.calls == []


def test_fixed_planner_uses_same_action_allowlist_without_llm_call():
    planner = FixedActionPlanner()
    actions = [
        _action("tool:cm", "align_numbering_cm"),
        _action("tool:legacy", "align_legacy_reference"),
    ]

    decision = planner.decide(
        request=AlignmentRequest(target_seq="ACGU", use_llm=True),
        phase="initial_tool_selection",
        observation={"input_type": "mature"},
        actions=actions,
        allowed_outcomes=["accept"],
    )

    assert decision.decision == "tool"
    assert decision.action_id == "tool:cm"
    assert decision.planner_source == "fixed_policy"
    assert decision.llm_called is False


class _ScriptedActionPlanner:
    def __init__(self):
        self.calls = []

    def decide(self, *, phase, actions, allowed_outcomes, **kwargs):
        candidates = list(actions)
        self.calls.append((phase, [action.action_type for action in candidates]))
        selected = next(
            action
            for action in candidates
            if action.action_type == "align_legacy_reference"
        )
        return PlannerDecision(
            decision="tool",
            action_id=selected.action_id,
            reason_code="llm_selected_legacy_evidence",
            planner_source="llm",
            llm_called=True,
            provider="test-provider",
            model="test-model",
        )


class _RecordingExecutor:
    def __init__(self):
        self.actions = []

    def execute(self, **kwargs):
        action = kwargs["action"]
        self.actions.append(action.action_type)
        return RootSnapshot(
            template_name="legacy-template",
            csv_content="",
            numbering_row=["1", "2", "3", "4"],
            template_row=["A", "C", "G", "U"],
            sequence_row=["A", "C", "G", "U"],
            raw_meta={"numbering_backend": "legacy"},
            source_action=action.action_type,
        )


class _SoftReviewExecutor:
    def __init__(self):
        self.actions = []

    def execute(self, **kwargs):
        action = kwargs["action"]
        self.actions.append(action.action_type)
        backend = (
            "cmalign"
            if action.action_type == "align_numbering_cm"
            else "legacy"
        )
        return RootSnapshot(
            template_name=f"{backend}-template",
            csv_content="",
            numbering_row=["1", "2", "3", "4"],
            template_row=["A", "C", "G", "U"],
            sequence_row=["A", "C", "G", "U"],
            raw_meta={
                "numbering_backend": backend,
                "numbering_model": {"group": "bact"},
                "cm_score": {"average_posterior": 0.99},
                "position_provenance": [],
            },
            source_action=action.action_type,
        )


class _SoftReviewActionPlanner:
    def __init__(self):
        self.calls = []

    def decide(self, *, phase, actions, **kwargs):
        candidates = list(actions)
        self.calls.append((phase, [item.action_type for item in candidates]))
        selected = next(
            item
            for item in candidates
            if item.action_type == "align_legacy_reference"
        )
        return PlannerDecision(
            decision="tool",
            action_id=selected.action_id,
            reason_code="request_independent_reference",
            planner_source="llm",
            llm_called=True,
            provider="test-provider",
            model="test-model",
        )


class _SequentialSoftReviewActionPlanner:
    def __init__(self):
        self.calls = []

    def decide(self, *, phase, actions, **kwargs):
        candidates = list(actions)
        self.calls.append((phase, [item.action_type for item in candidates]))
        if len(self.calls) == 1:
            return PlannerDecision(
                decision="accept",
                reason_code="retain_first_review_candidate",
                planner_source="llm",
                llm_called=True,
                provider="test-provider",
                model="test-model",
            )
        selected = next(
            item
            for item in candidates
            if item.action_type == "align_legacy_reference"
        )
        return PlannerDecision(
            decision="tool",
            action_id=selected.action_id,
            reason_code="request_second_review_tool",
            planner_source="llm",
            llm_called=True,
            provider="test-provider",
            model="test-model",
        )


class _SequentialSoftReviewService(AgentAlignmentService):
    @classmethod
    def _soft_risk_evidence(cls, request, work_state):
        if str(work_state.current_snapshot.raw_meta.get("numbering_backend")) != "cmalign":
            return []
        return [
            {"code": "cm_acceptor_tail_boundary_ambiguity"},
            {"code": "cm_average_posterior_below_threshold"},
        ]


class _EmptyScientificPlanner:
    class _Context:
        deterministic_boundary_enabled = True
        regional_llm_candidate_enabled = False

        @staticmethod
        def summary():
            return {}

    def plan_from_work_state(self, request, work_state):
        return self._Context(), [], []


class _EasyDifficultyAssessor:
    @staticmethod
    def build_diagnostics(snapshot):
        return DiagnosticsReport()

    @staticmethod
    def assess(diagnostics, snapshot):
        return DifficultyReport(
            difficulty_score=0.1,
            tier="easy",
            hard_case=False,
        )


class _CanonicalRouteAssessor:
    @staticmethod
    def assess(snapshot, diagnostics):
        return RouteReport(route="canonical", out_of_scope=False)


class _AlwaysValidGuards:
    @staticmethod
    def validate(request, snapshot):
        return HardGuardResult(valid=True, reasons=[])


class _BackendRanker:
    @staticmethod
    def score(snapshot, diagnostics, route, guard):
        backend = str(dict(snapshot.raw_meta or {}).get("numbering_backend") or "")
        return 10.0 if backend == "cmalign" else 40.0


class _NoSpecialRoute:
    @staticmethod
    def run_if_applicable(request):
        return None


def test_llm_agent_selects_the_initial_scientific_tool():
    action_planner = _ScriptedActionPlanner()
    executor = _RecordingExecutor()
    service = AgentAlignmentService(
        action_planner=action_planner,
        branch_executor=executor,
    )
    request = AlignmentRequest(
        target_seq="ACGU",
        use_llm=True,
        input_type="mature",
        intron_status="absent",
        runtime_values_override={"AGENT_LLM_TRIAGE_MODE": "always"},
    )

    snapshot, trace, failures, planner_calls = service._initialize_root_from_agent(
        request
    )

    assert snapshot is not None
    assert executor.actions == ["align_legacy_reference"]
    assert failures == []
    assert planner_calls == 1
    decision = next(event for event in trace if event["stage"] == "agent_decision")
    assert decision["planner_source"] == "llm"
    assert decision["action_id"] == "initial:align_legacy_reference"


def test_uncertain_only_mode_starts_with_deterministic_cm_evidence():
    action_planner = _ScriptedActionPlanner()
    executor = _RecordingExecutor()
    service = AgentAlignmentService(
        action_planner=action_planner,
        branch_executor=executor,
    )
    request = AlignmentRequest(
        target_seq="ACGU",
        use_llm=True,
        input_type="mature",
        intron_status="absent",
    )

    snapshot, trace, failures, planner_calls = service._initialize_root_from_agent(
        request
    )

    assert snapshot is not None
    assert executor.actions == ["align_numbering_cm"]
    assert failures == []
    assert planner_calls == 0
    decision = next(event for event in trace if event["stage"] == "agent_decision")
    assert decision["planner_source"] == "harness_fast_path"


def test_dual_trigger_mode_starts_with_deterministic_cm_evidence():
    action_planner = _ScriptedActionPlanner()
    executor = _RecordingExecutor()
    service = AgentAlignmentService(
        action_planner=action_planner,
        branch_executor=executor,
    )
    request = AlignmentRequest(
        target_seq="ACGU",
        use_llm=True,
        input_type="mature",
        intron_status="absent",
        runtime_values_override={"AGENT_LLM_TRIAGE_MODE": "dual_trigger"},
    )

    snapshot, trace, failures, planner_calls = service._initialize_root_from_agent(
        request
    )

    assert snapshot is not None
    assert executor.actions == ["align_numbering_cm"]
    assert failures == []
    assert planner_calls == 0
    decision = next(event for event in trace if event["stage"] == "agent_decision")
    assert decision["planner_source"] == "harness_fast_path"


def test_dual_trigger_rejects_a_worse_reference_candidate_and_keeps_cm_root():
    action_planner = _SoftReviewActionPlanner()
    executor = _SoftReviewExecutor()
    service = AgentAlignmentService(
        difficulty_assessor=_EasyDifficultyAssessor(),
        route_assessor=_CanonicalRouteAssessor(),
        planner=_EmptyScientificPlanner(),
        action_planner=action_planner,
        branch_executor=executor,
        hard_guards=_AlwaysValidGuards(),
        online_ranker=_BackendRanker(),
        special_route_service=_NoSpecialRoute(),
    )

    result = service.run(
        AlignmentRequest(
            target_seq="ACGU",
            use_llm=True,
            input_type="mature",
            domain_hint="bacteria",
            intron_status="absent",
            runtime_values_override={
                "AGENT_LLM_TRIAGE_MODE": "dual_trigger",
                "AGENT_ENABLE_PLANNING": True,
            },
        )
    )

    assert executor.actions == [
        "align_numbering_cm",
        "align_legacy_reference",
    ]
    assert action_planner.calls == [
        ("soft_risk_review", ["align_legacy_reference"])
    ]
    assert result.best_snapshot.raw_meta["numbering_backend"] == "cmalign"
    assert result.work_state_summary["action_history"][0]["accepted"] is False
    assert result.work_state_summary["soft_risk_review"]["eligible"] is True
    assert result.work_state_summary["soft_risk_review"]["decision"]["decision"] == "tool"


def test_accepting_one_soft_risk_batch_does_not_skip_the_next_batch():
    action_planner = _SequentialSoftReviewActionPlanner()
    executor = _SoftReviewExecutor()
    service = _SequentialSoftReviewService(
        difficulty_assessor=_EasyDifficultyAssessor(),
        route_assessor=_CanonicalRouteAssessor(),
        planner=_EmptyScientificPlanner(),
        action_planner=action_planner,
        branch_executor=executor,
        hard_guards=_AlwaysValidGuards(),
        online_ranker=_BackendRanker(),
        special_route_service=_NoSpecialRoute(),
    )

    result = service.run(
        AlignmentRequest(
            target_seq="ACGU",
            use_llm=True,
            input_type="mature",
            domain_hint="bacteria",
            intron_status="absent",
            runtime_values_override={
                "AGENT_LLM_TRIAGE_MODE": "dual_trigger",
                "AGENT_ENABLE_PLANNING": True,
            },
        )
    )

    assert action_planner.calls == [
        ("soft_risk_review", ["repair_acceptor_tail_with_structure"]),
        ("soft_risk_review", ["align_legacy_reference"]),
    ]
    assert executor.actions == ["align_numbering_cm", "align_legacy_reference"]
    review = result.work_state_summary["soft_risk_review"]
    assert review["reviewed_reason_codes"] == [
        "cm_acceptor_tail_boundary_ambiguity",
        "cm_average_posterior_below_threshold",
    ]
    retained = [
        event
        for event in result.trace_events
        if event["stage"] == "soft_risk_candidate_retained"
    ]
    assert retained[0]["reason_codes"] == [
        "cm_acceptor_tail_boundary_ambiguity"
    ]


def test_cross_backend_candidate_requires_the_frozen_score_margin():
    request = AlignmentRequest(
        target_seq="ACGU",
        runtime_values_override={
            "AGENT_SOFT_RISK_MIN_CROSS_BACKEND_SCORE_GAIN": 51.0,
        },
    )
    parent = _cm_work_state().current_snapshot
    candidate = RootSnapshot(
        template_name="legacy-template",
        csv_content="",
        raw_meta={"numbering_backend": "template_regional_dp"},
    )
    action = _action("tool:legacy", "align_legacy_reference")

    minimum_gain = AgentAlignmentService._candidate_minimum_score_gain(
        request=request,
        parent_snapshot=parent,
        candidate_snapshot=candidate,
        action=action,
    )

    assert minimum_gain == 51.0
    assert AgentAlignmentService._should_accept_candidate(
        current_valid=True,
        current_score=80.0,
        candidate_valid=True,
        candidate_score=131.0,
        minimum_score_gain=minimum_gain,
    ) is False
    assert AgentAlignmentService._should_accept_candidate(
        current_valid=True,
        current_score=80.0,
        candidate_valid=True,
        candidate_score=131.01,
        minimum_score_gain=minimum_gain,
    ) is True


def test_dp_ablation_does_not_expose_cm_as_its_default_initial_tool():
    service = AgentAlignmentService()
    actions = service._initial_root_actions(
        AlignmentRequest(
            target_seq="ACGU",
            use_llm=False,
            input_type="mature",
        )
    )

    assert [action.action_type for action in actions] == ["align_legacy_reference"]


def test_unknown_input_exposes_cm_and_legacy_tools_to_llm_planner():
    service = AgentAlignmentService()
    actions = service._initial_root_actions(
        AlignmentRequest(
            target_seq="ACGU",
            use_llm=True,
            input_type="unknown",
        )
    )

    assert [action.action_type for action in actions] == [
        "align_numbering_cm",
        "align_legacy_reference",
    ]


class _ScriptedClient:
    """Returns one payload per call, so a retry path can be observed."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        return JsonCompletion(
            payload=dict(payload),
            provider="test-provider",
            model="test-model",
            status_code=200,
            elapsed_sec=0.01,
            usage={"total_tokens": 12},
        )


def test_planner_only_offers_the_outcomes_this_state_allows():
    client = _ScriptedClient([{"decision": "accept"}])
    ConstrainedLLMActionPlanner(client=client).decide(
        request=AlignmentRequest(target_seq="ACGU", use_llm=True),
        phase="evidence_review",
        observation={},
        actions=[_action("tool:cm", "align_numbering_cm")],
        allowed_outcomes=["accept"],
    )
    messages = client.calls[0]["messages"]
    body = json.loads(messages[-1]["content"])
    assert body["response_schema"]["decision"] == "tool | accept"
    assert body["allowed_outcomes"] == ["accept"]


def test_terminal_outcome_outside_the_allowlist_gets_one_corrective_retry():
    # A valid candidate allows only "accept"; an answer of "investigate" is a
    # protocol slip, so the planner is asked once more instead of losing the case.
    client = _ScriptedClient(
        [{"decision": "investigate", "reason_code": "want_more_evidence"},
         {"decision": "accept", "reason_code": "candidate_is_legal"}]
    )
    decision = ConstrainedLLMActionPlanner(client=client).decide(
        request=AlignmentRequest(target_seq="ACGU", use_llm=True),
        phase="evidence_review",
        observation={},
        actions=[],
        allowed_outcomes=["accept"],
    )

    assert len(client.calls) == 2
    assert decision.decision == "accept"
    assert decision.planner_source == "llm"
    retry_note = client.calls[1]["messages"][-1]["content"]
    assert "outside the Harness allowlist" in retry_note
    assert "allowed: accept" in retry_note


def test_repeated_protocol_slip_still_falls_back_under_the_harness():
    client = _ScriptedClient([{"decision": "investigate"}])
    decision = ConstrainedLLMActionPlanner(client=client).decide(
        request=AlignmentRequest(target_seq="ACGU", use_llm=True),
        phase="evidence_review",
        observation={},
        actions=[_action("tool:cm", "align_numbering_cm")],
        allowed_outcomes=["accept"],
    )

    assert len(client.calls) == 2
    assert decision.planner_source == "deterministic_fallback"
    assert decision.action_id == "tool:cm"


def test_strict_run_reports_the_rejected_answer_instead_of_a_bare_error():
    client = _ScriptedClient([{"decision": "investigate", "reason_code": "x"}])
    with pytest.raises(JsonCompletionError) as raised:
        ConstrainedLLMActionPlanner(client=client).decide(
            request=AlignmentRequest(
                target_seq="ACGU",
                use_llm=True,
                runtime_values_override={"AGENT_STRICT_LLM_FAILURES": True},
            ),
            phase="evidence_review",
            observation={},
            actions=[],
            allowed_outcomes=["accept"],
        )

    assert len(client.calls) == 2
    assert "allowed: accept" in str(raised.value)
