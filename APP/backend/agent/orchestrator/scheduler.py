from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from ..contracts import AgentWorkState, AlignmentRequest, CandidateAction, RootSnapshot
from ..evaluation import HardGuards, OnlineRanker
from ..execution import BranchExecutor
from .difficulty import DifficultyAssessor
from .specialized import SpecializedRouteAssessor


class ParallelBranchScheduler:
    """
    Execute bounded branches in parallel during the migration phase.
    """

    def __init__(
        self,
        branch_executor: BranchExecutor,
        difficulty_assessor: DifficultyAssessor,
        route_assessor: SpecializedRouteAssessor,
        hard_guards: HardGuards,
        online_ranker: OnlineRanker,
    ) -> None:
        self.branch_executor = branch_executor
        self.difficulty_assessor = difficulty_assessor
        self.route_assessor = route_assessor
        self.hard_guards = hard_guards
        self.online_ranker = online_ranker

    def run(
        self,
        request: AlignmentRequest,
        root_snapshot: RootSnapshot,
        actions: List[CandidateAction],
        max_workers: int = 4,
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], Dict[str, object] | None]:
        if not actions:
            return [], [], None

        workers = max(1, min(max_workers, len(actions)))
        summaries: List[Dict[str, object]] = []
        trace_events: List[Dict[str, object]] = []
        best_item: Dict[str, object] | None = None
        best_score = float("-inf")

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent-branch") as pool:
            future_map = {
                pool.submit(self._run_one, request, root_snapshot, action, f"branch-{idx:04d}"): (idx, action)
                for idx, action in enumerate(actions, start=1)
            }
            for fut in as_completed(future_map):
                idx, action = future_map[fut]
                try:
                    item = fut.result()
                except Exception as exc:
                    summaries.append(
                        {
                            "snapshot_id": f"branch-{idx:04d}",
                            "source_action": action.action_type,
                            "template_name": root_snapshot.template_name,
                            "route": "execution_error",
                            "hard_valid": False,
                            "score": float("-inf"),
                            "reasons": [f"branch_execution_error:{exc}"],
                        }
                    )
                    trace_events.append(
                        {
                            "stage": "branch_failed",
                            "snapshot_id": f"branch-{idx:04d}",
                            "action_type": action.action_type,
                            "reason": f"{exc}",
                        }
                    )
                    continue
                summaries.append(item["summary"])
                trace_events.append(item["trace"])
                score = float(item["summary"].get("score", float("-inf")))
                if bool(item["summary"].get("hard_valid")) and score > best_score:
                    best_score = score
                    best_item = item

        summaries.sort(key=lambda row: str(row.get("snapshot_id")))
        trace_events.sort(key=lambda row: str(row.get("snapshot_id")))
        return summaries, trace_events, best_item

    def _run_one(
        self,
        request: AlignmentRequest,
        root_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
    ) -> Dict[str, object]:
        root_diagnostics = self.difficulty_assessor.build_diagnostics(root_snapshot)
        root_difficulty = self.difficulty_assessor.assess(root_diagnostics, root_snapshot)
        root_route = self.route_assessor.assess(root_snapshot, root_diagnostics)
        root_guard = self.hard_guards.validate(request, root_snapshot)
        root_score = self.online_ranker.score(root_snapshot, root_diagnostics, root_route, root_guard)
        work_state = AgentWorkState.from_root(
            input_sequence=request.target_seq,
            request_flags={
                "use_llm": bool(request.use_llm),
                "llm_reasoning": bool(request.llm_reasoning),
                "template_name": str(request.template_name or ""),
                "runtime_overrides": dict(request.runtime_values_override or {}),
            },
            snapshot=root_snapshot,
            diagnostics=root_diagnostics,
            difficulty=root_difficulty,
            route=root_route,
            valid=root_guard.valid,
            reasons=list(root_guard.reasons),
            score=root_score,
        )
        snapshot = self.branch_executor.execute(
            request,
            root_snapshot,
            action,
            branch_id,
            work_state=work_state,
        )
        diagnostics = self.difficulty_assessor.build_diagnostics(snapshot)
        difficulty = self.difficulty_assessor.assess(diagnostics, snapshot)
        route = self.route_assessor.assess(snapshot, diagnostics)
        guard = self.hard_guards.validate(request, snapshot)
        score = self.online_ranker.score(snapshot, diagnostics, route, guard)
        summary = {
            "snapshot_id": snapshot.snapshot_id,
            "action_id": action.action_id,
            "source_action": snapshot.source_action,
            "specialist": action.specialist,
            "template_name": snapshot.template_name,
            "route": route.route,
            "route_confidence": route.confidence,
            "in_canonical_benchmark": route.in_canonical_benchmark,
            "out_of_scope": route.out_of_scope,
            "difficulty_tier": difficulty.tier,
            "difficulty_score": difficulty.difficulty_score,
            "hard_valid": guard.valid,
            "score": score,
            "reasons": list(guard.reasons),
        }
        trace = {
            "stage": "branch_executed",
            "snapshot_id": snapshot.snapshot_id,
            "action_id": action.action_id,
            "action_type": action.action_type,
            "specialist": action.specialist,
            "target_region": action.target_region,
            "template_name": snapshot.template_name,
            "route": route.route,
            "difficulty_tier": difficulty.tier,
            "hard_valid": guard.valid,
            "score": score,
            "reasons": list(guard.reasons),
        }
        return {
            "snapshot": snapshot,
            "diagnostics": diagnostics,
            "difficulty": difficulty,
            "route": route,
            "guard": guard,
            "summary": summary,
            "trace": trace,
        }


__all__ = ["ParallelBranchScheduler"]
