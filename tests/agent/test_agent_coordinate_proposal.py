from __future__ import annotations

import json

from backend.agent.contracts import AlignmentRequest, CandidateAction, RootSnapshot
from backend.agent.execution.branch_executor import BranchExecutor
from backend.agent.orchestrator.context_engineering import SpecialistContext
from backend.agent.orchestrator.planner import AgentPlannerV2
from backend.agent.orchestrator.specialists import (
    CoordinateProposalSpecialist,
    ProposedAction,
)
from backend.domain.alignment import coordinate_proposal
from backend.infra.llm.json_client import JsonCompletion


class _Client:
    """Answers with one payload per call and keeps the prompts it saw."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        if isinstance(payload, Exception):
            raise payload
        return JsonCompletion(
            payload=dict(payload),
            provider="test",
            model="test-model",
            status_code=200,
            elapsed_sec=0.01,
            usage={"total_tokens": 40},
        )


_SEQUENCE = ["G", "C", "A", "U", "G", "G", "C", "A"]
_PARENT_ROW = ["1", "2", "3", "4", "5", "-", "7", "8"]
_REGION = "Aminoacyl arm 5' end"


def _propose(payloads, *, numbering_row=None, allowed_regions=None, **overrides):
    client = _Client(payloads)
    result = coordinate_proposal.propose_row(
        client=client,
        sequence_row=_SEQUENCE,
        numbering_row=list(numbering_row or _PARENT_ROW),
        allowed_regions=list(allowed_regions or [_REGION]),
        structure_pairs=[
            {"left_index": 1, "right_index": 8, "left_id": "1", "right_id": "8"}
        ],
        **overrides,
    )
    return client, result


def test_prompt_shows_the_rules_and_the_row_side_by_side():
    labels = [*_PARENT_ROW[:3], "4i1", *_PARENT_ROW[4:]]
    client, result = _propose([{"labels": labels}])
    assert result.status == "proposed"
    body = json.loads(client.calls[0]["messages"][-1]["content"])
    assert body["residues"]["bases"] == "G C A U G G C A"
    assert body["residues"]["current_labels"] == "<EDIT> <EDIT> <EDIT> <EDIT> <EDIT> <EDIT> <EDIT> <EDIT>"
    assert body["residues"]["hidden_label_positions"] == "1 2 3 4 5 6 7 8"
    assert body["structure_pairs"] == [[1, 8]]
    assert body["allowed_regions"] == [_REGION]
    assert "REFERENCE - SPRINZL COORDINATE SYSTEM" in client.calls[0]["messages"][0]["content"]


def test_a_repaired_row_is_returned_with_the_positions_it_moved():
    labels = ["1", "2", "3", "4", "5", "6", "7", "8"]
    client, result = _propose([{"labels": labels}])
    assert client.calls and result.status == "proposed"
    assert result.labels == labels
    assert result.changed == [{"position": "6", "from": "-", "to": "6"}]


def test_a_short_row_is_answered_once_more_before_it_is_given_up_on():
    client, result = _propose(
        [{"labels": ["1", "2"]}, {"labels": ["1", "2", "3", "4", "5", "6", "-", "8"]}]
    )
    assert len(client.calls) == 2
    assert result.status == "proposed"
    assert "expected 8" in client.calls[1]["messages"][-1]["content"]


def test_an_illegal_label_never_becomes_a_candidate():
    client, result = _propose(
        [{"labels": ["1", "2", "3", "4", "5", "99", "7", "8"]}], attempts=1
    )
    assert client.calls == client.calls[:1]
    assert result.status == "rejected"
    assert result.labels == []
    assert "illegal Sprinzl labels: 99" in result.error


def test_editing_outside_the_flagged_region_is_refused():
    # Position 4 sits in the D arm, which this action was not allowed to touch.
    _, result = _propose(
        [{"labels": ["1", "2", "3", "10", "5", "6", "7", "8"]}], attempts=1
    )
    assert result.status == "rejected"
    assert "outside the allowed set" in result.error


def test_a_row_identical_to_the_parent_costs_no_candidate():
    _, result = _propose([{"labels": list(_PARENT_ROW)}])
    assert result.status == "no_change"
    assert result.labels == []


def test_transport_failure_is_reported_instead_of_retried():
    from backend.infra.llm.json_client import JsonCompletionError

    client, result = _propose([JsonCompletionError("provider down")])
    assert len(client.calls) == 1
    assert result.status == "llm_error"
    assert "provider down" in result.error


# --- how the action reaches the planner -----------------------------------


def _context(**values) -> SpecialistContext:
    fields = {
        "target_length": len(_SEQUENCE),
        "anticodon_hint": "",
        "use_llm": True,
        "llm_reasoning": False,
        "selected_template": "bact-num",
        "failure_regions": [_REGION],
        "coordinate_proposal_enabled": True,
    }
    fields.update(values)
    return SpecialistContext(**fields)


def test_the_proposal_exists_only_for_an_enabled_llm_agent():
    specialist = CoordinateProposalSpecialist()
    assert [a.action_type for a in specialist.propose(_context())] == [
        "propose_coordinate_revision"
    ]
    assert specialist.propose(_context(coordinate_proposal_enabled=False)) == []
    assert specialist.propose(_context(use_llm=False)) == []
    assert specialist.propose(_context(failure_regions=[], validation_reasons=[])) == []


def test_a_legal_but_disputed_row_still_offers_the_proposal():
    # The guards passed, so failure_regions is empty; the acceptor end is still
    # ambiguous, and that is the case a coordinate edit exists for.
    specialist = CoordinateProposalSpecialist()
    action = specialist.propose(
        _context(
            failure_regions=[],
            disputed_regions=["Aminoacyl arm 3' end", "Variable loop"],
        )
    )[0]
    assert action.action_type == "propose_coordinate_revision"
    assert action.params["focus_regions"] == ["Aminoacyl arm 3' end", "Variable loop"]
    assert action.allowed_regions == action.params["focus_regions"]


def test_the_fixed_policy_never_picks_the_model_first():
    planner = AgentPlannerV2()
    context = _context(difficulty_tier="hard")
    actions = [
        CandidateAction(
            action_id="a1",
            action_type="align_legacy_reference",
            allowed_regions=[_REGION],
            success_condition="x",
            rollback_condition="y",
        ),
        CandidateAction(
            action_id="a2",
            action_type="propose_coordinate_revision",
            allowed_regions=[_REGION],
            success_condition="x",
            rollback_condition="y",
        ),
    ]
    ranked = sorted(actions, key=planner._priority)
    assert [action.action_type for action in ranked] == [
        "align_legacy_reference",
        "propose_coordinate_revision",
    ]


def test_a_bounded_action_list_keeps_the_proposal_visible():
    # A canonical CM-rooted round shows four actions.  Sorting the proposal last
    # would otherwise truncate it before the Agent ever saw the option.
    planner = AgentPlannerV2()
    ranked = [
        ProposedAction("s", CandidateAction(action_type=kind, allowed_regions=[_REGION],
                                            success_condition="x", rollback_condition="y"))
        for kind in (
            "align_legacy_reference",
            "reconcile_variable_boundary_with_structure",
            "repair_acceptor_tail_with_structure",
            "switch_template_topk",
            "search_literature",
            "propose_coordinate_revision",
        )
    ]
    kept = planner._reserve_tail_actions(ranked, 4)
    assert [item.action.action_type for item in kept] == [
        "align_legacy_reference",
        "reconcile_variable_boundary_with_structure",
        "search_literature",
        "propose_coordinate_revision",
    ]


# --- what the Harness does with the answer --------------------------------


def _snapshot() -> RootSnapshot:
    return RootSnapshot(
        template_name="bact-num",
        csv_content="",
        numbering_row=list(_PARENT_ROW),
        template_row=list(_SEQUENCE),
        sequence_row=list(_SEQUENCE),
        structure_view={"id_pairs": [{"left_index": 1, "right_index": 8}]},
        raw_meta={},
    )


def _action() -> CandidateAction:
    return CandidateAction(
        action_id="propose:1",
        action_type="propose_coordinate_revision",
        specialist=CoordinateProposalSpecialist.name,
        allowed_regions=[_REGION],
        success_condition="row_keeps_bases_and_scores_higher",
        rollback_condition="retain_parent",
    )


def _executor(client) -> BranchExecutor:
    class _Builder:
        @staticmethod
        def snapshot_from_components(**kwargs):  # pragma: no cover - unused
            raise AssertionError("coordinate proposals do not rebuild from blocks")

    return BranchExecutor(root_builder=_Builder())


def test_branch_executor_hands_the_proposed_row_to_the_normal_snapshot_path():
    client = _Client([{"labels": ["1", "2", "3", "4", "5", "6", "7", "8"]}])
    import backend.domain.alignment.coordinate_proposal as cp

    original = cp.propose_row

    def patched(**kwargs):
        kwargs["client"] = client
        return original(**kwargs)

    cp.propose_row = patched
    try:
        snapshot = _executor(client).execute(
            AlignmentRequest(target_seq="GCAUGGCA", use_llm=True),
            _snapshot(),
            _action(),
            "step-01",
        )
    finally:
        cp.propose_row = original

    assert snapshot.numbering_row == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert snapshot.source_action == "propose_coordinate_revision"
    assert snapshot.raw_meta["coordinate_proposal"]["changed_count"] == 1
    rows = [line.split(",") for line in snapshot.csv_content.strip().splitlines()]
    assert rows[0] == snapshot.numbering_row


def test_a_rejected_proposal_leaves_the_parent_row_alone():
    client = _Client([{"labels": ["1", "2", "3", "10", "5", "6", "7", "8"]}])
    import backend.domain.alignment.coordinate_proposal as cp

    original = cp.propose_row

    def patched(**kwargs):
        kwargs["client"] = client
        return original(**kwargs)

    cp.propose_row = patched
    try:
        snapshot = _executor(client).execute(
            AlignmentRequest(target_seq="GCAUGGCA", use_llm=True),
            _snapshot(),
            _action(),
            "step-02",
        )
    finally:
        cp.propose_row = original

    assert snapshot.numbering_row == _PARENT_ROW
    assert snapshot.raw_meta["coordinate_proposal"]["status"] == "rejected"


def test_the_checks_a_row_failed_are_put_back_in_front_of_the_model():
    client, result = _propose(
        [{"labels": ["1", "2", "3", "4", "5", "6", "7", "8"]}],
        prior_violations=["variable_singleton_occupied:V11"],
    )
    body = json.loads(client.calls[0]["messages"][-1]["content"])
    assert body["checks_your_last_row_failed"] == ["variable_singleton_occupied:V11"]
    assert "ACCEPTANCE paragraph" in body["instruction"]
    assert result.status == "proposed"


def test_the_executor_carries_earlier_rejections_into_the_next_request():
    from types import SimpleNamespace

    captured = {}
    import backend.domain.alignment.coordinate_proposal as cp

    original = cp.propose_row

    def spy(**kwargs):
        captured.update(kwargs)
        kwargs["client"] = _Client([{"labels": list(_PARENT_ROW)}])
        return original(**kwargs)

    cp.propose_row = spy
    try:
        state = SimpleNamespace(
            coordinate_proposal_attempts=[
                {
                    "round": 1,
                    "accepted": False,
                    "violations": ["insertion_without_parent:13i1"],
                }
            ],
            literature_findings=[],
            current_valid=True,
            current_reasons=[],
            issues=[],
        )
        _executor(None).execute(
            AlignmentRequest(target_seq="GCAUGGCA", use_llm=True),
            _snapshot(),
            _action(),
            "step-03",
            work_state=state,
        )
    finally:
        cp.propose_row = original

    assert captured["prior_violations"] == ["insertion_without_parent:13i1"]


def test_the_prompt_asks_for_window_rewrites_not_a_whole_row():
    client, result = _propose(
        [{"edits": [{"from": 1, "to": 8, "labels": ["1", "2", "3", "4", "5", "6", "7", "8"]}]}]
    )
    body = json.loads(client.calls[0]["messages"][-1]["content"])
    assert body["edit_windows"] == [{"from": 1, "to": 8}]
    assert "edits" in body["response_schema"]
    assert result.status == "proposed"
    assert result.labels == ["1", "2", "3", "4", "5", "6", "7", "8"]


def test_windows_pad_by_one_residue_so_a_boundary_can_move():
    # Only residue 4 is in the disputed region, yet the window reaches 3 and 5:
    # a boundary fix usually means moving the neighbour, not the flagged base.
    row = ["1", "2", "3", "30", "5", "-", "7", "8"]
    assert coordinate_proposal.edit_windows(
        row, ["Anticodon loop + Anticodon stem"]
    ) == [{"from": 3, "to": 5}]
    # The acceptor labels either side of residue 4 are close enough that padding
    # merges them into one window, so the model is not asked twice for one run.
    assert coordinate_proposal.edit_windows(row, [_REGION]) == [{"from": 1, "to": 8}]
    far = ["1", "2", "-", "-", "-", "-", "50", "8"]
    assert coordinate_proposal.edit_windows(
        far, [_REGION, "T loop + T stem"]
    ) == [{"from": 1, "to": 3}, {"from": 6, "to": 8}]


def test_an_edit_for_a_window_that_was_not_offered_is_refused():
    _, result = _propose(
        [{"edits": [{"from": 1, "to": 8, "labels": ["1"] * 8},
                    {"from": 9, "to": 10, "labels": ["-", "-"]}]}],
        attempts=1,
    )
    assert result.status == "rejected"
    assert "not one of the windows" in result.error


def test_a_window_left_unanswered_is_refused():
    # Two separate regions are disputed, so both windows must come back.
    _, result = _propose(
        [{"edits": [{"from": 1, "to": 3, "labels": ["1", "2", "-"]}]}],
        numbering_row=["1", "2", "-", "-", "-", "-", "50", "8"],
        allowed_regions=[_REGION, "T loop + T stem"],
        attempts=1,
    )
    assert result.status == "rejected"
    assert "no labels returned for window" in result.error


def test_a_truncated_answer_retries_with_more_room_and_does_not_crash():
    """A budget exhaustion must not surface as an action failure.

    The retry path used to fall through into the block that reads the answer,
    so the first truncation raised UnboundLocalError and the Agent recorded
    ``action_failed`` instead of a second attempt.
    """

    from backend.infra.llm.json_client import JsonTruncatedError

    labels = ["1", "2", "3", "4", "5", "6", "7", "8"]
    client, result = _propose(
        [JsonTruncatedError("completion budget reached"), {"labels": labels}]
    )
    assert result.status == "proposed"
    assert result.attempts == 2
    assert len(client.calls) == 2
    assert client.calls[1]["max_tokens"] > client.calls[0]["max_tokens"]


def test_two_truncated_answers_report_the_budget_rather_than_a_crash():
    from backend.infra.llm.json_client import JsonTruncatedError

    _client, result = _propose([JsonTruncatedError("completion budget reached")])
    assert result.status == "llm_truncated"
    assert "JsonTruncatedError" in result.error
