from __future__ import annotations

from flask import request
from flask_restx import Namespace, Resource, fields

from backend.application.alignment.scientific_interpretation import (
    ScientificInterpretationError,
)
from backend.domain.gold_curation.service import (
    DEFAULT_RUN_ID,
    ensure_run_available,
    append_draft,
    append_decision,
    export_curated_tsv,
    generate_case_interpretation,
    list_cases,
    list_runs,
    load_case_bundle,
    load_case_process,
    set_case_evaluation,
    summary,
)


def _run_id() -> str:
    run_id = str(request.args.get("run_id") or DEFAULT_RUN_ID).strip() or DEFAULT_RUN_ID
    ensure_run_available(run_id)
    return run_id


def _scope() -> str:
    return str(request.args.get("scope") or "").strip()


def register_gold_curation_namespace(api) -> None:
    ns = Namespace("gold-curation", description="Manual gold-standard curation")

    decision_req = ns.model(
        "GoldCurationDecisionRequest",
        {
            "status": fields.String(
                required=True,
                description="accepted_gold, accepted_model, manual_corrected, unsure, or excluded.",
                example="manual_corrected",
            ),
            "curated_labels": fields.List(
                fields.String,
                required=False,
                description="One Sprinzl label per original residue for manual_corrected.",
            ),
            "notes": fields.String(required=False, description="Free-form curator notes."),
            "exclusion_reason": fields.String(
                required=False,
                description="Reason for excluding a record, for example non_canonical_trna.",
            ),
            "curated_by": fields.String(required=False, description="Curator identifier."),
        },
    )

    evaluation_req = ns.model(
        "GoldCurationEvaluationRequest",
        {
            "allowed": fields.Boolean(
                required=True,
                description="Whether this sequence counts toward the benchmark score.",
                example=False,
            ),
            "reason": fields.String(
                required=False,
                description="Required when allowed is false, for example non_canonical_trna.",
            ),
        },
    )

    @ns.route("/runs", strict_slashes=False)
    class GoldCurationRuns(Resource):
        @ns.doc(description="Benchmark batches the curation page can open.")
        def get(self):
            try:
                return list_runs(), 200
            except Exception as exc:
                ns.abort(500, f"Gold curation run list failed: {exc}")

    @ns.route("/summary", strict_slashes=False)
    class GoldCurationSummary(Resource):
        def get(self):
            try:
                return summary(run_id=_run_id(), scope=_scope()), 200
            except FileNotFoundError as exc:
                ns.abort(404, str(exc))
            except Exception as exc:
                ns.abort(500, f"Gold curation summary failed: {exc}")

    @ns.route("/cases", strict_slashes=False)
    class GoldCurationCases(Resource):
        @ns.doc(
            params={
                "run_id": "Benchmark run id.",
                "status": "all, reviewable, auto_agree, needs_review, completed, accepted_gold, accepted_model, manual_corrected, unsure, excluded. completed selects every committed decision.",
                "region": "Optional region filter.",
                "q": "Optional seqname search.",
                "scope": "Optional review campaign scope: classic_three_domain_mismatch or all_evaluation_mismatch.",
                "limit": "Page size.",
                "offset": "Page offset.",
            }
        )
        def get(self):
            try:
                limit = int(request.args.get("limit") or 200)
                offset = int(request.args.get("offset") or 0)
                data = list_cases(
                    run_id=_run_id(),
                    status=str(request.args.get("status") or "needs_review"),
                    region=str(request.args.get("region") or ""),
                    query=str(request.args.get("q") or ""),
                    scope=_scope(),
                    limit=limit,
                    offset=offset,
                )
                return data, 200
            except FileNotFoundError as exc:
                ns.abort(404, str(exc))
            except Exception as exc:
                ns.abort(500, f"Gold curation list failed: {exc}")

    @ns.route("/cases/<path:seqname>", strict_slashes=False)
    class GoldCurationCase(Resource):
        def get(self, seqname: str):
            try:
                return load_case_bundle(seqname, run_id=_run_id(), scope=_scope()), 200
            except KeyError:
                ns.abort(404, f"Gold case not found: {seqname}")
            except FileNotFoundError as exc:
                ns.abort(404, str(exc))
            except Exception as exc:
                ns.abort(500, f"Gold curation case failed: {exc}")

    @ns.route("/cases/<path:seqname>/decision", strict_slashes=False)
    class GoldCurationDecision(Resource):
        @ns.expect(decision_req, validate=True)
        def post(self, seqname: str):
            try:
                payload = request.get_json(force=True) or {}
                record = append_decision(seqname, payload, run_id=_run_id(), scope=_scope())
                return {"ok": True, "record": record}, 200
            except KeyError:
                ns.abort(404, f"Gold case not found: {seqname}")
            except ValueError as exc:
                ns.abort(400, str(exc))
            except FileNotFoundError as exc:
                ns.abort(404, str(exc))
            except Exception as exc:
                ns.abort(500, f"Gold curation save failed: {exc}")

    @ns.route("/cases/<path:seqname>/evaluation", strict_slashes=False)
    class GoldCurationCaseEvaluation(Resource):
        @ns.expect(evaluation_req, validate=True)
        def post(self, seqname: str):
            """Flip scoring eligibility for one sequence; the answer is untouched."""
            try:
                payload = request.get_json(force=True) or {}
                if payload.get("allowed") is None:
                    raise ValueError("`allowed` is required.")
                record = set_case_evaluation(
                    seqname,
                    allowed=bool(payload.get("allowed")),
                    reason=str(payload.get("reason") or ""),
                )
                return {"ok": True, "record": record}, 200
            except LookupError as exc:
                ns.abort(404, str(exc))
            except ValueError as exc:
                ns.abort(400, str(exc))
            except Exception as exc:
                ns.abort(500, f"Gold curation eligibility failed: {exc}")

    @ns.route("/cases/<path:seqname>/process", strict_slashes=False)
    class GoldCurationCaseProcess(Resource):
        @ns.doc(description="Stored derivation for one case: steps, decisions, tool calls and cost.")
        def get(self, seqname: str):
            try:
                return load_case_process(seqname, run_id=_run_id()), 200
            except KeyError:
                ns.abort(404, f"Gold case not found: {seqname}")
            except Exception as exc:
                ns.abort(500, f"Gold curation process failed: {exc}")

    @ns.route("/cases/<path:seqname>/interpretation", strict_slashes=False)
    class GoldCurationInterpretation(Resource):
        def post(self, seqname: str):
            try:
                return generate_case_interpretation(
                    seqname,
                    run_id=_run_id(),
                    scope=_scope(),
                    language=str(request.args.get("language") or "en"),
                ), 200
            except KeyError:
                ns.abort(404, f"Gold case not found: {seqname}")
            except ValueError as exc:
                ns.abort(400, str(exc))
            except ScientificInterpretationError as exc:
                ns.abort(502, f"Scientific interpretation unavailable: {exc}")
            except FileNotFoundError as exc:
                ns.abort(404, str(exc))
            except Exception as exc:
                ns.abort(500, f"Gold interpretation failed: {exc}")

    @ns.route("/cases/<path:seqname>/draft", strict_slashes=False)
    class GoldCurationDraft(Resource):
        @ns.expect(decision_req, validate=False)
        def post(self, seqname: str):
            try:
                payload = request.get_json(force=True) or {}
                record = append_draft(seqname, payload, run_id=_run_id(), scope=_scope())
                return {"ok": True, "record": record}, 200
            except KeyError:
                ns.abort(404, f"Gold case not found: {seqname}")
            except FileNotFoundError as exc:
                ns.abort(404, str(exc))
            except Exception as exc:
                ns.abort(500, f"Gold curation draft save failed: {exc}")

    @ns.route("/export", strict_slashes=False)
    class GoldCurationExport(Resource):
        def post(self):
            try:
                return {"ok": True, **export_curated_tsv()}, 200
            except FileNotFoundError as exc:
                ns.abort(404, str(exc))
            except Exception as exc:
                ns.abort(500, f"Gold curation export failed: {exc}")

    api.add_namespace(ns)
