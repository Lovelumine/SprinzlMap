# -*- coding: utf-8 -*-
# ==============================================
# File: pythonscript/llm_postcheck.py
# ----------------------------------------------
# Dispatcher for 6 standard regions.
# Each region lives in its own file under pythonscript/postcheck/.
# Variable loop has a full implementation; others are placeholders for now.
# ==============================================

from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import as_completed
from copy import deepcopy
import logging
import inspect

# 标准 6 区段
regions = [
    "Aminoacyl arm 5' end",
    "D loop + D stem",
    "Anticodon loop + Anticodon stem",
    "Variable loop",
    "T loop + T stem",
    "Aminoacyl arm 3' end",
]

# 每个区段都有独立文件与入口
from .postcheck import (
    postcheck_aminoacyl_5,
    postcheck_d_region,
    postcheck_anticodon,
    postcheck_variable_loop,
    postcheck_t_region,
    postcheck_aminoacyl_3,
)
from .postcheck.aminoacyl_joint import postcheck_aminoacyl_joint
from .postcheck.variable_loop import trace_variable_loop_block
from .postcheck.d_region import trace_d_region_block
from .postcheck.anticodon import trace_anticodon_block
from .postcheck.t_region import trace_t_region_block
from .postcheck.aminoacyl_joint import trace_aminoacyl_joint_blocks
from .postcheck.variable_t_boundary_repair import repair_variable_t_boundary_shift
from .postcheck.common import normalize_runtime_trace
from .postcheck.common import build_canonical_trace_fields

_REGION_HANDLERS = {
    "Aminoacyl arm 5' end":            postcheck_aminoacyl_5,
    "D loop + D stem":                 postcheck_d_region,
    "Anticodon loop + Anticodon stem": postcheck_anticodon,
    "Variable loop":                   postcheck_variable_loop,
    "T loop + T stem":                 postcheck_t_region,
    "Aminoacyl arm 3' end":            postcheck_aminoacyl_3,
}

_REGION_PROMPT_KEYS = {
    "Aminoacyl arm 5' end": "LLM_AA5_SYSTEM_PROMPT",
    "D loop + D stem": "LLM_D_SYSTEM_PROMPT",
    "Anticodon loop + Anticodon stem": "LLM_ANTICODON_SYSTEM_PROMPT",
    "Variable loop": "LLM_VARLOOP_SYSTEM_PROMPT",
    "T loop + T stem": "LLM_T_SYSTEM_PROMPT",
    "Aminoacyl arm 3' end": "LLM_AA3_SYSTEM_PROMPT",
}

_REGION_TRACE_HANDLERS = {
    "D loop + D stem": trace_d_region_block,
    "Anticodon loop + Anticodon stem": trace_anticodon_block,
    "Variable loop": trace_variable_loop_block,
    "T loop + T stem": trace_t_region_block,
}

_DEFAULT_PROMPT_FALLBACK_REGIONS = {
    "Aminoacyl arm 5' end",
    "D loop + D stem",
    "Anticodon loop + Anticodon stem",
    "T loop + T stem",
    "Aminoacyl arm 3' end",
}

from backend.infra.llm.llm_dispatcher import submit_llm_task, get_dispatcher_metrics
from .runtime_helpers import (
    load_runtime_values,
    merge_runtime_values,
    runtime_bool,
    runtime_int,
    runtime_prompt_value,
    runtime_str,
)


logger = logging.getLogger(__name__)


def _finalize_postcheck_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return repair_variable_t_boundary_shift(list(blocks))


def _normalize_postcheck_traces(
    blocks: List[Dict[str, Any]],
    *,
    input_blocks: List[Dict[str, Any]],
    run_llm: bool,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = list(blocks)
    for idx, blk in enumerate(out):
        if not isinstance(blk, dict):
            continue
        meta = _ensure_block_meta(blk)
        raw_trace = meta.get("llm_runtime_trace")
        region = str(blk.get("region") or "")
        enabled = True
        if isinstance(raw_trace, dict):
            enabled = bool(raw_trace.get("enabled", True))
        input_block = input_blocks[idx] if idx < len(input_blocks) else None
        meta["llm_runtime_trace"] = normalize_runtime_trace(
            raw_trace if isinstance(raw_trace, dict) else {},
            region=region,
            input_block=input_block,
            output_block=blk,
            run_llm=run_llm,
            enabled=enabled,
        )
    return out


def _finalize_and_normalize_postcheck_blocks(
    blocks: List[Dict[str, Any]],
    *,
    input_blocks: List[Dict[str, Any]],
    run_llm: bool,
) -> List[Dict[str, Any]]:
    finalized = _finalize_postcheck_blocks(blocks)
    return _normalize_postcheck_traces(finalized, input_blocks=input_blocks, run_llm=run_llm)


def _to_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _handler_accepts_runtime_values(handler: Any) -> bool:
    try:
        sig = inspect.signature(handler)
    except Exception:
        return False
    return "runtime_values" in sig.parameters


def _invoke_handler(handler: Any, blk: Dict[str, Any], runtime_values: Dict[str, Any]) -> Dict[str, Any]:
    if _handler_accepts_runtime_values(handler):
        return handler(blk, runtime_values=runtime_values)
    return handler(blk)


def _region_prompt_enabled(region: str, runtime_values: Dict[str, Any]) -> bool:
    if region in _DEFAULT_PROMPT_FALLBACK_REGIONS:
        return True
    key = _REGION_PROMPT_KEYS.get(region)
    if not key:
        return False
    return bool(runtime_prompt_value(key, runtime_values))


def _parse_region_gate(raw: Any) -> set[str]:
    if raw in (None, ""):
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(item or "").strip() for item in raw if str(item or "").strip()}
    return {part.strip() for part in str(raw).split(",") if part.strip()}


def _region_allowed_by_agent(region: str, runtime_values: Dict[str, Any]) -> tuple[bool, str]:
    only_regions = _parse_region_gate(runtime_values.get("AGENT_POSTCHECK_ONLY_REGIONS"))
    skip_regions = _parse_region_gate(runtime_values.get("AGENT_POSTCHECK_SKIP_REGIONS"))
    if only_regions and region not in only_regions:
        return False, "not_in_only_regions"
    if region in skip_regions:
        return False, "in_skip_regions"
    return True, ""


def _postcheck_max_workers(runtime_values: Dict[str, Any]) -> int:
    return runtime_int("LLM_POSTCHECK_MAX_WORKERS", 6, 1, 8, runtime_values=runtime_values)


def _postcheck_scheduler(runtime_values: Dict[str, Any]) -> str:
    mode = runtime_str("LLM_POSTCHECK_SCHEDULER", "all", runtime_values=runtime_values).lower()
    if mode in ("cap", "limited"):
        return "cap"
    return "all"


def _find_aminoacyl_indices(blocks: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    idx5: Optional[int] = None
    idx3: Optional[int] = None
    for i, blk in enumerate(blocks):
        region = str((blk or {}).get("region") or "")
        if region == "Aminoacyl arm 5' end" and idx5 is None:
            idx5 = i
        elif region == "Aminoacyl arm 3' end" and idx3 is None:
            idx3 = i
    return idx5, idx3


def _norm_base_token(raw: Any) -> str:
    s = str(raw or "").strip().upper().replace("T", "U")
    return s if s in {"A", "U", "G", "C", "N", "-"} else "-"


def _ensure_block_meta(blk: Dict[str, Any]) -> Dict[str, Any]:
    meta = (blk or {}).get("meta")
    if isinstance(meta, dict):
        return meta
    meta = {}
    blk["meta"] = meta
    return meta


def _attach_runtime_trace(
    blk: Dict[str, Any],
    *,
    status: str,
    message: str,
    run_llm: bool,
    enabled: bool,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not isinstance(blk, dict):
        return
    meta = _ensure_block_meta(blk)
    payload: Dict[str, Any] = {
        "implemented": True,
        "region": str((blk or {}).get("region") or ""),
        "status": str(status or ""),
        "message": str(message or ""),
        "run_llm": bool(run_llm),
        "enabled": bool(enabled),
        "llm_attempts": [],
    }
    payload.update(
        build_canonical_trace_fields(
            raw_status=str(status or ""),
            attempt_logs=[],
            message=str(message or ""),
            output_changed=False,
        )
    )
    if isinstance(extra, dict):
        payload.update(extra)
    meta["llm_runtime_trace"] = payload


def _block_template_target_full_match(blk: Dict[str, Any]) -> bool:
    lines = (blk or {}).get("lines")
    if not (isinstance(lines, list) and len(lines) == 3):
        return False
    templ = list(lines[1] or [])
    targ = list(lines[2] or [])
    if len(templ) != len(targ) or not templ:
        return False
    for tb, qb in zip(templ, targ):
        if _norm_base_token(tb) != _norm_base_token(qb):
            return False
    return True


def _single_region_prompt_preview(
    region: str,
    blk: Dict[str, Any],
    runtime_values: Dict[str, Any],
) -> Dict[str, Any]:
    tracer = _REGION_TRACE_HANDLERS.get(str(region or ""))
    if tracer is None:
        return {"system_prompt": "", "user_prompt": ""}
    try:
        traced = tracer(blk, runtime_values=runtime_values, run_llm=False)
    except Exception:
        return {"system_prompt": "", "user_prompt": ""}
    if not isinstance(traced, dict):
        return {"system_prompt": "", "user_prompt": ""}
    return {
        "system_prompt": str(traced.get("system_prompt") or ""),
        "user_prompt": str(traced.get("user_prompt") or ""),
        "prompt_source": str(traced.get("prompt_source") or ""),
    }


def _aminoacyl_prompt_preview(
    blocks_snapshot: List[Dict[str, Any]],
    runtime_values: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        traced = trace_aminoacyl_joint_blocks(
            blocks_snapshot,
            runtime_values=runtime_values,
            run_llm=False,
        )
    except Exception:
        return {"system_prompt": "", "user_prompt": ""}
    if not isinstance(traced, dict):
        return {"system_prompt": "", "user_prompt": ""}
    return {
        "system_prompt": str(traced.get("system_prompt") or ""),
        "user_prompt": str(traced.get("user_prompt") or ""),
        "prompt_source": str(traced.get("prompt_source") or ""),
    }

def postcheck_alignment(
    blocks: List[Dict[str, Any]],
    use_llm: bool = True,
    runtime_values_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    对每个 block 按 region 分发到对应后校对函数。
    - use_llm=True（默认）：保持原有行为，分发到各区段后校对（其中可能调用大模型）。
    - use_llm=False：完全跳过后校对（包括一切大模型相关步骤），直接原样返回 blocks。

    说明：
    之所以在此做“硬旁路（bypass）”，是因为各区段实现的函数签名目前只接收 block，
    且是否启用 LLM 的开关通常在各文件内部用环境变量常量化读取，运行期很难
    通过参数动态传递进去并生效。为了保证行为可预测，这里选择在调度层面直接跳过。
    """
    input_blocks_snapshot = deepcopy(list(blocks))

    if not use_llm:
        # 直接返回：不进入任何区段的 postcheck，等价于“不要大模型”
        out = list(blocks)
        for blk in out:
            _attach_runtime_trace(
                blk,
                status="skipped_use_llm_off",
                message="Request disabled LLM postcheck (use_llm=false).",
                run_llm=False,
                enabled=False,
            )
        return _normalize_postcheck_traces(out, input_blocks=input_blocks_snapshot, run_llm=False)

    runtime_values = merge_runtime_values(load_runtime_values(), runtime_values_override)
    if not runtime_bool(
        "AGENT_ENABLE_LLM_POSTCHECK",
        True,
        runtime_values=runtime_values,
    ):
        out = list(blocks)
        for blk in out:
            _attach_runtime_trace(
                blk,
                status="skipped_agent_llm_postcheck_disabled",
                message="Agent component ablation disabled regional LLM postcheck.",
                run_llm=False,
                enabled=False,
            )
        return _normalize_postcheck_traces(
            out,
            input_blocks=input_blocks_snapshot,
            run_llm=False,
        )
    if not _to_bool(runtime_values.get("LLM_POSTCHECK"), True):
        out = list(blocks)
        for blk in out:
            _attach_runtime_trace(
                blk,
                status="skipped_postcheck_disabled",
                message="LLM_POSTCHECK is disabled.",
                run_llm=False,
                enabled=False,
            )
        return _normalize_postcheck_traces(out, input_blocks=input_blocks_snapshot, run_llm=False)

    workers = _postcheck_max_workers(runtime_values)
    scheduler = _postcheck_scheduler(runtime_values)
    out: List[Dict[str, Any]] = list(blocks)
    tasks: List[Dict[str, Any]] = []
    idx5, idx3 = _find_aminoacyl_indices(out)
    use_aminoacyl_joint = idx5 is not None and idx3 is not None

    for idx, blk in enumerate(out):
        region = (blk or {}).get("region", "")
        if use_aminoacyl_joint and region in ("Aminoacyl arm 5' end", "Aminoacyl arm 3' end"):
            continue
        allowed, gate_reason = _region_allowed_by_agent(str(region), runtime_values)
        if not allowed:
            _attach_runtime_trace(
                blk,
                status="skipped_agent_region_gate",
                message=f"Region skipped by agent gate: {gate_reason}.",
                run_llm=False,
                enabled=False,
                extra={"skip_reason": gate_reason},
            )
            continue
        handler = _REGION_HANDLERS.get(region)
        if handler is None:
            _attach_runtime_trace(
                blk,
                status="skipped_no_handler",
                message="No postcheck handler for this region.",
                run_llm=False,
                enabled=False,
            )
            continue
        if not _region_prompt_enabled(region, runtime_values):
            _attach_runtime_trace(
                blk,
                status="skipped_prompt_disabled",
                message="Prompt is disabled/empty for this region.",
                run_llm=False,
                enabled=False,
            )
            continue
        if _block_template_target_full_match(blk):
            prompt_preview = _single_region_prompt_preview(str(region), blk, runtime_values)
            _attach_runtime_trace(
                blk,
                status="skipped_template_all_match",
                message="Target fully matches template in this region; LLM skipped.",
                run_llm=False,
                enabled=True,
                extra={"skip_reason": "template_all_match", **prompt_preview},
            )
            continue
        tasks.append({
            "kind": "single",
            "idx": idx,
            "blk": blk,
            "region": str(region),
            "handler": handler,
        })

    if use_aminoacyl_joint:
        blk5 = out[idx5] if isinstance(idx5, int) and 0 <= idx5 < len(out) else None
        blk3 = out[idx3] if isinstance(idx3, int) and 0 <= idx3 < len(out) else None
        if isinstance(blk5, dict) and isinstance(blk3, dict):
            allow5, gate_reason5 = _region_allowed_by_agent("Aminoacyl arm 5' end", runtime_values)
            allow3, gate_reason3 = _region_allowed_by_agent("Aminoacyl arm 3' end", runtime_values)
            if not (allow5 and allow3):
                if not allow5:
                    _attach_runtime_trace(
                        blk5,
                        status="skipped_agent_region_gate",
                        message=f"AA5 skipped by agent gate: {gate_reason5}.",
                        run_llm=False,
                        enabled=False,
                        extra={"skip_reason": gate_reason5},
                    )
                if not allow3:
                    _attach_runtime_trace(
                        blk3,
                        status="skipped_agent_region_gate",
                        message=f"AA3 skipped by agent gate: {gate_reason3}.",
                        run_llm=False,
                        enabled=False,
                        extra={"skip_reason": gate_reason3},
                    )
            elif _block_template_target_full_match(blk5) and _block_template_target_full_match(blk3):
                joint_prompt_preview = _aminoacyl_prompt_preview(out, runtime_values)
                _attach_runtime_trace(
                    blk5,
                    status="skipped_template_all_match",
                    message="AA5 fully matches template; aminoacyl joint LLM skipped.",
                    run_llm=False,
                    enabled=True,
                    extra={"skip_reason": "template_all_match", **joint_prompt_preview},
                )
                _attach_runtime_trace(
                    blk3,
                    status="skipped_template_all_match",
                    message="AA3 fully matches template; aminoacyl joint LLM skipped.",
                    run_llm=False,
                    enabled=True,
                    extra={"skip_reason": "template_all_match", **joint_prompt_preview},
                )
            else:
                tasks.append({
                    "kind": "aminoacyl_joint",
                    "idx5": idx5,
                    "idx3": idx3,
                    "region": "Aminoacyl joint",
                    "blocks_snapshot": list(out),
                })

    if not tasks:
        return _finalize_and_normalize_postcheck_blocks(
            out,
            input_blocks=input_blocks_snapshot,
            run_llm=use_llm,
        )

    def _run_task(task: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(task.get("kind") or "")
        if kind == "aminoacyl_joint":
            blocks_snapshot = list(task.get("blocks_snapshot") or [])
            updated = postcheck_aminoacyl_joint(blocks_snapshot, runtime_values=runtime_values)
            return {
                "kind": "aminoacyl_joint",
                "idx5": task.get("idx5"),
                "idx3": task.get("idx3"),
                "updated_blocks": updated,
                "region": task.get("region"),
            }
        idx = int(task["idx"])
        blk = task["blk"]
        region = str(task["region"])
        handler = task["handler"]
        updated = _invoke_handler(handler, blk, runtime_values)
        return {
            "kind": "single",
            "idx": idx,
            "updated_block": updated,
            "region": region,
        }

    def _merge_task_result(result: Dict[str, Any]) -> None:
        kind = str(result.get("kind") or "")
        if kind == "aminoacyl_joint":
            idx5_local = result.get("idx5")
            idx3_local = result.get("idx3")
            updated_blocks = result.get("updated_blocks")
            if isinstance(updated_blocks, list):
                if isinstance(idx5_local, int) and 0 <= idx5_local < len(out) and idx5_local < len(updated_blocks):
                    out[idx5_local] = updated_blocks[idx5_local]
                if isinstance(idx3_local, int) and 0 <= idx3_local < len(out) and idx3_local < len(updated_blocks):
                    out[idx3_local] = updated_blocks[idx3_local]
            return
        idx_local = int(result["idx"])
        out[idx_local] = result["updated_block"]

    if len(tasks) <= 1:
        for task in tasks:
            region = str(task.get("region") or "")
            try:
                merged = _run_task(task)
                _merge_task_result(merged)
            except Exception as e:
                logger.warning("Postcheck handler for region '%s' raised %r. Passing through.", region, e)
                if str(task.get("kind") or "") == "single":
                    out[int(task["idx"])] = task["blk"]
                    _attach_runtime_trace(
                        out[int(task["idx"])],
                        status="handler_error_pass_through",
                        message=f"Handler raised error and result was passed through: {repr(e)}",
                        run_llm=True,
                        enabled=True,
                    )
                else:
                    idx5_local = task.get("idx5")
                    idx3_local = task.get("idx3")
                    if isinstance(idx5_local, int) and 0 <= idx5_local < len(out):
                        _attach_runtime_trace(
                            out[idx5_local],
                            status="handler_error_pass_through",
                            message=f"Aminoacyl joint handler error: {repr(e)}",
                            run_llm=True,
                            enabled=True,
                        )
                    if isinstance(idx3_local, int) and 0 <= idx3_local < len(out):
                        _attach_runtime_trace(
                            out[idx3_local],
                            status="handler_error_pass_through",
                            message=f"Aminoacyl joint handler error: {repr(e)}",
                            run_llm=True,
                            enabled=True,
                        )
        return _finalize_and_normalize_postcheck_blocks(
            out,
            input_blocks=input_blocks_snapshot,
            run_llm=use_llm,
        )

    max_workers = len(tasks) if scheduler == "all" else min(workers, len(tasks))
    if max_workers <= 1:
        for task in tasks:
            region = str(task.get("region") or "")
            try:
                merged = _run_task(task)
                _merge_task_result(merged)
            except Exception as e:
                logger.warning("Postcheck handler for region '%s' raised %r. Passing through.", region, e)
                if str(task.get("kind") or "") == "single":
                    out[int(task["idx"])] = task["blk"]
                    _attach_runtime_trace(
                        out[int(task["idx"])],
                        status="handler_error_pass_through",
                        message=f"Handler raised error and result was passed through: {repr(e)}",
                        run_llm=True,
                        enabled=True,
                    )
                else:
                    idx5_local = task.get("idx5")
                    idx3_local = task.get("idx3")
                    if isinstance(idx5_local, int) and 0 <= idx5_local < len(out):
                        _attach_runtime_trace(
                            out[idx5_local],
                            status="handler_error_pass_through",
                            message=f"Aminoacyl joint handler error: {repr(e)}",
                            run_llm=True,
                            enabled=True,
                        )
                    if isinstance(idx3_local, int) and 0 <= idx3_local < len(out):
                        _attach_runtime_trace(
                            out[idx3_local],
                            status="handler_error_pass_through",
                            message=f"Aminoacyl joint handler error: {repr(e)}",
                            run_llm=True,
                            enabled=True,
                        )
        return _finalize_and_normalize_postcheck_blocks(
            out,
            input_blocks=input_blocks_snapshot,
            run_llm=use_llm,
        )

    logger.info(
        "Running postcheck handlers via global dispatcher (request_parallel=%s, tasks=%s, scheduler=%s).",
        max_workers,
        len(tasks),
        scheduler,
    )

    def _submit(task: Dict[str, Any]):
        region = str(task.get("region") or "")
        task_tag = f"postcheck:{region}"
        return submit_llm_task(
            _run_task,
            task,
            task_tag=task_tag,
            runtime_values_override=runtime_values,
        )

    # Cap mode throttles the number of in-flight tasks for this request only.
    # Global concurrency is still controlled by services.llm_dispatcher.
    if max_workers >= len(tasks):
        future_to_task = {_submit(task): task for task in tasks}
        for fut in as_completed(future_to_task):
            task = future_to_task[fut]
            region = str(task.get("region") or "")
            try:
                merged = fut.result()
                _merge_task_result(merged)
            except Exception as e:
                logger.warning("Postcheck handler for region '%s' raised %r. Passing through.", region, e)
                if str(task.get("kind") or "") == "single":
                    out[int(task["idx"])] = task["blk"]
                    _attach_runtime_trace(
                        out[int(task["idx"])],
                        status="handler_error_pass_through",
                        message=f"Handler raised error and result was passed through: {repr(e)}",
                        run_llm=True,
                        enabled=True,
                    )
                else:
                    idx5_local = task.get("idx5")
                    idx3_local = task.get("idx3")
                    if isinstance(idx5_local, int) and 0 <= idx5_local < len(out):
                        _attach_runtime_trace(
                            out[idx5_local],
                            status="handler_error_pass_through",
                            message=f"Aminoacyl joint handler error: {repr(e)}",
                            run_llm=True,
                            enabled=True,
                        )
                    if isinstance(idx3_local, int) and 0 <= idx3_local < len(out):
                        _attach_runtime_trace(
                            out[idx3_local],
                            status="handler_error_pass_through",
                            message=f"Aminoacyl joint handler error: {repr(e)}",
                            run_llm=True,
                            enabled=True,
                        )
    else:
        pending = list(tasks)
        active: Dict[Any, Dict[str, Any]] = {}
        while pending or active:
            while pending and len(active) < max_workers:
                task = pending.pop(0)
                active[_submit(task)] = task
            fut = next(as_completed(active))
            task = active.pop(fut)
            region = str(task.get("region") or "")
            try:
                merged = fut.result()
                _merge_task_result(merged)
            except Exception as e:
                logger.warning("Postcheck handler for region '%s' raised %r. Passing through.", region, e)
                if str(task.get("kind") or "") == "single":
                    out[int(task["idx"])] = task["blk"]
                    _attach_runtime_trace(
                        out[int(task["idx"])],
                        status="handler_error_pass_through",
                        message=f"Handler raised error and result was passed through: {repr(e)}",
                        run_llm=True,
                        enabled=True,
                    )
                else:
                    idx5_local = task.get("idx5")
                    idx3_local = task.get("idx3")
                    if isinstance(idx5_local, int) and 0 <= idx5_local < len(out):
                        _attach_runtime_trace(
                            out[idx5_local],
                            status="handler_error_pass_through",
                            message=f"Aminoacyl joint handler error: {repr(e)}",
                            run_llm=True,
                            enabled=True,
                        )
                    if isinstance(idx3_local, int) and 0 <= idx3_local < len(out):
                        _attach_runtime_trace(
                            out[idx3_local],
                            status="handler_error_pass_through",
                            message=f"Aminoacyl joint handler error: {repr(e)}",
                            run_llm=True,
                            enabled=True,
                        )

    metrics = get_dispatcher_metrics()
    logger.info(
        "Postcheck dispatcher metrics: inflight=%s submitted=%s completed=%s failed=%s max_workers=%s max_inflight=%s",
        metrics.get("inflight"),
        metrics.get("submitted"),
        metrics.get("completed"),
        metrics.get("failed"),
        metrics.get("max_workers"),
        metrics.get("max_inflight"),
    )
    return _finalize_and_normalize_postcheck_blocks(
        out,
        input_blocks=input_blocks_snapshot,
        run_llm=use_llm,
    )
