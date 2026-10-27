from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Optional, Sequence


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _normalized_email(value: Any, *, required: bool = False) -> Optional[str]:
    email = str(value or "").strip().lower()
    if not email:
        if required:
            raise ValueError("Field `email` is required.")
        return None
    if len(email) > 255 or not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Field `email` must be a valid email address.")
    return email


@dataclass(frozen=True)
class AlignRequestPayload:
    target_seq: str
    anticodon: str
    use_llm: bool
    llm_reasoning: bool
    template_name: Optional[str]
    input_type: str
    domain_hint: str
    cca_status: str
    intron_status: str
    trna_class: str
    mitochondrial_subtype: str
    enable_root_recovery: bool
    bypass_cache: bool


@dataclass(frozen=True)
class AlignBatchRequestPayload:
    target_seqs: list[str]
    anticodon: str
    use_llm: bool
    llm_reasoning: bool
    template_name: Optional[str]
    batch_max_workers: Any
    input_type: str
    domain_hint: str
    cca_status: str
    intron_status: str
    trna_class: str
    mitochondrial_subtype: str
    enable_root_recovery: bool
    bypass_cache: bool


@dataclass(frozen=True)
class AlignTraceRequestPayload:
    target_seq: str
    run_llm: bool
    llm_reasoning: bool
    template_name: Optional[str]


@dataclass(frozen=True)
class TemplateQuery:
    query: str
    limit: Optional[int]
    details: bool


@dataclass(frozen=True)
class SessionCreatePayload:
    payload: Any
    results: Any
    email: Optional[str]


@dataclass(frozen=True)
class SessionEmailUpdatePayload:
    email: str


@dataclass(frozen=True)
class SessionListQuery:
    email: str
    limit: int


@dataclass(frozen=True)
class ExportQuery:
    template_name: str


def parse_align_request(data: Mapping[str, Any]) -> AlignRequestPayload:
    target_seq = str(data.get("target_seq") or "").strip()
    if not target_seq:
        raise ValueError("Field `target_seq` is required and must be non-empty.")
    input_type = _choice(data.get("input_type"), {"mature", "gene_body", "pre_trna", "unknown"}, "input_type")
    domain_hint = _choice(
        data.get("domain_hint"),
        {
            "unknown", "archaea", "bacteria", "eukaryota", "cytosolic_eukaryote",
            "chloroplast", "cyanelle", "mitochondrial", "virus",
        },
        "domain_hint",
    )
    cca_status = _choice(
        data.get("cca_status"),
        {"input_encoded", "inferred_post_transcriptional", "absent", "unknown"},
        "cca_status",
    )
    intron_status = _choice(
        data.get("intron_status"),
        {"present", "absent", "removed", "unknown"},
        "intron_status",
    )
    trna_class = _choice(
        data.get("trna_class"),
        {"canonical", "selenocysteine", "unknown"},
        "trna_class",
    )
    mitochondrial_subtype = _choice(
        data.get("mitochondrial_subtype"),
        {"mammal", "vertebrate", "unknown"},
        "mitochondrial_subtype",
    )
    return AlignRequestPayload(
        target_seq=target_seq,
        anticodon=str(data.get("anticodon") or data.get("anticode") or "").strip(),
        use_llm=bool(data.get("use_llm", False)),
        llm_reasoning=bool(data.get("llm_reasoning", False)),
        template_name=_optional_text(data.get("template_name")),
        input_type=input_type,
        domain_hint=domain_hint,
        cca_status=cca_status,
        intron_status=intron_status,
        trna_class=trna_class,
        mitochondrial_subtype=mitochondrial_subtype,
        enable_root_recovery=bool(data.get("enable_root_recovery", False)),
        bypass_cache=bool(data.get("bypass_cache", False)),
    )


def _choice(value: Any, choices: set[str], field_name: str) -> str:
    normalized = str(value or "unknown").strip().lower() or "unknown"
    if normalized not in choices:
        allowed = ", ".join(sorted(choices))
        raise ValueError(f"Field `{field_name}` must be one of: {allowed}.")
    return normalized


def parse_batch_request(data: Mapping[str, Any], max_size: int) -> AlignBatchRequestPayload:
    raw_list = data.get("target_seqs")
    if not isinstance(raw_list, list) or not raw_list:
        raise ValueError("Field `target_seqs` is required and must be a non-empty list.")
    if len(raw_list) > max_size:
        raise ValueError(f"`target_seqs` exceeds max batch size ({max_size}).")
    input_type = _choice(data.get("input_type"), {"mature", "gene_body", "pre_trna", "unknown"}, "input_type")
    domain_hint = _choice(
        data.get("domain_hint"),
        {
            "unknown", "archaea", "bacteria", "eukaryota", "cytosolic_eukaryote",
            "chloroplast", "cyanelle", "mitochondrial", "virus",
        },
        "domain_hint",
    )
    cca_status = _choice(
        data.get("cca_status"),
        {"input_encoded", "inferred_post_transcriptional", "absent", "unknown"},
        "cca_status",
    )
    intron_status = _choice(
        data.get("intron_status"),
        {"present", "absent", "removed", "unknown"},
        "intron_status",
    )
    trna_class = _choice(
        data.get("trna_class"),
        {"canonical", "selenocysteine", "unknown"},
        "trna_class",
    )
    mitochondrial_subtype = _choice(
        data.get("mitochondrial_subtype"),
        {"mammal", "vertebrate", "unknown"},
        "mitochondrial_subtype",
    )
    return AlignBatchRequestPayload(
        target_seqs=[str(x or "").strip() for x in raw_list],
        anticodon=str(data.get("anticodon") or data.get("anticode") or "").strip(),
        use_llm=bool(data.get("use_llm", False)),
        llm_reasoning=bool(data.get("llm_reasoning", False)),
        template_name=_optional_text(data.get("template_name")),
        batch_max_workers=data.get("batch_max_workers"),
        input_type=input_type,
        domain_hint=domain_hint,
        cca_status=cca_status,
        intron_status=intron_status,
        trna_class=trna_class,
        mitochondrial_subtype=mitochondrial_subtype,
        enable_root_recovery=bool(data.get("enable_root_recovery", False)),
        bypass_cache=bool(data.get("bypass_cache", False)),
    )


def parse_trace_request(data: Mapping[str, Any]) -> AlignTraceRequestPayload:
    target_seq = str(data.get("target_seq") or "").strip()
    if not target_seq:
        raise ValueError("Field `target_seq` is required and must be non-empty.")
    return AlignTraceRequestPayload(
        target_seq=target_seq,
        run_llm=bool(data.get("run_llm", False)),
        llm_reasoning=bool(data.get("llm_reasoning", False)),
        template_name=_optional_text(data.get("template_name")),
    )


def parse_template_query(args: Mapping[str, Any]) -> TemplateQuery:
    query = str(args.get("q") or "").strip()
    limit_raw = str(args.get("limit") or "").strip()
    details = str(args.get("details") or "").strip().lower() in ("1", "true", "yes")
    limit = int(limit_raw) if limit_raw.isdigit() else None
    return TemplateQuery(query=query, limit=limit, details=details)


def parse_session_create_request(data: Mapping[str, Any]) -> SessionCreatePayload:
    payload = data.get("payload")
    results = data.get("results")
    if payload is None or results is None:
        raise ValueError("Both `payload` and `results` are required.")
    return SessionCreatePayload(
        payload=payload,
        results=results,
        email=_normalized_email(data.get("email")),
    )


def parse_session_email_update_request(data: Mapping[str, Any]) -> SessionEmailUpdatePayload:
    return SessionEmailUpdatePayload(
        email=_normalized_email(data.get("email"), required=True) or "",
    )


def parse_session_list_query(args: Mapping[str, Any]) -> SessionListQuery:
    email = _normalized_email(args.get("email"), required=True) or ""
    limit_raw = str(args.get("limit") or "").strip()
    limit = int(limit_raw) if limit_raw.isdigit() else 20
    return SessionListQuery(email=email, limit=limit)


def parse_export_query(args: Mapping[str, Any]) -> ExportQuery:
    template_name = str(args.get("template_name") or "").strip()
    if not template_name:
        raise ValueError("Missing query parameter `template_name`")
    return ExportQuery(template_name=template_name)
