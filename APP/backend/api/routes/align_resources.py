from __future__ import annotations

import json
import math
import os
import queue
import threading
from typing import Any, Dict

from flask import Response, request, stream_with_context
from flask_restx import Namespace, Resource

from backend.application.alignment.alignment_wrapper import (
    iter_batch_alignment_events_with_meta,
    perform_batch_alignment_with_meta,
    perform_full_alignment_with_meta,
)
from backend.application.alignment.prompt_trace import trace_prompt_pipeline
from backend.application.alignment.scientific_interpretation import (
    ScientificInterpretationError,
    generate_scientific_interpretation,
)
from backend.domain.alignment.engine import postcheck_runtime_overrides
from backend.domain.alignment.preparation.template_retrieval import list_template_names, list_template_records
from backend.domain.alignment.tRNAs_flat_exporter import export_template_csv
from backend.infra.storage.alignment_session_store import (
    create_session,
    fetch_session,
    list_sessions_by_email,
    update_session_email,
)

from .align_models import AlignRouteModels
from .align_payloads import (
    parse_align_request,
    parse_batch_request,
    parse_export_query,
    parse_session_create_request,
    parse_session_email_update_request,
    parse_session_list_query,
    parse_template_query,
    parse_trace_request,
)

try:
    from backend.infra.config.runtime_settings import get_effective_runtime_settings
except Exception:  # pragma: no cover
    get_effective_runtime_settings = None  # type: ignore[assignment]


def _align_batch_max_size() -> int:
    raw = None
    if get_effective_runtime_settings is not None:
        try:
            values = get_effective_runtime_settings()
            if isinstance(values, dict):
                raw = values.get("ALIGN_BATCH_MAX_SIZE")
        except Exception:
            raw = None
    if raw in (None, ""):
        raw = os.getenv("ALIGN_BATCH_MAX_SIZE", "128")
    try:
        value = int(raw)
    except Exception:
        value = 128
    return max(1, min(value, 1000))


def _sse_event(event: str, data: object) -> str:
    payload = json.dumps(_json_safe(data), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


def _trace_runtime_values(llm_reasoning: bool) -> Dict[str, Any]:
    runtime_values: Dict[str, Any] = {}
    if get_effective_runtime_settings is not None:
        try:
            values = get_effective_runtime_settings()
            if isinstance(values, dict):
                runtime_values.update(values)
        except Exception:
            pass
    runtime_values.update(
        postcheck_runtime_overrides(
            llm_reasoning=llm_reasoning,
            runtime_values_override=runtime_values,
        )
    )
    return runtime_values


def _split_alignment_meta(
    meta: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    structure = {}
    llm_trace = {}
    agent = {}
    scientific = {}
    if isinstance(meta, dict):
        structure = _json_safe(dict(meta.get("structure_view") or {}))
        llm_trace = _json_safe(dict(meta.get("llm_trace_view") or {}))
        agent = _json_safe(dict(meta.get("agent_v2") or {}))
        annotation_meta = dict(meta.get("annotation_meta") or {})
        trnascan_meta = {}
        if "trnascan_search_mode" in annotation_meta:
            trnascan_meta = {
                "search_mode": annotation_meta.get("trnascan_search_mode", "G"),
                "domain_hint": annotation_meta.get("trnascan_domain_hint", "unknown"),
                "mitochondrial_subtype": annotation_meta.get(
                    "trnascan_mitochondrial_subtype", "unknown"
                ),
            }
        scientific = _json_safe({
            "numbering_backend": meta.get("numbering_backend"),
            "numbering_model": dict(meta.get("numbering_model") or {}),
            "cm_score": dict(meta.get("cm_score") or {}),
            "cm_portfolio": dict(meta.get("cm_portfolio") or {}),
            "cm_numbering": dict(meta.get("cm_numbering") or {}),
            "cm_tool": dict(meta.get("cm_tool") or {}),
            "specialized_route": dict(meta.get("specialized_route") or {}),
            "input_transform": dict(meta.get("input_transform") or {}),
            "root_recovery": dict(meta.get("root_recovery") or {}),
            "legacy_root_failure": dict(meta.get("legacy_root_failure") or {}),
            "agent_strategy": dict(meta.get("agent_strategy") or {}),
            "legacy_reference_tool": dict(meta.get("legacy_reference_tool") or {}),
            "boundary_refinement_contract": dict(
                meta.get("boundary_refinement_contract") or {}
            ),
            "regional_candidate_contract": dict(
                meta.get("regional_candidate_contract") or {}
            ),
            "structural_region_evidence": dict(
                meta.get("structural_region_evidence") or {}
            ),
            "variable_bridge_evidence": dict(
                meta.get("variable_bridge_evidence") or {}
            ),
            "variable_boundary_reconciliation": dict(
                meta.get("variable_boundary_reconciliation") or {}
            ),
            "acceptor_tail_reconciliation": dict(
                meta.get("acceptor_tail_reconciliation") or {}
            ),
            "trnascan": trnascan_meta,
        })
    return structure, llm_trace, agent, scientific


def register_align_routes(ns: Namespace, models: AlignRouteModels) -> None:
    @ns.route("/explanation")
    class AlignScientificInterpretation(Resource):
        @ns.expect(models.interpretation_req, validate=False)
        @ns.response(200, "Success", models.interpretation_res)
        @ns.response(400, "Bad Request")
        @ns.response(502, "Interpretation unavailable")
        def post(self):
            payload = request.get_json(force=True) or {}
            target_seq = str(payload.get("target_seq") or "").strip()
            coordinates = payload.get("coordinates")
            if not target_seq or not isinstance(coordinates, dict):
                ns.abort(400, "target_seq and coordinates are required.")
            labels = coordinates.get("labels")
            bases = coordinates.get("bases")
            if not isinstance(labels, list) or not isinstance(bases, list):
                ns.abort(400, "coordinates.labels and coordinates.bases must be arrays.")
            if len(labels) != len(bases) or len(labels) > 256:
                ns.abort(400, "Candidate coordinate rows are invalid.")
            try:
                result = generate_scientific_interpretation(
                    sequence=target_seq,
                    labels=labels,
                    bases=bases,
                    structure=dict(payload.get("structure") or {}),
                    agent=dict(payload.get("agent") or {}),
                    scientific_provenance=dict(payload.get("scientific_provenance") or {}),
                    warnings=list(payload.get("warnings") or []),
                    record=dict(payload.get("record") or {}),
                    language=str(payload.get("language") or "en"),
                )
            except ValueError as exc:
                ns.abort(400, str(exc))
            except ScientificInterpretationError as exc:
                ns.abort(502, f"Scientific interpretation unavailable: {exc}")
            return _json_safe(result), 200


    @ns.route("/explanation/stream")
    class AlignScientificInterpretationStream(Resource):
        @ns.response(200, "Success (text/event-stream)")
        def post(self):
            payload = request.get_json(force=True) or {}
            target_seq = str(payload.get("target_seq") or "").strip()
            coordinates = payload.get("coordinates")
            if not target_seq or not isinstance(coordinates, dict):
                ns.abort(400, "target_seq and coordinates are required.")
            labels = coordinates.get("labels")
            bases = coordinates.get("bases")
            if not isinstance(labels, list) or not isinstance(bases, list):
                ns.abort(400, "coordinates.labels and coordinates.bases must be arrays.")
            if len(labels) != len(bases) or len(labels) > 256:
                ns.abort(400, "Candidate coordinate rows are invalid.")

            events: queue.Queue[tuple[str, Any]] = queue.Queue()

            def publish(event: dict[str, Any]) -> None:
                process_event = event.get("process_event") if isinstance(event, dict) else None
                if isinstance(process_event, dict) and process_event.get("stage") == "llm_delta":
                    content = str(process_event.get("content") or "")
                    if content:
                        events.put(("delta", content))

            def generate() -> None:
                try:
                    result = generate_scientific_interpretation(
                        sequence=target_seq,
                        labels=labels,
                        bases=bases,
                        structure=dict(payload.get("structure") or {}),
                        agent=dict(payload.get("agent") or {}),
                        scientific_provenance=dict(payload.get("scientific_provenance") or {}),
                        warnings=list(payload.get("warnings") or []),
                        record=dict(payload.get("record") or {}),
                        language=str(payload.get("language") or "en"),
                        status_callback=publish,
                    )
                    events.put(("complete", result))
                except ValueError as exc:
                    events.put(("error", (400, str(exc))))
                except ScientificInterpretationError as exc:
                    events.put(("error", (502, f"Scientific interpretation unavailable: {exc}")))
                except Exception as exc:
                    events.put(("error", (500, f"Scientific interpretation failed: {exc}")))

            def stream():
                worker = threading.Thread(target=generate, daemon=True)
                worker.start()
                while True:
                    event, data = events.get()
                    if event == "delta":
                        yield _sse_event("delta", {"content": data})
                    elif event == "complete":
                        yield _sse_event("complete", data)
                        break
                    else:
                        status, message = data
                        yield _sse_event("error", {"message": message, "status": status})
                        break

            return Response(
                stream_with_context(stream()),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

    @ns.route("/")
    class Align(Resource):
        @ns.expect(models.align_req, validate=True)
        @ns.response(200, "Success", models.align_res)
        @ns.response(400, "Bad Request")
        @ns.response(500, "Internal Server Error")
        def post(self):
            payload = parse_align_request(request.get_json(force=True) or {})
            try:
                tpl_name, csv_raw, warnings, meta = perform_full_alignment_with_meta(
                    target_seq=payload.target_seq,
                    output_csv_path="/dev/null",
                    anticode=payload.anticodon,
                    use_llm=payload.use_llm,
                    llm_reasoning=payload.llm_reasoning,
                    template_name=payload.template_name,
                    input_type=payload.input_type,
                    domain_hint=payload.domain_hint,
                    cca_status=payload.cca_status,
                    intron_status=payload.intron_status,
                    trna_class=payload.trna_class,
                    mitochondrial_subtype=payload.mitochondrial_subtype,
                    enable_root_recovery=payload.enable_root_recovery,
                    bypass_cache=payload.bypass_cache,
                )
            except ValueError as exc:
                ns.abort(400, str(exc))
            except Exception as exc:
                ns.abort(500, f"Alignment failed: {exc}")

            structure, llm_trace, agent, scientific = _split_alignment_meta(meta)
            reference_template_name = str(
                meta.get("reference_template_name") or tpl_name or ""
            )
            return _json_safe({
                "template_name": reference_template_name,
                "reference_template_name": reference_template_name,
                "numbering_model": dict(meta.get("numbering_model") or {}),
                "csv_content": csv_raw,
                "warnings": warnings,
                "structure": structure,
                "llm_trace": llm_trace,
                "agent": agent,
                "scientific_provenance": scientific,
            }), 200

    @ns.route("/batch")
    class AlignBatch(Resource):
        @ns.expect(models.align_batch_req, validate=True)
        @ns.response(200, "Success", models.align_batch_res)
        @ns.response(400, "Bad Request")
        @ns.response(500, "Internal Server Error")
        def post(self):
            try:
                payload = parse_batch_request(request.get_json(force=True) or {}, _align_batch_max_size())
            except ValueError as exc:
                ns.abort(400, str(exc))
            try:
                resp = perform_batch_alignment_with_meta(
                    target_seqs=payload.target_seqs,
                    anticode=payload.anticodon,
                    use_llm=payload.use_llm,
                    llm_reasoning=payload.llm_reasoning,
                    template_name=payload.template_name,
                    batch_max_workers=payload.batch_max_workers,
                    input_type=payload.input_type,
                    domain_hint=payload.domain_hint,
                    cca_status=payload.cca_status,
                    intron_status=payload.intron_status,
                    trna_class=payload.trna_class,
                    mitochondrial_subtype=payload.mitochondrial_subtype,
                    enable_root_recovery=payload.enable_root_recovery,
                )
            except Exception as exc:
                ns.abort(500, f"Batch alignment failed: {exc}")
            return _json_safe(resp), 200

    @ns.route("/batch/stream")
    class AlignBatchStream(Resource):
        @ns.expect(models.align_batch_req, validate=True)
        @ns.response(200, "Success (text/event-stream)")
        @ns.response(400, "Bad Request")
        def post(self):
            try:
                payload = parse_batch_request(request.get_json(force=True) or {}, _align_batch_max_size())
            except ValueError as exc:
                ns.abort(400, str(exc))

            def _generate():
                try:
                    for evt in iter_batch_alignment_events_with_meta(
                        target_seqs=payload.target_seqs,
                        anticode=payload.anticodon,
                        use_llm=payload.use_llm,
                        llm_reasoning=payload.llm_reasoning,
                        template_name=payload.template_name,
                        batch_max_workers=payload.batch_max_workers,
                        input_type=payload.input_type,
                        domain_hint=payload.domain_hint,
                        cca_status=payload.cca_status,
                        intron_status=payload.intron_status,
                        trna_class=payload.trna_class,
                        mitochondrial_subtype=payload.mitochondrial_subtype,
                        enable_root_recovery=payload.enable_root_recovery,
                        bypass_cache=payload.bypass_cache,
                    ):
                        ev_name = str((evt or {}).get("event") or "message")
                        ev_data = (evt or {}).get("data")
                        yield _sse_event(ev_name, ev_data)
                except Exception as exc:
                    yield _sse_event("error", {"message": f"Batch stream failed: {exc}"})
                yield _sse_event("close", {"ok": True})

            headers = {
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
            return Response(
                stream_with_context(_generate()),
                mimetype="text/event-stream",
                headers=headers,
            )

    @ns.route("/trace")
    class AlignTrace(Resource):
        @ns.expect(models.align_trace_req, validate=True)
        @ns.response(200, "Success", models.align_trace_res)
        @ns.response(400, "Bad Request")
        @ns.response(500, "Internal Server Error")
        def post(self):
            try:
                payload = parse_trace_request(request.get_json(force=True) or {})
            except ValueError as exc:
                ns.abort(400, str(exc))
            try:
                traced = trace_prompt_pipeline(
                    sequence=payload.target_seq,
                    runtime_values=_trace_runtime_values(llm_reasoning=payload.llm_reasoning),
                    run_llm=payload.run_llm,
                    template_name=payload.template_name,
                )
            except ValueError as exc:
                ns.abort(400, str(exc))
            except Exception as exc:
                ns.abort(500, f"Trace failed: {exc}")
            return _json_safe(traced), 200

    @ns.route("/templates")
    class Templates(Resource):
        @ns.response(200, "Success", models.templates_res)
        def get(self):
            try:
                query = parse_template_query(request.args)
                if query.details:
                    records = list_template_records(q=query.query, limit=query.limit)
                    return {"templates": records}, 200
                names = list_template_names()
                if query.query:
                    q_lower = query.query.lower()
                    names = [name for name in names if q_lower in name.lower()]
                if query.limit and query.limit > 0:
                    names = names[:query.limit]
            except Exception as exc:
                ns.abort(500, f"Template list error: {exc}")
            return {"templates": names}, 200

    @ns.route("/session")
    class AlignmentSession(Resource):
        @ns.expect(models.session_req, validate=True)
        @ns.response(200, "Success", models.session_res)
        @ns.response(400, "Bad Request")
        @ns.response(503, "Service Unavailable")
        @ns.response(500, "Internal Server Error")
        def post(self):
            try:
                payload = parse_session_create_request(request.get_json(force=True) or {})
            except ValueError as exc:
                ns.abort(400, str(exc))
            try:
                resp = create_session(
                    payload=payload.payload,
                    results=payload.results,
                    email=payload.email,
                    ttl_days=7,
                )
            except RuntimeError as exc:
                ns.abort(503, str(exc))
            except Exception as exc:
                ns.abort(500, f"Session create failed: {exc}")
            return resp, 200

        @ns.response(200, "Success", models.session_list_res)
        @ns.response(400, "Bad Request")
        @ns.response(503, "Service Unavailable")
        @ns.response(500, "Internal Server Error")
        def get(self):
            try:
                query = parse_session_list_query(request.args)
            except ValueError as exc:
                ns.abort(400, str(exc))
            try:
                data = list_sessions_by_email(email=query.email, limit=query.limit)
            except RuntimeError as exc:
                ns.abort(503, str(exc))
            except Exception as exc:
                ns.abort(500, f"Session list failed: {exc}")
            return data, 200

    @ns.route("/session/<string:request_id>")
    class AlignmentSessionGet(Resource):
        @ns.response(200, "Success", models.session_get_res)
        @ns.response(404, "Not Found")
        @ns.response(503, "Service Unavailable")
        @ns.response(500, "Internal Server Error")
        def get(self, request_id: str):
            try:
                data = fetch_session(request_id)
            except RuntimeError as exc:
                ns.abort(503, str(exc))
            except Exception as exc:
                ns.abort(500, f"Session fetch failed: {exc}")
            if not data:
                ns.abort(404, f"Request ID not found or expired: {request_id}")
            return data, 200

    @ns.route("/session/<string:request_id>/email")
    class AlignmentSessionEmail(Resource):
        @ns.expect(models.session_email_req, validate=True)
        @ns.response(200, "Success", models.session_res)
        @ns.response(400, "Bad Request")
        @ns.response(404, "Not Found")
        @ns.response(503, "Service Unavailable")
        @ns.response(500, "Internal Server Error")
        def patch(self, request_id: str):
            try:
                payload = parse_session_email_update_request(
                    request.get_json(force=True) or {}
                )
            except ValueError as exc:
                ns.abort(400, str(exc))
            try:
                data = update_session_email(request_id, payload.email)
            except RuntimeError as exc:
                ns.abort(503, str(exc))
            except Exception as exc:
                ns.abort(500, f"Session email update failed: {exc}")
            if not data:
                ns.abort(404, f"Request ID not found or expired: {request_id}")
            return data, 200


def register_align_export_routes(export_ns: Namespace, export_res) -> None:
    @export_ns.route("/")
    class Export(Resource):
        @export_ns.doc(
            params={
                "template_name": {
                    "description": "Template name from /align response (seqname).",
                    "example": "acidHosp1_chr.trna1-LeuTAG",
                }
            }
        )
        @export_ns.response(200, "Success", export_res)
        @export_ns.response(400, "Missing or invalid parameter")
        @export_ns.response(404, "Template not found")
        def get(self):
            try:
                query = parse_export_query(request.args)
            except ValueError as exc:
                export_ns.abort(400, str(exc))
            try:
                csv_raw = export_template_csv(query.template_name)
            except Exception as exc:
                export_ns.abort(500, f"Export error: {exc}")
            if csv_raw is None:
                export_ns.abort(404, f"Template not found: {query.template_name}")
            return {"template_name": query.template_name, "csv_content": csv_raw}, 200
