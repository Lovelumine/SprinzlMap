from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask_restx import fields


@dataclass(frozen=True)
class AlignRouteModels:
    align_req: Any
    align_res: Any
    align_batch_req: Any
    align_batch_res: Any
    align_trace_req: Any
    align_trace_res: Any
    interpretation_req: Any
    interpretation_res: Any
    templates_res: Any
    session_req: Any
    session_email_req: Any
    session_res: Any
    session_get_res: Any
    session_list_res: Any


def build_align_models(ns) -> AlignRouteModels:
    align_req = ns.model("AlignRequest", {
        "target_seq": fields.String(
            required=True,
            description="Target tRNA sequence (A/U/G/C).",
            example="GGUCUCGUGGCCCAAUGGUUAAGGCGCUUGACUACGGAUCAAGAGAUUCCAGGUUCGACUCCUGGCGGGAUCG",
            default="GGUCUCGUGGCCCAAUGGUUAAGGCGCUUGACUACGGAUCAAGAGAUUCCAGGUUCGACUCCUGGCGGGAUCG",
        ),
        "anticodon": fields.String(
            description="Anticodon triplet (optional). (Backward-compatible: `anticode` also accepted.)",
            example="ACG",
            default="",
        ),
        "use_llm": fields.Boolean(
            description="Whether to prioritize LLM for alignment.",
            default=False,
            example=False,
        ),
        "llm_reasoning": fields.Boolean(
            description="Use reasoning mode for LLM postcheck (request-level override, e.g. deepseek-reasoner).",
            default=False,
            example=False,
        ),
        "template_name": fields.String(
            required=False,
            description="Optional template name override (seqname from templates list).",
            example="acidHosp1_chr.trna1-LeuTAG",
        ),
        "input_type": fields.String(
            required=False,
            enum=["mature", "gene_body", "pre_trna", "unknown"],
            default="unknown",
            description="Biological input type; unknown is retained when not established.",
        ),
        "domain_hint": fields.String(
            required=False,
            enum=["unknown", "archaea", "bacteria", "eukaryota", "cytosolic_eukaryote", "chloroplast", "cyanelle", "mitochondrial", "virus"],
            default="unknown",
            description=(
                "Optional biological domain/source hint. Unknown resolves to the general "
                "tRNAscan-SE mode (-G); explicit sources use their dedicated mode."
            ),
        ),
        "cca_status": fields.String(
            required=False,
            enum=["input_encoded", "inferred_post_transcriptional", "absent", "unknown"],
            default="unknown",
            description=(
                "Terminal CCA claim. input_encoded confirms that the input terminal CCA "
                "represents mature coordinates 74-76; it does not establish genomic encoding."
            ),
        ),
        "intron_status": fields.String(
            required=False,
            enum=["present", "absent", "removed", "unknown"],
            default="unknown",
            description="Known intron state; unknown is not coerced to absent.",
        ),
        "trna_class": fields.String(
            required=False,
            enum=["canonical", "selenocysteine", "unknown"],
            default="unknown",
            description="Explicit tRNA class; Sec uses domain-specific evidence models.",
        ),
        "mitochondrial_subtype": fields.String(
            required=False,
            enum=["mammal", "vertebrate", "unknown"],
            default="unknown",
            description="Validated tRNAscan-SE mitochondrial model scope.",
        ),
        "enable_root_recovery": fields.Boolean(
            required=False,
            default=False,
            description="Legacy-only compatibility flag for CM recovery after root failure.",
        ),
        "bypass_cache": fields.Boolean(default=False, description="Skip cached results and replace the cache with this run."),
    })

    align_res = ns.model("AlignResponse", {
        "template_name": fields.String(description="Name of the matched tRNA template."),
        "reference_template_name": fields.String(
            description="Concrete tRNA seqname used as the reference export template."
        ),
        "numbering_model": fields.Raw(
            description="Numbering model metadata, e.g. the Infernal CM model."
        ),
        "csv_content": fields.String(description="Three-line CSV text: numbering line + template line + aligned sequence line"),
        "warnings": fields.List(fields.String, description="Warnings about annotation/alignment quality"),
        "structure": fields.Raw(description="Optional structure overlay info (secondary_structure + id_pairs)."),
        "llm_trace": fields.Raw(description="Runtime LLM trace captured during alignment generation."),
        "agent": fields.Raw(description="Agent v2 diagnostics, route, branch summaries and trace."),
        "scientific_provenance": fields.Raw(description="Scientific models, scores, candidate contracts and position-level provenance."),
    })

    align_batch_req = ns.model("AlignBatchRequest", {
        "target_seqs": fields.List(
            fields.String,
            required=True,
            description="Target tRNA sequence list (A/U/G/C).",
            example=[
                "UAAACAGAAACUCGUUGGUUGGAGUGUUUAGCUGUUAACUAAAUAUUUGUGAGAUCGAAGCCCGCCUGUUUAU",
                "GGUAAGAUGGCUGAGUGUUAAAGCGUUAGGCUGUAGUCCUUUUUACAGAGGUUCAAUUCCUCUUCUUAUCG",
            ],
        ),
        "anticodon": fields.String(
            description="Anticodon triplet (optional). (Backward-compatible: `anticode` also accepted.)",
            example="ACG",
            default="",
        ),
        "use_llm": fields.Boolean(
            description="Whether to prioritize LLM for alignment.",
            default=False,
            example=False,
        ),
        "llm_reasoning": fields.Boolean(
            description="Use reasoning mode for LLM postcheck (request-level override, e.g. deepseek-reasoner).",
            default=False,
            example=False,
        ),
        "template_name": fields.String(
            required=False,
            description="Optional template name override (seqname from templates list).",
            example="acidHosp1_chr.trna1-LeuTAG",
        ),
        "batch_max_workers": fields.Integer(
            required=False,
            description="Optional per-request max worker override for sequence-level parallelism.",
            example=8,
        ),
        "input_type": fields.String(enum=["mature", "gene_body", "pre_trna", "unknown"], default="unknown"),
        "domain_hint": fields.String(enum=["unknown", "archaea", "bacteria", "eukaryota", "cytosolic_eukaryote", "chloroplast", "cyanelle", "mitochondrial", "virus"], default="unknown"),
        "cca_status": fields.String(enum=["input_encoded", "inferred_post_transcriptional", "absent", "unknown"], default="unknown"),
        "intron_status": fields.String(enum=["present", "absent", "removed", "unknown"], default="unknown"),
        "trna_class": fields.String(enum=["canonical", "selenocysteine", "unknown"], default="unknown"),
        "mitochondrial_subtype": fields.String(enum=["mammal", "vertebrate", "unknown"], default="unknown"),
        "enable_root_recovery": fields.Boolean(default=False),
        "bypass_cache": fields.Boolean(default=False, description="Skip cached results and replace the cache with this run."),
    })

    align_batch_item = ns.model("AlignBatchItem", {
        "index": fields.Integer(description="Sequence index in the input list."),
        "target_seq": fields.String(description="Original target sequence."),
        "ok": fields.Boolean(description="Whether this sequence succeeded."),
        "template_name": fields.String(description="Matched template name when succeeded."),
        "reference_template_name": fields.String(
            description="Concrete tRNA seqname used as the reference export template."
        ),
        "csv_content": fields.String(description="Three-line CSV text when succeeded."),
        "warnings": fields.List(fields.String, description="Warnings when succeeded."),
        "structure": fields.Raw(description="Optional structure overlay info."),
        "llm_trace": fields.Raw(description="Runtime LLM trace captured during alignment."),
        "agent": fields.Raw(description="Agent v2 diagnostics, route, branch summaries and trace."),
        "scientific_provenance": fields.Raw(description="Scientific numbering provenance when available."),
        "error": fields.String(description="Error message when failed."),
    })

    align_batch_res = ns.model("AlignBatchResponse", {
        "count": fields.Integer(description="Total sequence count."),
        "succeeded": fields.Integer(description="Succeeded sequence count."),
        "failed": fields.Integer(description="Failed sequence count."),
        "batch_workers": fields.Integer(description="Effective sequence-level worker count."),
        "elapsed_ms": fields.Float(description="Batch elapsed time in milliseconds."),
        "results": fields.List(fields.Nested(align_batch_item), description="Per-sequence results."),
    })

    align_trace_req = ns.model("AlignTraceRequest", {
        "target_seq": fields.String(
            required=True,
            description="Target tRNA sequence (A/U/G/C).",
            example="UAAACAGAAACUCGUUGGUUGGAGUGUUUAGCUGUUAACUAAAUAUUUGUGAGAUCGAAGCCCGCCUGUUUAU",
        ),
        "run_llm": fields.Boolean(
            required=False,
            description="Whether to execute actual LLM replay; false means prompt preview only (recommended for speed).",
            default=False,
            example=False,
        ),
        "llm_reasoning": fields.Boolean(
            required=False,
            description="Use reasoning profile for replay.",
            default=False,
            example=False,
        ),
        "template_name": fields.String(
            required=False,
            description="Optional template override for trace replay.",
            example="lactPlan_B21_chr.trna1-AsnGTT",
        ),
    })

    align_trace_res = ns.model("AlignTraceResponse", {
        "ok": fields.Boolean(description="Whether trace ran successfully."),
        "template_name": fields.String(description="Template used by trace replay."),
        "sequence_length": fields.Integer(description="Input sequence length."),
        "run_llm": fields.Boolean(description="Whether actual LLM replay was executed."),
        "warnings": fields.List(fields.String, description="Trace build warnings."),
        "regions": fields.List(fields.Raw, description="Per-region trace details."),
    })

    interpretation_req = ns.model("ScientificInterpretationRequest", {
        "target_seq": fields.String(required=True, description="Original tRNA sequence."),
        "coordinates": fields.Raw(
            required=True,
            description="Current candidate coordinate rows: labels and bases arrays.",
        ),
        "structure": fields.Raw(description="Completed structure annotation for the candidate."),
        "agent": fields.Raw(description="Completed Agent trace and disposition."),
        "scientific_provenance": fields.Raw(description="Completed CM and validation provenance."),
        "warnings": fields.List(fields.String, description="Completed-run warnings."),
        "record": fields.Raw(description="Optional record identifier only; it does not supply reference coordinates."),
        "language": fields.String(
            enum=["en", "zh-CN", "de", "fr"],
            default="en",
            description="Language used for the generated run-detail narrative.",
        ),
    })

    interpretation_res = ns.model("ScientificInterpretationResponse", {
        "schema_version": fields.String(description="Explanation schema version."),
        "cached": fields.Boolean(description="Whether a matching candidate explanation was reused."),
        "system_disposition": fields.Raw(description="Deterministic four-state system disposition."),
        "interpretation": fields.Raw(description="Model-generated sequence interpretation."),
        "evidence": fields.List(fields.Raw, description="Evidence items cited by the interpretation."),
        "provenance": fields.Raw(description="Model and evidence-version provenance."),
    })

    templates_res = ns.model("TemplatesResponse", {
        "templates": fields.List(fields.Raw, description="Template names or detailed template records"),
    })

    session_req = ns.model("AlignmentSessionRequest", {
        "payload": fields.Raw(
            required=True,
            description="Request payload metadata (mode, reference, inputs).",
        ),
        "results": fields.List(
            fields.Raw,
            required=True,
            description="Alignment results array.",
        ),
        "email": fields.String(
            required=False,
            description="Optional email for the request.",
            example="user@example.com",
        ),
    })

    session_email_req = ns.model("AlignmentSessionEmailRequest", {
        "email": fields.String(
            required=True,
            description="Email to associate with an existing request ID.",
            example="user@example.com",
        ),
    })

    session_res = ns.model("AlignmentSessionResponse", {
        "request_id": fields.String(description="Request ID"),
        "email": fields.String(description="Associated email, when supplied"),
        "created_at": fields.String(description="ISO timestamp"),
        "expires_at": fields.String(description="ISO timestamp"),
    })

    session_get_res = ns.model("AlignmentSessionGetResponse", {
        "request_id": fields.String(description="Request ID"),
        "email": fields.String(description="Optional email"),
        "payload": fields.Raw(description="Saved payload"),
        "results": fields.Raw(description="Saved results"),
        "created_at": fields.String(description="ISO timestamp"),
        "expires_at": fields.String(description="ISO timestamp"),
    })

    session_list_res = ns.model("AlignmentSessionListResponse", {
        "email": fields.String(description="Email used for lookup"),
        "items": fields.List(fields.Raw, description="List of request_id records"),
    })

    return AlignRouteModels(
        align_req=align_req,
        align_res=align_res,
        align_batch_req=align_batch_req,
        align_batch_res=align_batch_res,
        align_trace_req=align_trace_req,
        align_trace_res=align_trace_res,
        interpretation_req=interpretation_req,
        interpretation_res=interpretation_res,
        templates_res=templates_res,
        session_req=session_req,
        session_email_req=session_email_req,
        session_res=session_res,
        session_get_res=session_get_res,
        session_list_res=session_list_res,
    )


def build_align_export_response_model(export_ns):
    return export_ns.model("ExportResponse", {
        "template_name": fields.String(description="Template name"),
        "csv_content": fields.String(description="Two-line CSV text"),
    })
