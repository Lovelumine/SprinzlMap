# -*- coding: utf-8 -*-
import sys
from copy import deepcopy

sys.path.insert(0, ".")

from backend.agent.contracts import (
    AgentWorkState,
    AlignmentRequest,
    CandidateAction,
    DiagnosticsReport,
    DifficultyReport,
    RootSnapshot,
    RouteReport,
)
from backend.agent.execution import BranchExecutor, RootSnapshotBuilder
from backend.agent.evaluation.hard_guards import HardGuards
from backend.agent.orchestrator.planner import AgentPlannerV2
from backend.domain.alignment.engine import STANDARD_ALIGNMENT_REGIONS


def _block(region: str, token: str) -> dict:
    return {
        "region": region,
        "lines": [
            [f"{token}1", f"{token}2"],
            ["A", "C"],
            ["A", "C"],
        ],
        "meta": {},
    }


def _snapshot_with_state() -> RootSnapshot:
    regions = {
        region: {
            "template_numbering": [f"{idx}", f"{idx + 1}"],
            "template_seq": f"T{idx}",
            "input_seq": f"I{idx}",
            "input_struct": "",
        }
        for idx, region in enumerate(STANDARD_ALIGNMENT_REGIONS, start=1)
    }
    blocks = [_block(region, str(idx)) for idx, region in enumerate(STANDARD_ALIGNMENT_REGIONS, start=1)]
    return RootSnapshot(
        template_name="tpl-A",
        csv_content="1,2\nA,C\nA,C\n",
        numbering_row=["1", "2"],
        template_row=["A", "C"],
        sequence_row=["A", "C"],
        raw_meta={
            "annotation_meta": {"trnascan_hit": True, "boundary_resolved": False},
            "agent_cmp": {"template_name": "tpl-A", "regions": deepcopy(regions)},
            "agent_ann_meta": {"trnascan_hit": True, "boundary_resolved": False},
            "agent_blocks": deepcopy(blocks),
            "agent_runtime_overrides": {},
        },
        template_candidates=[
            {"seqname": "tpl-A", "family_match_rank": 1, "family_match_label": "isotype_only"},
            {"seqname": "tpl-B", "family_match_rank": 2, "family_match_label": "anticodon_only"},
        ],
        template_score=10.0,
        template_margin=0.5,
    )


def _work_state_from_snapshot(
    snapshot: RootSnapshot,
    *,
    input_sequence: str = "AC",
    valid: bool = True,
    reasons: list[str] | None = None,
    score: float = 12.5,
) -> AgentWorkState:
    diagnostics = DiagnosticsReport(
        template_score=float(snapshot.template_score or 0.0),
        template_margin=float(snapshot.template_margin or 0.0),
    )
    difficulty = DifficultyReport(difficulty_score=0.1, tier="easy", hard_case=False)
    route = RouteReport(route="canonical")
    return AgentWorkState.from_root(
        input_sequence=input_sequence,
        request_flags={"use_llm": False},
        snapshot=snapshot,
        diagnostics=diagnostics,
        difficulty=difficulty,
        route=route,
        valid=valid,
        reasons=list(reasons or []),
        score=float(score),
    )


def test_agent_work_state_promotes_explicit_snapshot_state():
    snapshot = _snapshot_with_state()
    state = _work_state_from_snapshot(snapshot)

    assert state.rows["numbering"] == ["1", "2"]
    assert state.selected_template["template_name"] == "tpl-A"
    assert len(state.template_candidates) == 2
    assert len(state.region_blocks) == len(STANDARD_ALIGNMENT_REGIONS)
    assert len(state.seams) == len(STANDARD_ALIGNMENT_REGIONS) - 1
    assert state.compare_state["template_name"] == "tpl-A"
    assert state.annotation_state["trnascan_hit"] is True
    assert state.runtime_state == {}
    assert state.validation == {"valid": True, "reasons": []}
    assert state.ranking["score"] == 12.5


def test_template_switch_stays_local_and_never_rebuilds_root(monkeypatch):
    root_builder = RootSnapshotBuilder()
    executor = BranchExecutor(root_builder=root_builder)
    parent_snapshot = _snapshot_with_state()
    request = AlignmentRequest(target_seq="AC", use_llm=True)
    action = CandidateAction(
        action_type="switch_template_topk",
        action_id="a1",
        params={"template_name": "tpl-B"},
        reason="switch to better family template",
        allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
        success_condition="switch_template_locally_and_recompute_only_affected_regions",
        rollback_condition="rollback_if_template_switch_breaks_scope_or_validation",
    )

    parent_cmp = deepcopy(parent_snapshot.raw_meta["agent_cmp"])
    candidate_cmp = deepcopy(parent_cmp)
    target_region = STANDARD_ALIGNMENT_REGIONS[1]
    candidate_cmp["template_name"] = "tpl-B"
    candidate_cmp["regions"][target_region]["template_seq"] = "CHANGED"
    candidate_cmp["template_candidates"] = [
        {"seqname": "tpl-B", "family_match_rank": 2, "family_match_label": "anticodon_only"},
        {"seqname": "tpl-A", "family_match_rank": 1, "family_match_label": "isotype_only"},
    ]

    monkeypatch.setattr(root_builder, "compare", lambda request, template_name=None: deepcopy(candidate_cmp))

    def _fail_build_from_compare(*args, **kwargs):
        raise AssertionError("template switch must not call build_from_compare during search")

    monkeypatch.setattr(root_builder, "build_from_compare", _fail_build_from_compare)

    def _fail_hidden_postcheck(**kwargs):
        raise AssertionError("template switching must not trigger regional LLM postcheck")

    monkeypatch.setattr(
        "backend.agent.execution.branch_executor.maybe_run_region_postcheck",
        _fail_hidden_postcheck,
    )
    monkeypatch.setattr(
        root_builder,
        "prepare_compare_state",
        lambda **kwargs: (
            deepcopy(candidate_cmp),
            {"trnascan_hit": True, "boundary_resolved": False},
            [],
            {},
            {},
        ),
    )

    captured = {}

    def _fake_recompute_regions(**kwargs):
        captured["regions"] = list(kwargs["regions"])
        return deepcopy(parent_snapshot.raw_meta["agent_blocks"]), {}

    monkeypatch.setattr(executor, "_recompute_regions", _fake_recompute_regions)

    def _fake_snapshot_from_components(**kwargs):
        blocks = deepcopy(kwargs["blocks"])
        return RootSnapshot(
            template_name="tpl-B",
            csv_content="1,2\nA,C\nA,C\n",
            numbering_row=["1", "2"],
            template_row=["A", "C"],
            sequence_row=["A", "C"],
            raw_meta={
                "annotation_meta": {"trnascan_hit": True, "boundary_resolved": False},
                "agent_cmp": deepcopy(candidate_cmp),
                "agent_ann_meta": {"trnascan_hit": True, "boundary_resolved": False},
                "agent_blocks": blocks,
                "agent_runtime_overrides": {},
            },
            template_candidates=deepcopy(candidate_cmp["template_candidates"]),
            template_score=9.5,
            template_margin=0.4,
        )

    monkeypatch.setattr(root_builder, "snapshot_from_components", _fake_snapshot_from_components)

    work_state = _work_state_from_snapshot(parent_snapshot)

    snapshot = executor.execute(
        request=request,
        parent_snapshot=parent_snapshot,
        action=action,
        branch_id="step-0001",
        work_state=work_state,
    )

    assert captured["regions"] == [target_region]
    assert snapshot.template_name == "tpl-B"
    assert snapshot.source_action == "switch_template_topk"
    assert snapshot.raw_meta["agent_branch"]["action_type"] == "switch_template_topk"


def test_region_postcheck_is_an_explicit_bounded_candidate_action(monkeypatch):
    root_builder = RootSnapshotBuilder()
    executor = BranchExecutor(root_builder=root_builder)
    parent_snapshot = _snapshot_with_state()
    work_state = _work_state_from_snapshot(parent_snapshot)
    request = AlignmentRequest(target_seq="AC", use_llm=True)
    focus_region = "D loop + D stem"
    action = CandidateAction(
        action_type="repair_region_with_structure_first_policy",
        action_id="explicit-postcheck",
        target_region=focus_region,
        params={"focus_regions": [focus_region]},
        reason="explicit regional candidate proposal",
        allowed_regions=[focus_region],
        success_condition="propose_only_inside_declared_region",
        rollback_condition="rollback_if_shared_harness_rejects_candidate",
    )

    def _fake_postcheck(**kwargs):
        blocks = deepcopy(kwargs["blocks"])
        for block in blocks:
            if block["region"] != focus_region:
                continue
            block["meta"]["llm_runtime_trace"] = {
                "final_status": "pass_through_no_improvement",
                "llm_attempted": True,
                "output_changed": False,
            }
        return blocks

    monkeypatch.setattr(
        "backend.agent.execution.branch_executor.maybe_run_region_postcheck",
        _fake_postcheck,
    )
    monkeypatch.setattr(executor, "_global_struct_summary", lambda **kwargs: {})
    monkeypatch.setattr(
        root_builder,
        "snapshot_from_components",
        lambda **kwargs: RootSnapshot(
            template_name=parent_snapshot.template_name,
            csv_content=parent_snapshot.csv_content,
            numbering_row=list(parent_snapshot.numbering_row),
            template_row=list(parent_snapshot.template_row),
            sequence_row=list(parent_snapshot.sequence_row),
            raw_meta={"agent_blocks": deepcopy(kwargs["blocks"])},
        ),
    )

    snapshot = executor.execute(
        request=request,
        parent_snapshot=parent_snapshot,
        action=action,
        branch_id="step-explicit-postcheck",
        work_state=work_state,
    )

    contract = snapshot.raw_meta["regional_candidate_contract"]
    assert contract["invocation"] == "explicit_agent_action"
    assert contract["role"] == "bounded_regional_candidate_generator"
    assert contract["candidate_status"] == "pending_shared_harness_evaluation"
    assert contract["llm_output_directly_authoritative"] is False
    assert contract["llm_attempted_regions"] == [focus_region]
    assert contract["parent_snapshot_id"] == parent_snapshot.snapshot_id
    assert len(contract["parent_fingerprint"]) == 64
    assert len(contract["candidate_fingerprint"]) == 64


def test_variable_topology_local_repair_can_rescue_seq16_without_root_rebuild():
    seq16 = "GUAGUCGUGGCCGAGUGGUUAAGGCGAUGGUCUUCAAAGCUAUAGGGGCUCCGCCUCACGGGUUCGAAUCCCGUCGACGACG"
    root_builder = RootSnapshotBuilder()
    executor = BranchExecutor(root_builder=root_builder)
    request = AlignmentRequest(
        target_seq=seq16,
        output_csv_path="/dev/null",
        use_llm=False,
        llm_reasoning=False,
        domain_hint="cytosolic_eukaryote",
    )
    parent_snapshot = root_builder.build(request)

    parent_guard = HardGuards().validate(request, parent_snapshot)
    assert parent_guard.valid is False
    work_state = _work_state_from_snapshot(
        parent_snapshot,
        input_sequence=seq16,
        valid=parent_guard.valid,
        reasons=list(parent_guard.reasons),
        score=0.0,
    )

    action = CandidateAction(
        action_type="repair_variable_topology_locally",
        action_id="seq16-topology",
        target_region="Variable loop",
        params={"focus_regions": ["Variable loop", "T loop + T stem"]},
        reason="local topology repair",
        allowed_regions=["Variable loop", "T loop + T stem"],
        success_condition="repair_variable_topology_without_cross_region_mutation",
        rollback_condition="rollback_if_variable_topology_repair_breaks_scope_or_validation",
    )

    snapshot = executor.execute(
        request=request,
        parent_snapshot=parent_snapshot,
        action=action,
        branch_id="step-0001",
        work_state=work_state,
    )

    result = HardGuards().validate(request, snapshot)

    assert result.valid is True
    assert snapshot.source_action == "repair_variable_topology_locally"


def test_local_template_switch_still_executes_after_parent_raw_meta_is_cleared(monkeypatch):
    root_builder = RootSnapshotBuilder()
    executor = BranchExecutor(root_builder=root_builder)
    parent_snapshot = _snapshot_with_state()
    work_state = _work_state_from_snapshot(parent_snapshot)
    parent_snapshot.raw_meta = {}
    request = AlignmentRequest(target_seq="AC", use_llm=False)
    action = CandidateAction(
        action_type="switch_template_topk",
        action_id="a2",
        params={"template_name": "tpl-B"},
        reason="switch from explicit work state only",
        allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
        success_condition="switch_template_locally_and_recompute_only_affected_regions",
        rollback_condition="rollback_if_template_switch_breaks_scope_or_validation",
    )

    parent_cmp = deepcopy(work_state.compare_state)
    candidate_cmp = deepcopy(parent_cmp)
    candidate_cmp["template_name"] = "tpl-B"
    candidate_cmp["regions"][STANDARD_ALIGNMENT_REGIONS[0]]["template_seq"] = "CHANGED"
    candidate_cmp["template_candidates"] = [
        {"seqname": "tpl-B", "family_match_rank": 2, "family_match_label": "anticodon_only"},
        {"seqname": "tpl-A", "family_match_rank": 1, "family_match_label": "isotype_only"},
    ]

    monkeypatch.setattr(root_builder, "compare", lambda request, template_name=None: deepcopy(candidate_cmp))
    monkeypatch.setattr(
        root_builder,
        "prepare_compare_state",
        lambda **kwargs: (
            deepcopy(candidate_cmp),
            {"trnascan_hit": True, "boundary_resolved": False},
            [],
            {},
            {},
        ),
    )
    monkeypatch.setattr(
        executor,
        "_recompute_regions",
        lambda **kwargs: (deepcopy(work_state.region_blocks), {}),
    )
    monkeypatch.setattr(
        root_builder,
        "snapshot_from_components",
        lambda **kwargs: RootSnapshot(
            template_name="tpl-B",
            csv_content="1,2\nA,C\nA,C\n",
            numbering_row=["1", "2"],
            template_row=["A", "C"],
            sequence_row=["A", "C"],
            raw_meta={},
            template_candidates=deepcopy(candidate_cmp["template_candidates"]),
            template_score=9.5,
            template_margin=0.4,
        ),
    )

    snapshot = executor.execute(
        request=request,
        parent_snapshot=parent_snapshot,
        action=action,
        branch_id="step-0002",
        work_state=work_state,
    )

    assert snapshot.template_name == "tpl-B"
    assert snapshot.source_action == "switch_template_topk"


def test_region_scoped_action_rolls_back_when_it_modifies_outside_allowed_regions(monkeypatch):
    root_builder = RootSnapshotBuilder()
    executor = BranchExecutor(root_builder=root_builder)
    parent_snapshot = _snapshot_with_state()
    work_state = _work_state_from_snapshot(parent_snapshot)
    request = AlignmentRequest(target_seq="AC", use_llm=False)
    action = CandidateAction(
        action_type="repair_region_with_dp_only",
        action_id="scope-rollback",
        target_region="Variable loop",
        params={"focus_regions": ["Variable loop"]},
        reason="should stay inside variable loop",
        allowed_regions=["Variable loop"],
        success_condition="repair_only_variable_loop",
        rollback_condition="rollback_if_other_regions_change",
    )

    def _bad_recompute_regions(**kwargs):
        blocks = deepcopy(work_state.region_blocks)
        for block in blocks:
            if block["region"] == "D loop + D stem":
                block["lines"][2] = ["G", "G"]
        return blocks, {}

    monkeypatch.setattr(executor, "_recompute_regions", _bad_recompute_regions)
    monkeypatch.setattr(
        root_builder,
        "snapshot_from_components",
        lambda **kwargs: RootSnapshot(
            template_name=parent_snapshot.template_name,
            csv_content=parent_snapshot.csv_content,
            numbering_row=list(parent_snapshot.numbering_row),
            template_row=list(parent_snapshot.template_row),
            sequence_row=list(parent_snapshot.sequence_row),
            raw_meta={},
            warnings=list(parent_snapshot.warnings),
        ),
    )

    snapshot = executor.execute(
        request=request,
        parent_snapshot=parent_snapshot,
        action=action,
        branch_id="step-0003",
        work_state=work_state,
    )

    assert snapshot.template_name == parent_snapshot.template_name
    assert snapshot.numbering_row == parent_snapshot.numbering_row
    assert snapshot.sequence_row == parent_snapshot.sequence_row
    assert "action_scope_rollback:" in "|".join(snapshot.raw_meta["agent_branch"]["notes"])


def test_root_snapshot_builder_retries_finalization_with_pre_postcheck_blocks_when_unpaired_varloop_breaks_compaction(monkeypatch):
    root_builder = RootSnapshotBuilder()
    request = AlignmentRequest(target_seq="AC", output_csv_path="/dev/null", use_llm=True, llm_reasoning=False)
    cmp = {"template_name": "tpl-A", "template_candidates": []}
    ann_meta = {"trnascan_hit": True, "boundary_resolved": False}

    current_blocks = [
        {
            "region": "Variable loop",
            "lines": [["44", "45"], ["A", "C"], ["A", "C"]],
            "meta": {"llm_runtime_trace": {"pair_hint": 0}},
        }
    ]
    fallback_blocks = [
        {
            "region": "Variable loop",
            "lines": [["44", "45"], ["A", "C"], ["A", "C"]],
            "meta": {},
        }
    ]

    calls = []

    def _fake_finalize(**kwargs):
        calls.append((bool(kwargs["use_llm"]), deepcopy(kwargs["blocks"]), list(kwargs["warnings"])))
        if bool(kwargs["use_llm"]):
            raise ValueError("Insertion-column canonicalization broke base-preservation check")
        return (
            "44,45\nA,C\nA,C\n",
            list(kwargs["warnings"]),
            {"ok": True},
            {"run_llm": False},
        )

    monkeypatch.setattr(
        "backend.agent.execution.root_snapshot_builder.finalize_alignment_rows",
        _fake_finalize,
    )

    snapshot = root_builder.snapshot_from_components(
        request=request,
        cmp=cmp,
        ann_meta=ann_meta,
        blocks=current_blocks,
        fallback_blocks=fallback_blocks,
        warnings=["w0"],
        runtime_overrides={},
        global_struct_summary={},
        use_llm=True,
        llm_reasoning=False,
        snapshot_id="root",
        parent_snapshot_id=None,
        source_action="root_snapshot",
        root_source="standard_pipeline",
    )

    assert [flag for flag, _, _ in calls] == [True, False]
    assert calls[1][1] == fallback_blocks
    assert "LLM final-row rollback applied after insertion-column canonicalization broke base preservation." in snapshot.warnings
    assert snapshot.raw_meta["agent_finalization_fallback"] is True
    assert snapshot.raw_meta["agent_effective_use_llm"] is False


def test_local_actions_pass_parent_blocks_as_finalization_fallback(monkeypatch):
    root_builder = RootSnapshotBuilder()
    executor = BranchExecutor(root_builder=root_builder)
    parent_snapshot = _snapshot_with_state()
    work_state = _work_state_from_snapshot(parent_snapshot)
    request = AlignmentRequest(target_seq="AC", use_llm=True)
    action = CandidateAction(
        action_type="refine_boundary",
        action_id="boundary-fallback",
        params={},
        reason="boundary refinement should preserve parent-state fallback",
        allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
        success_condition="apply_boundary_refinement_locally",
        rollback_condition="rollback_to_parent_blocks_if_finalization_breaks_preservation",
    )

    monkeypatch.setattr(
        "backend.agent.execution.branch_executor.maybe_refine_anticodon_boundaries",
        lambda cmp, regions, runtime_values_override=None: None,
    )
    monkeypatch.setattr(
        executor,
        "_recompute_regions",
        lambda **kwargs: (deepcopy(work_state.region_blocks), {}),
    )

    captured = {}

    def _fake_snapshot_from_components(**kwargs):
        captured["fallback_blocks"] = deepcopy(kwargs.get("fallback_blocks"))
        return RootSnapshot(
            template_name=parent_snapshot.template_name,
            csv_content=parent_snapshot.csv_content,
            numbering_row=list(parent_snapshot.numbering_row),
            template_row=list(parent_snapshot.template_row),
            sequence_row=list(parent_snapshot.sequence_row),
            raw_meta={},
            warnings=list(parent_snapshot.warnings),
        )

    monkeypatch.setattr(root_builder, "snapshot_from_components", _fake_snapshot_from_components)

    executor.execute(
        request=request,
        parent_snapshot=parent_snapshot,
        action=action,
        branch_id="step-boundary-fallback",
        work_state=work_state,
    )

    assert captured["fallback_blocks"] == work_state.region_blocks


def test_planner_can_plan_directly_from_work_state(monkeypatch):
    planner = AgentPlannerV2()
    snapshot = _snapshot_with_state()
    work_state = _work_state_from_snapshot(snapshot)
    request = AlignmentRequest(target_seq="AC", use_llm=False)
    captured = {}

    def _fake_plan(request, root_snapshot, diagnostics, difficulty, route, guard_reasons=None):
        captured["request"] = request
        captured["root_snapshot"] = root_snapshot
        captured["diagnostics"] = diagnostics
        captured["difficulty"] = difficulty
        captured["route"] = route
        captured["guard_reasons"] = list(guard_reasons or [])
        return "context", [], []

    monkeypatch.setattr(planner, "plan", _fake_plan)

    context, memory_hits, actions = planner.plan_from_work_state(request=request, work_state=work_state)

    assert context == "context"
    assert memory_hits == []
    assert actions == []
    assert captured["request"] is request
    assert captured["root_snapshot"] is work_state.current_snapshot
    assert captured["diagnostics"] is work_state.current_diagnostics
    assert captured["difficulty"] is work_state.current_difficulty
    assert captured["route"] is work_state.current_route
    assert captured["guard_reasons"] == work_state.current_reasons
