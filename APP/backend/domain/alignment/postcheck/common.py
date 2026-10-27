from __future__ import annotations

import inspect
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from backend.infra.llm.provider_config import (
    apply_openai_provider_extras,
    openai_chat_completions_url,
)


_STATUS_PASS_THROUGH_DISABLED = {
    "skipped_use_llm_off",
    "skipped_postcheck_disabled",
    "postcheck_disabled",
    "skipped_prompt_disabled",
    "skipped_prompt_empty",
    "skipped_no_handler",
}

_STATUS_PASS_THROUGH_NO_IMPROVEMENT = {
    "skipped_template_all_match",
    "no_variable_positions",
    "no_improved_candidate",
    "keep_current",
    "invalid_block",
    "missing_blocks",
}

_STATUS_PASS_THROUGH_HANDLER_ERROR = {
    "handler_error_pass_through",
}

_ATTEMPT_NON_REJECTION_STATUSES = {
    "",
    "accepted",
    "candidate_ready",
    "keep_current",
    "ready",
}


def _norm_base(raw: Any) -> str:
    token = str(raw or "").strip().upper().replace("T", "U")
    return token if token in {"A", "U", "G", "C", "N", "-"} else "-"


def snapshot_block(block: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    lines = (block or {}).get("lines")
    if not (isinstance(lines, list) and len(lines) == 3):
        return {
            "region": str((block or {}).get("region") or ""),
            "ids": [],
            "template_tokens": [],
            "target_tokens": [],
        }
    ids = [str(x or "") for x in list(lines[0])]
    template_tokens = [_norm_base(x) for x in list(lines[1])]
    target_tokens = [_norm_base(x) for x in list(lines[2])]
    return {
        "region": str((block or {}).get("region") or ""),
        "ids": ids,
        "template_tokens": template_tokens,
        "target_tokens": target_tokens,
    }


def prompt_source_from_configured_prompt(configured_prompt: str) -> str:
    return "runtime" if str(configured_prompt or "").strip() else "default_fallback"


def build_runtime_trace(
    *,
    region: str,
    configured_prompt: str,
    effective_prompt: str,
    user_prompt: str,
    attempt_logs: Optional[List[Dict[str, Any]]] = None,
    run_llm: bool = True,
    enabled: bool = True,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "implemented": True,
        "region": str(region or ""),
        "enabled": bool(enabled),
        "run_llm": bool(run_llm),
        "prompt_source": prompt_source_from_configured_prompt(configured_prompt),
        "system_prompt": str(effective_prompt or ""),
        "user_prompt": str(user_prompt or ""),
        "llm_attempts": list(attempt_logs or []),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def finalized_runtime_trace(
    runtime_trace: Mapping[str, Any],
    *,
    status: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = dict(runtime_trace or {})
    payload["status"] = str(status or "")
    payload["message"] = str(message or "")
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def block_with_runtime_trace(
    *,
    region: str,
    nums: List[str],
    templ: List[str],
    targ: List[str],
    meta: Mapping[str, Any],
    runtime_trace: Mapping[str, Any],
) -> Dict[str, Any]:
    out_meta = dict(meta or {})
    out_meta["llm_runtime_trace"] = dict(runtime_trace or {})
    return {
        "region": str(region or ""),
        "lines": [list(nums), list(templ), list(targ)],
        "meta": out_meta,
    }


def build_canonical_trace_fields(
    *,
    raw_status: str,
    attempt_logs: Optional[List[Dict[str, Any]]] = None,
    message: str = "",
    accepted_candidate: Optional[Dict[str, Any]] = None,
    output_changed: Optional[bool] = None,
) -> Dict[str, Any]:
    attempts = list(attempt_logs or [])
    fallback_used = _detect_fallback_used(str(raw_status or ""), attempts)
    final_status = _canonical_final_status(str(raw_status or ""), fallback_used)
    rejection_reasons = _extract_rejection_reasons(
        {"message": message},
        raw_status=str(raw_status or ""),
        final_status=final_status,
        attempt_logs=attempts,
    )
    payload: Dict[str, Any] = {
        "raw_status": str(raw_status or ""),
        "final_status": final_status,
        "fallback_used": fallback_used,
        "attempts": int(len(attempts)),
        "llm_attempted": bool(attempts),
        "rejection_reasons": rejection_reasons,
    }
    if accepted_candidate is not None:
        payload["accepted_candidate"] = accepted_candidate
    if output_changed is not None:
        payload["output_changed"] = bool(output_changed)
    return payload


def invalid_trace_preview(message: str, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "status": "invalid_block",
        "implemented": True,
        "enabled": False,
        "system_prompt": "",
        "user_prompt": "",
        "llm_attempts": [],
        "target_tokens": None,
        "message": str(message or "Invalid block."),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def parse_keep_current_status(status: str) -> Optional[str]:
    text = str(status or "").strip()
    if not text:
        return None
    lower = text.lower()
    if lower == "keep_current":
        return ""
    if lower.startswith("keep_current:"):
        return text.split(":", 1)[1].strip()
    return None


def extract_single_block_lines(
    block: Mapping[str, Any],
    *,
    norm_base: Callable[[Any], str],
) -> Optional[Tuple[List[str], List[str], List[str], Dict[str, Any]]]:
    lines = (block or {}).get("lines")
    if not (isinstance(lines, list) and len(lines) == 3):
        return None
    nums = list(lines[0])
    templ = list(lines[1])
    targ = [norm_base(x) for x in list(lines[2])]
    meta = (block or {}).get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return nums, templ, targ, meta


def trace_single_block_postcheck(
    *,
    block: Mapping[str, Any],
    runtime_values: Optional[Dict[str, Any]],
    run_llm: bool,
    load_cfg: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    prompt_field: str,
    default_prompt: str,
    norm_base: Callable[[Any], str],
    build_messages: Callable[[List[str], List[str], List[str], Dict[str, Any], Dict[str, Any]], List[Dict[str, str]]],
    propose_target: Callable[
        [List[str], List[str], List[str], Dict[str, Any], Dict[str, Any], Optional[List[Dict[str, Any]]]],
        Optional[List[str]],
    ],
    build_baseline_payload: Callable[[List[str], List[str], List[str], Dict[str, Any]], Dict[str, Any]],
    build_success_payload: Callable[[List[str], List[str], List[str], Dict[str, Any]], Optional[Dict[str, Any]]],
    no_candidate_message: str,
    success_message: str,
    invalid_message: str = "Invalid block: expected 3 lines.",
) -> Dict[str, Any]:
    cfg = load_cfg(runtime_values)
    ext = extract_single_block_lines(block, norm_base=norm_base)
    if ext is None:
        return invalid_trace_preview(invalid_message)

    nums, templ, targ, meta = ext
    configured_prompt = str(cfg.get(prompt_field) or "").strip()
    effective_prompt = configured_prompt or default_prompt
    cfg_eff = dict(cfg)
    cfg_eff[prompt_field] = effective_prompt
    messages = build_messages(nums, templ, targ, meta, cfg_eff)

    out: Dict[str, Any] = build_single_block_trace_preview(
        configured_prompt=configured_prompt,
        effective_prompt=effective_prompt,
        user_prompt=str(messages[1].get("content") or "") if len(messages) > 1 else "",
        postcheck_on=bool(cfg.get("postcheck_on")),
        run_llm=bool(run_llm),
        baseline_payload=build_baseline_payload(nums, templ, targ, meta),
    )

    if not bool(cfg.get("postcheck_on")):
        out["status"] = "postcheck_disabled"
        out["message"] = "LLM_POSTCHECK is disabled."
        return out
    if not run_llm:
        out["status"] = "preview_only"
        out["message"] = "Prompt preview only (LLM call skipped)."
        return out

    attempt_logs: List[Dict[str, Any]] = []
    candidate = _call_propose_target(
        propose_target=propose_target,
        nums=nums,
        templ=templ,
        targ=targ,
        meta=meta,
        cfg=cfg_eff,
        attempt_logs=attempt_logs,
        runtime_values=runtime_values,
    )
    success_payload = None
    if candidate is not None:
        success_payload = build_success_payload(nums, templ, candidate, meta)
    return finalize_single_block_trace_preview(
        out,
        candidate=candidate,
        attempt_logs=attempt_logs,
        success_payload=success_payload,
        no_candidate_message=no_candidate_message,
        success_message=success_message,
    )


def run_single_block_postcheck(
    *,
    block: Mapping[str, Any],
    runtime_values: Optional[Dict[str, Any]],
    load_cfg: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    log_runtime_cfg: Optional[Callable[[Dict[str, Any]], None]],
    prompt_field: str,
    default_prompt: str,
    norm_base: Callable[[Any], str],
    build_messages: Callable[[List[str], List[str], List[str], Dict[str, Any], Dict[str, Any]], List[Dict[str, str]]],
    propose_target: Callable[
        [List[str], List[str], List[str], Dict[str, Any], Dict[str, Any], Optional[List[Dict[str, Any]]]],
        Optional[List[str]],
    ],
    build_success_payload: Callable[[List[str], List[str], List[str], Dict[str, Any]], Optional[Dict[str, Any]]],
    no_candidate_message: str,
    success_message: str,
    apply_success_message: str,
    region_label: Optional[str] = None,
    invalid_message: str = "Invalid block: expected 3 lines.",
) -> Dict[str, Any]:
    cfg = load_cfg(runtime_values)
    if callable(log_runtime_cfg):
        log_runtime_cfg(cfg)
    if not bool(cfg.get("postcheck_on")):
        return dict(block)

    if not str(cfg.get(prompt_field) or "").strip():
        cfg = dict(cfg)
        cfg[prompt_field] = default_prompt

    ext = extract_single_block_lines(block, norm_base=norm_base)
    if ext is None:
        return dict(block)

    nums, templ, targ, meta = ext
    configured_prompt = str(cfg.get(prompt_field) or "").strip()
    effective_prompt = configured_prompt or default_prompt
    cfg_eff = dict(cfg)
    cfg_eff[prompt_field] = effective_prompt
    messages = build_messages(nums, templ, targ, meta, cfg_eff)

    attempt_logs: List[Dict[str, Any]] = []
    candidate = _call_propose_target(
        propose_target=propose_target,
        nums=nums,
        templ=templ,
        targ=targ,
        meta=meta,
        cfg=cfg_eff,
        attempt_logs=attempt_logs,
        runtime_values=runtime_values,
    )
    success_payload = None
    if candidate is not None:
        print(f"[POSTCHECK][{region_label or block.get('region', '')}] applied improved TARGET from LLM.")
        success_payload = build_success_payload(nums, templ, candidate, meta)
    else:
        print(f"[POSTCHECK][{region_label or block.get('region', '')}] no improved candidate; pass-through.")

    return apply_single_block_postcheck_result(
        region=str((block or {}).get("region") or region_label or ""),
        nums=nums,
        templ=templ,
        targ=targ,
        meta=meta,
        configured_prompt=configured_prompt,
        effective_prompt=effective_prompt,
        user_prompt=str(messages[1].get("content") or "") if len(messages) > 1 else "",
        attempt_logs=attempt_logs,
        candidate=candidate,
        no_candidate_message=no_candidate_message,
        success_message=apply_success_message,
        success_payload=success_payload,
    )


def build_single_block_trace_preview(
    *,
    configured_prompt: str,
    effective_prompt: str,
    user_prompt: str,
    postcheck_on: bool,
    run_llm: bool,
    baseline_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "implemented": True,
        "enabled": True,
        "prompt_source": prompt_source_from_configured_prompt(configured_prompt),
        "postcheck_on": bool(postcheck_on),
        "run_llm": bool(run_llm),
        "system_prompt": str(effective_prompt or ""),
        "user_prompt": str(user_prompt or ""),
        "llm_attempts": [],
        "target_tokens": None,
    }
    if isinstance(baseline_payload, dict):
        out.update(baseline_payload)
    return out


def finalize_single_block_trace_preview(
    out: Dict[str, Any],
    *,
    candidate: Optional[List[str]],
    attempt_logs: List[Dict[str, Any]],
    success_payload: Optional[Dict[str, Any]] = None,
    no_candidate_message: str,
    success_message: str,
) -> Dict[str, Any]:
    out = dict(out)
    out["llm_attempts"] = list(attempt_logs or [])
    if candidate is None:
        raw_status, message = _no_change_outcome(
            attempt_logs,
            default_message=str(no_candidate_message or "No valid improved target returned."),
        )
        out["status"] = "llm_keep_current" if raw_status == "keep_current" else "llm_failed"
        out["message"] = message
        return out
    out["status"] = "llm_success"
    out["target_tokens"] = list(candidate)
    out["message"] = str(success_message or "Prompt replay succeeded.")
    if isinstance(success_payload, dict):
        out.update(success_payload)
    return out


def build_joint_trace_preview(
    *,
    configured_prompt: str,
    effective_prompt: str,
    user_prompt: str,
    postcheck_on: bool,
    run_llm: bool,
    baseline_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "implemented": True,
        "enabled": True,
        "prompt_source": prompt_source_from_configured_prompt(configured_prompt),
        "postcheck_on": bool(postcheck_on),
        "run_llm": bool(run_llm),
        "system_prompt": str(effective_prompt or ""),
        "user_prompt": str(user_prompt or ""),
        "llm_attempts": [],
        "aa5_target_tokens": None,
        "aa3_target_tokens": None,
    }
    if isinstance(baseline_payload, dict):
        out.update(baseline_payload)
    return out


def finalize_joint_trace_preview(
    out: Dict[str, Any],
    *,
    aa5_candidate: Optional[List[str]],
    aa3_candidate: Optional[List[str]],
    attempt_logs: List[Dict[str, Any]],
    success_payload: Optional[Dict[str, Any]] = None,
    no_candidate_message: str,
    success_message: str,
) -> Dict[str, Any]:
    out = dict(out)
    out["llm_attempts"] = list(attempt_logs or [])
    if aa5_candidate is None or aa3_candidate is None:
        raw_status, message = _no_change_outcome(
            attempt_logs,
            default_message=str(no_candidate_message or "No valid improved targets returned."),
        )
        out["status"] = "llm_keep_current" if raw_status == "keep_current" else "llm_failed"
        out["message"] = message
        return out
    out["status"] = "llm_success"
    out["aa5_target_tokens"] = list(aa5_candidate)
    out["aa3_target_tokens"] = list(aa3_candidate)
    out["message"] = str(success_message or "Joint prompt replay succeeded.")
    if isinstance(success_payload, dict):
        out.update(success_payload)
    return out


def apply_single_block_postcheck_result(
    *,
    region: str,
    nums: List[str],
    templ: List[str],
    targ: List[str],
    meta: Mapping[str, Any],
    configured_prompt: str,
    effective_prompt: str,
    user_prompt: str,
    attempt_logs: List[Dict[str, Any]],
    candidate: Optional[List[str]],
    no_candidate_message: str,
    success_message: str,
    success_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime_trace = build_runtime_trace(
        region=region,
        configured_prompt=configured_prompt,
        effective_prompt=effective_prompt,
        user_prompt=user_prompt,
        attempt_logs=attempt_logs,
        run_llm=True,
        enabled=True,
    )
    if candidate is None:
        raw_status, message = _no_change_outcome(
            attempt_logs,
            default_message=str(no_candidate_message or ""),
        )
        return block_with_runtime_trace(
            region=region,
            nums=nums,
            templ=templ,
            targ=targ,
            meta=meta,
            runtime_trace=finalized_runtime_trace(
                runtime_trace,
                status=raw_status,
                message=message,
                extra=build_canonical_trace_fields(
                    raw_status=raw_status,
                    attempt_logs=attempt_logs,
                    message=message,
                    output_changed=False,
                ),
            ),
        )
    message = success_message
    accepted_snapshot = {
        "region": region,
        "ids": [str(x or "") for x in nums],
        "template_tokens": [_norm_base(x) for x in templ],
        "target_tokens": [_norm_base(x) for x in candidate],
    }
    return block_with_runtime_trace(
        region=region,
        nums=nums,
        templ=templ,
        targ=candidate,
        meta=meta,
        runtime_trace=finalized_runtime_trace(
            runtime_trace,
            status="accepted",
            message=message,
            extra={
                **build_canonical_trace_fields(
                    raw_status="accepted",
                    attempt_logs=attempt_logs,
                    message=message,
                    accepted_candidate=accepted_snapshot,
                    output_changed=True,
                ),
                **(success_payload or {}),
            },
        ),
    )


def apply_joint_postcheck_result(
    *,
    blocks: List[Dict[str, Any]],
    idx5: int,
    idx3: int,
    nums5: List[str],
    templ5: List[str],
    targ5: List[str],
    meta5: Mapping[str, Any],
    nums3: List[str],
    templ3: List[str],
    targ3: List[str],
    meta3: Mapping[str, Any],
    configured_prompt: str,
    effective_prompt: str,
    user_prompt: str,
    attempt_logs: List[Dict[str, Any]],
    aa5_candidate: Optional[List[str]],
    aa3_candidate: Optional[List[str]],
    no_candidate_message: str,
    success_message: str,
    success_payload: Optional[Dict[str, Any]] = None,
    region5: str = "Aminoacyl arm 5' end",
    region3: str = "Aminoacyl arm 3' end",
) -> List[Dict[str, Any]]:
    base_trace = build_runtime_trace(
        region="Aminoacyl joint",
        configured_prompt=configured_prompt,
        effective_prompt=effective_prompt,
        user_prompt=user_prompt,
        attempt_logs=attempt_logs,
        run_llm=True,
        enabled=True,
    )

    out = list(blocks)
    if aa5_candidate is None or aa3_candidate is None:
        raw_status, message = _no_change_outcome(
            attempt_logs,
            default_message=str(no_candidate_message or ""),
        )
        trace5 = finalized_runtime_trace(
            dict(base_trace, region=region5),
            status=raw_status,
            message=message,
            extra=build_canonical_trace_fields(
                raw_status=raw_status,
                attempt_logs=attempt_logs,
                message=message,
                output_changed=False,
            ),
        )
        trace3 = finalized_runtime_trace(
            dict(base_trace, region=region3),
            status=raw_status,
            message=message,
            extra=build_canonical_trace_fields(
                raw_status=raw_status,
                attempt_logs=attempt_logs,
                message=message,
                output_changed=False,
            ),
        )
        out[idx5] = block_with_runtime_trace(
            region=region5,
            nums=nums5,
            templ=templ5,
            targ=targ5,
            meta=meta5,
            runtime_trace=trace5,
        )
        out[idx3] = block_with_runtime_trace(
            region=region3,
            nums=nums3,
            templ=templ3,
            targ=targ3,
            meta=meta3,
            runtime_trace=trace3,
        )
        return out

    message = success_message
    trace5 = finalized_runtime_trace(
        dict(base_trace, region=region5),
        status="accepted",
        message=message,
        extra={
            **build_canonical_trace_fields(
                raw_status="accepted",
                attempt_logs=attempt_logs,
                message=message,
                accepted_candidate={
                    "region": region5,
                    "ids": [str(x or "") for x in nums5],
                    "template_tokens": [_norm_base(x) for x in templ5],
                    "target_tokens": [_norm_base(x) for x in aa5_candidate],
                },
                output_changed=True,
            ),
            **(success_payload or {}),
        },
    )
    trace3 = finalized_runtime_trace(
        dict(base_trace, region=region3),
        status="accepted",
        message=message,
        extra={
            **build_canonical_trace_fields(
                raw_status="accepted",
                attempt_logs=attempt_logs,
                message=message,
                accepted_candidate={
                    "region": region3,
                    "ids": [str(x or "") for x in nums3],
                    "template_tokens": [_norm_base(x) for x in templ3],
                    "target_tokens": [_norm_base(x) for x in aa3_candidate],
                },
                output_changed=True,
            ),
            **(success_payload or {}),
        },
    )
    out[idx5] = block_with_runtime_trace(
        region=region5,
        nums=nums5,
        templ=templ5,
        targ=aa5_candidate,
        meta=meta5,
        runtime_trace=trace5,
    )
    out[idx3] = block_with_runtime_trace(
        region=region3,
        nums=nums3,
        templ=templ3,
        targ=aa3_candidate,
        meta=meta3,
        runtime_trace=trace3,
    )
    return out


def run_standard_target_candidate_loop(
    *,
    cfg: Mapping[str, Any],
    runtime_values: Optional[Dict[str, Any]],
    messages: List[Dict[str, str]],
    expected_len: int,
    attempt_logs: Optional[List[Dict[str, Any]]],
    http_post_json: Callable[[str, Dict[str, Any], Mapping[str, Any]], Tuple[bool, str]],
    parse_candidate: Callable[[str, int], Tuple[Optional[List[str]], str]],
    evaluate_candidate: Callable[[List[str]], Dict[str, Any]],
    parse_feedback_builder: Callable[[str], str],
    log_trunc: Callable[[str, int], str],
    fallback_model_resolver: Callable[[Optional[Dict[str, Any]]], str],
) -> Optional[List[str]]:
    start = time.monotonic()
    attempts = 0

    def _build_payload(model_name: str, request_messages: List[Dict[str, str]]) -> Tuple[str, Dict[str, Any]]:
        return build_chat_request(
            cfg=cfg,
            messages=request_messages,
            model_name=model_name,
        )

    while attempts <= int(cfg["max_retries"]):
        attempts += 1
        result = run_target_candidate_attempt(
            cfg=cfg,
            messages=messages,
            expected_len=expected_len,
            attempt_label=attempts,
            attempt_logs=attempt_logs,
            http_post_json=http_post_json,
            parse_candidate=parse_candidate,
            evaluate_candidate=evaluate_candidate,
            parse_feedback_builder=parse_feedback_builder,
            log_trunc=log_trunc,
        )
        if bool(result.get("accepted")):
            return result.get("candidate")
        if bool(result.get("terminal")) or str(result.get("status") or "") == "keep_current":
            return None
        if str(result.get("status") or "") == "http_failed":
            if (time.monotonic() - start) >= float(cfg["hard_budget_s"]):
                return None
            delay = min(float(cfg["backoff_base_s"]) * (2 ** (attempts - 1)), 2.0)
            time.sleep(delay)
        continue

    if str(cfg.get("mode") or "") == "openai" and "reasoner" in str(cfg.get("model_name") or "").lower():
        fb_model = provider_fallback_model(
            cfg,
            fallback_model_resolver(runtime_values),
        )
        result = run_target_candidate_attempt(
            cfg=cfg,
            messages=messages,
            expected_len=expected_len,
            attempt_label="fallback_chat",
            attempt_logs=attempt_logs,
            http_post_json=http_post_json,
            parse_candidate=parse_candidate,
            evaluate_candidate=evaluate_candidate,
            parse_feedback_builder=parse_feedback_builder,
            log_trunc=log_trunc,
            model_name=fb_model,
            append_feedback=False,
        )
        if bool(result.get("accepted")):
            return result.get("candidate")

    return None


def build_chat_request(
    *,
    cfg: Mapping[str, Any],
    messages: List[Dict[str, str]],
    model_name: str,
) -> Tuple[str, Dict[str, Any]]:
    if str(cfg.get("mode") or "") == "ollama":
        return (
            f"{cfg['base_url']}/api/chat",
            {
                "model": model_name,
                "messages": messages,
                "options": {"temperature": 0},
                "format": "json",
                "stream": bool(cfg["streaming"]),
            },
        )
    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": int(cfg["max_tokens"]),
        "stream": bool(cfg["streaming"]),
        "response_format": {"type": "json_object"},
    }
    if "reasoner" in str(model_name or "").lower():
        payload["reasoning_effort"] = "low"
    apply_openai_provider_extras(payload, cfg, model_name=model_name)
    return openai_chat_completions_url(str(cfg["base_url"])), payload


def provider_fallback_model(
    cfg: Mapping[str, Any],
    deepseek_fallback: str,
) -> str:
    current_model = str(cfg.get("model_name") or "").strip()
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider == "deepseek" and "reasoner" in current_model.lower():
        return str(deepseek_fallback or current_model).strip()
    return current_model


def run_target_candidate_attempt(
    *,
    cfg: Mapping[str, Any],
    messages: List[Dict[str, str]],
    expected_len: int,
    attempt_label: Any,
    attempt_logs: Optional[List[Dict[str, Any]]],
    http_post_json: Callable[[str, Dict[str, Any], Mapping[str, Any]], Tuple[bool, str]],
    parse_candidate: Callable[[str, int], Tuple[Optional[List[str]], str]],
    evaluate_candidate: Callable[[List[str]], Dict[str, Any]],
    parse_feedback_builder: Callable[[str], str],
    log_trunc: Callable[[str, int], str],
    model_name: Optional[str] = None,
    append_feedback: bool = True,
) -> Dict[str, Any]:
    log: Dict[str, Any] = {"attempt": attempt_label}
    url, payload = build_chat_request(
        cfg=cfg,
        messages=messages,
        model_name=str(model_name or cfg.get("model_name") or ""),
    )
    ok, raw = http_post_json(url, payload, cfg)
    if not ok:
        log["status"] = "http_failed"
        if attempt_logs is not None:
            attempt_logs.append(log)
        return {"accepted": False, "status": "http_failed", "candidate": None}

    log["raw_response"] = log_trunc(raw or "", 1200)
    candidate, parse_status = parse_candidate(raw, expected_len)
    if candidate is None:
        keep_current_reason = parse_keep_current_status(parse_status)
        if keep_current_reason is not None:
            log["status"] = "keep_current"
            if keep_current_reason:
                log["message"] = keep_current_reason
            if attempt_logs is not None:
                attempt_logs.append(log)
            return {"accepted": False, "status": "keep_current", "candidate": None, "terminal": True}
        log["status"] = parse_status
        if attempt_logs is not None:
            attempt_logs.append(log)
        if append_feedback:
            messages.append({"role": "user", "content": parse_feedback_builder(parse_status)})
        return {"accepted": False, "status": parse_status, "candidate": None}

    decision = dict(evaluate_candidate(candidate) or {})
    log.update(dict(decision.get("log_extra") or {}))
    log["status"] = str(decision.get("status") or "rejected")
    if attempt_logs is not None:
        attempt_logs.append(log)
    if bool(decision.get("accepted")):
        return {"accepted": True, "status": log["status"], "candidate": candidate}

    feedback = str(decision.get("feedback") or "").strip()
    if append_feedback and feedback:
        messages.append({"role": "user", "content": feedback})
    return {"accepted": False, "status": log["status"], "candidate": candidate}


def _no_change_outcome(
    attempt_logs: List[Dict[str, Any]],
    *,
    default_message: str,
) -> Tuple[str, str]:
    for entry in reversed(list(attempt_logs or [])):
        if str(entry.get("status") or "").strip().lower() != "keep_current":
            continue
        message = str(entry.get("message") or "").strip()
        if not message:
            message = "Model kept the current target because no strictly better edit was available."
        return "keep_current", message
    return "no_improved_candidate", str(default_message or "No valid improved target returned.")


def _call_propose_target(
    *,
    propose_target: Callable[..., Optional[List[str]]],
    nums: List[str],
    templ: List[str],
    targ: List[str],
    meta: Dict[str, Any],
    cfg: Dict[str, Any],
    attempt_logs: Optional[List[Dict[str, Any]]],
    runtime_values: Optional[Dict[str, Any]],
) -> Optional[List[str]]:
    try:
        params = inspect.signature(propose_target).parameters
    except (TypeError, ValueError):
        params = {}
    if "runtime_values" in params:
        return propose_target(
            nums,
            templ,
            targ,
            meta,
            cfg,
            attempt_logs,
            runtime_values=runtime_values,
        )
    return propose_target(nums, templ, targ, meta, cfg, attempt_logs)


def _output_changed(input_block: Dict[str, Any], output_block: Dict[str, Any]) -> bool:
    return list(input_block.get("target_tokens") or []) != list(output_block.get("target_tokens") or [])


def _extract_attempt_logs(raw_trace: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw_attempts = raw_trace.get("llm_attempts")
    if not isinstance(raw_attempts, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in raw_attempts:
        if isinstance(entry, dict):
            out.append(dict(entry))
    return out


def _accepted_attempt(attempt_logs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for entry in reversed(attempt_logs):
        if str(entry.get("status") or "").strip().lower() == "accepted":
            return entry
    return None


def _detect_fallback_used(
    raw_status: str,
    attempt_logs: List[Dict[str, Any]],
) -> Optional[str]:
    if raw_status in _STATUS_PASS_THROUGH_HANDLER_ERROR:
        return "handler_error"
    if raw_status in {"fallback_deterministic", "deterministic_structure_rule"}:
        return "deterministic"

    accepted_attempt = _accepted_attempt(attempt_logs)
    if accepted_attempt is None:
        return None

    attempt_name = str(accepted_attempt.get("attempt") or "").strip().lower()
    if attempt_name in {"fallback_chat", "hard_feedback_fallback_chat"}:
        return "fallback_chat"
    if attempt_name == "deterministic_search":
        return "deterministic"
    return None


def _canonical_final_status(
    raw_status: str,
    fallback_used: Optional[str],
) -> str:
    if raw_status == "accepted":
        if fallback_used == "fallback_chat":
            return "accepted_fallback_chat"
        if fallback_used == "deterministic":
            return "accepted_deterministic"
        return "accepted"
    if raw_status in {"fallback_deterministic", "deterministic_structure_rule"}:
        return "accepted_deterministic"
    if raw_status in _STATUS_PASS_THROUGH_HANDLER_ERROR:
        return "pass_through_handler_error"
    if raw_status in _STATUS_PASS_THROUGH_DISABLED:
        return "pass_through_disabled"
    if raw_status in _STATUS_PASS_THROUGH_NO_IMPROVEMENT:
        return "pass_through_no_improvement"
    if raw_status.startswith("skipped_"):
        return "pass_through_disabled"
    return "pass_through_no_improvement"


def _append_reason(reasons: List[str], value: str) -> None:
    text = str(value or "").strip()
    if not text:
        return
    if text not in reasons:
        reasons.append(text)


def _extract_rejection_reasons(
    raw_trace: Mapping[str, Any],
    *,
    raw_status: str,
    final_status: str,
    attempt_logs: List[Dict[str, Any]],
) -> List[str]:
    reasons: List[str] = []

    for entry in attempt_logs:
        status = str(entry.get("status") or "").strip()
        if status.lower() in _ATTEMPT_NON_REJECTION_STATUSES:
            continue
        reason = status
        feedback = str(entry.get("feedback") or "").strip()
        message = str(entry.get("message") or "").strip()
        error = str(entry.get("error") or "").strip()
        violations = entry.get("violations")
        if feedback:
            reason = f"{reason}: {feedback}"
        elif message:
            reason = f"{reason}: {message}"
        elif error:
            reason = f"{reason}: {error}"
        elif isinstance(violations, list) and violations:
            reason = f"{reason}: {'; '.join(str(x) for x in violations[:6])}"
        _append_reason(reasons, reason)

    message = str(raw_trace.get("message") or "").strip()
    if raw_status == "keep_current":
        return reasons
    if final_status.startswith("pass_through") and message:
        _append_reason(reasons, message)
    elif raw_status in {"invalid_block", "missing_blocks"} and message:
        _append_reason(reasons, message)

    return reasons


def normalize_runtime_trace(
    raw_trace: Optional[Mapping[str, Any]],
    *,
    region: str,
    input_block: Optional[Mapping[str, Any]],
    output_block: Optional[Mapping[str, Any]],
    run_llm: bool,
    enabled: bool,
) -> Dict[str, Any]:
    base = dict(raw_trace or {})
    attempt_logs = _extract_attempt_logs(base)
    raw_status = str(base.get("raw_status") or base.get("status") or "").strip()
    fallback_used = base.get("fallback_used")
    if not isinstance(fallback_used, str) or not fallback_used.strip():
        fallback_used = _detect_fallback_used(raw_status, attempt_logs)
    final_status = str(base.get("final_status") or "").strip() or _canonical_final_status(raw_status, fallback_used)

    input_snapshot = snapshot_block(input_block)
    output_snapshot = snapshot_block(output_block)
    changed = base.get("output_changed")
    if not isinstance(changed, bool):
        changed = _output_changed(input_snapshot, output_snapshot)

    llm_attempted = base.get("llm_attempted")
    if not isinstance(llm_attempted, bool):
        llm_attempted = bool(attempt_logs)
    accepted_candidate = base.get("accepted_candidate")
    if not isinstance(accepted_candidate, dict):
        accepted_candidate = output_snapshot if final_status.startswith("accepted") else None
    rejection_reasons = base.get("rejection_reasons")
    if not isinstance(rejection_reasons, list):
        rejection_reasons = _extract_rejection_reasons(
            base,
            raw_status=raw_status,
            final_status=final_status,
            attempt_logs=attempt_logs,
        )

    base.setdefault("implemented", True)
    base["region"] = region
    base["run_llm"] = bool(base.get("run_llm", run_llm))
    base["enabled"] = bool(base.get("enabled", enabled))
    base["llm_attempts"] = attempt_logs
    base["trace_schema_version"] = 2
    base["raw_status"] = raw_status
    base["final_status"] = final_status
    base["attempts"] = int(base.get("attempts")) if isinstance(base.get("attempts"), int) else int(len(attempt_logs))
    base["llm_attempted"] = llm_attempted
    base["fallback_used"] = fallback_used
    base["rejection_reasons"] = rejection_reasons
    base["input_block"] = input_snapshot
    base["accepted_candidate"] = accepted_candidate
    base["output_changed"] = changed
    return base
