# -*- coding: utf-8 -*-  # UTF-8
"""
对齐包装层：保持外部调用签名不变，内部转到你原来的实现
"""  # 模块说明

from typing import Tuple, List, Dict, Any, Optional, Iterator, Callable  # 类型注解
from functools import lru_cache  # LRU缓存
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from queue import Empty, Queue
import os
import time
from backend.infra.storage import sequence_repository as sequence_db
from backend.agent import AgentAlignmentService, AlignmentRequest
from backend.application.alignment.demo_sequences import is_demo_sequence, DEMO_SEQS  # 示例序列识别
try:
    from backend.infra.config.runtime_settings import get_effective_runtime_settings
except Exception:  # pragma: no cover
    get_effective_runtime_settings = None  # type: ignore[assignment]


_AGENT_ALIGNMENT_SERVICE = AgentAlignmentService()


def _effective_runtime_values() -> Dict[str, Any]:
    if get_effective_runtime_settings is None:
        return {}
    try:
        values = get_effective_runtime_settings()
        return dict(values) if isinstance(values, dict) else {}
    except Exception:
        return {}


def _normalize_seq(raw: str) -> str:
    """Normalize input sequence for cache key stability."""
    return (raw or "").upper().replace(" ", "").replace("\n", "").replace("\r", "").replace("T", "U")


@lru_cache(maxsize=512)
def _cached_alignment(
    norm_seq: str,
    anticode: str,
    use_llm: bool,
    llm_reasoning: bool,
    template_name: str,
) -> Tuple[str, str, Tuple[str, ...]]:
    """Cached alignment wrapper (in-memory)."""
    tpl_name, csv_raw, warnings = _AGENT_ALIGNMENT_SERVICE.align(
        AlignmentRequest(
            target_seq=norm_seq,
            output_csv_path="/dev/null",
            anticode=anticode,
            use_llm=use_llm,
            llm_reasoning=llm_reasoning,
            template_name=template_name or None,
            runtime_values_override=_effective_runtime_values(),
        )
    )
    return tpl_name, csv_raw, tuple(warnings or [])


def prewarm_demo_cache(use_llm: bool = False) -> None:
    """Prewarm demo alignment cache (optional)."""
    for seq in DEMO_SEQS:
        try:
            _cached_alignment(seq, "", use_llm, False, "")
        except Exception:
            continue


if os.getenv("DEMO_CACHE_PREWARM", "").lower() in ("1", "true", "yes", "on"):
    prewarm_demo_cache(use_llm=False)


def _resolve_batch_workers(batch_max_workers: Optional[int] = None) -> int:
    if batch_max_workers is not None:
        try:
            return max(1, min(int(batch_max_workers), 64))
        except Exception:
            pass
    raw = None
    if get_effective_runtime_settings is not None:
        try:
            values = get_effective_runtime_settings()
            if isinstance(values, dict):
                raw = values.get("ALIGN_BATCH_MAX_WORKERS")
        except Exception:
            raw = None
    if raw in (None, ""):
        raw = os.getenv("ALIGN_BATCH_MAX_WORKERS", "20")
    try:
        v = int(raw)
    except Exception:
        v = 8
    return max(1, min(v, 64))


def perform_full_alignment(
    target_seq: str,
    output_csv_path: str,
    anticode: str = "",
    use_llm: bool = True,
    llm_reasoning: bool = False,
    template_name: str | None = None,
) -> Tuple[str, str, List[str]]:
    """
    完整对齐流水线（外部统一入口）：
    参数保持不变：序列 / 输出路径 / 反密码子 / 是否优先LLM
    返回：模板名、三行CSV原文、警告列表
    """  # 函数说明
    norm_seq = _normalize_seq(target_seq)
    if is_demo_sequence(norm_seq):
        tpl_name, csv_raw, warnings = _cached_alignment(
            norm_seq,
            anticode or "",
            use_llm,
            llm_reasoning,
            template_name or "",
        )
        return tpl_name, csv_raw, list(warnings)  # 返回(模板名, csv原文, warnings)
    return _AGENT_ALIGNMENT_SERVICE.align(
        AlignmentRequest(
            target_seq=target_seq,
            output_csv_path=output_csv_path,
            anticode=anticode,
            use_llm=use_llm,
            llm_reasoning=llm_reasoning,
            template_name=template_name,
            runtime_values_override=_effective_runtime_values(),
        )
    )


def perform_full_alignment_with_meta(
    target_seq: str,
    output_csv_path: str,
    anticode: str = "",
    use_llm: bool = True,
    llm_reasoning: bool = False,
    template_name: str | None = None,
    status_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    input_type: str = "unknown",
    domain_hint: str = "",
    cca_status: str = "unknown",
    intron_status: str = "unknown",
    trna_class: str = "unknown",
    mitochondrial_subtype: str = "unknown",
    enable_root_recovery: bool = False,
    bypass_cache: bool = False,
) -> Tuple[str, str, List[str], Dict[str, Any]]:
    """
    对齐并返回额外元数据（例如二级结构可视化信息）。
    """
    runtime = _effective_runtime_values()
    def compute(callback):
        response = _AGENT_ALIGNMENT_SERVICE.align_with_meta(
            AlignmentRequest(
                target_seq=target_seq,
                output_csv_path=output_csv_path,
                anticode=anticode,
                use_llm=use_llm,
                llm_reasoning=llm_reasoning,
                template_name=template_name,
                input_type=input_type,
                domain_hint=domain_hint,
                cca_status=cca_status,
                intron_status=intron_status,
                trna_class=trna_class,
                mitochondrial_subtype=mitochondrial_subtype,
                enable_root_recovery=bool(enable_root_recovery),
                status_callback=callback,
                return_meta=True,
                runtime_values_override=runtime,
            )
        )
        return (
            response.template_name,
            response.csv_content,
            list(response.warnings or []),
            dict(response.meta or {}),
        )
    if sequence_db.enabled():
        options = {
            "anticode": anticode, "use_llm": use_llm, "llm_reasoning": llm_reasoning,
            "template_name": template_name, "input_type": input_type,
            "domain_hint": domain_hint, "cca_status": cca_status,
            "intron_status": intron_status, "trna_class": trna_class,
            "mitochondrial_subtype": mitochondrial_subtype,
            "enable_root_recovery": bool(enable_root_recovery),
        }
        return sequence_db.execute_cached(target_seq, options, runtime, compute, status_callback, output_csv_path, bypass_cache=bypass_cache)
    return compute(status_callback)


def perform_batch_alignment_with_meta(
    target_seqs: List[str],
    anticode: str = "",
    use_llm: bool = True,
    llm_reasoning: bool = False,
    template_name: str | None = None,
    batch_max_workers: Optional[int] = None,
    input_type: str = "unknown",
    domain_hint: str = "unknown",
    cca_status: str = "unknown",
    intron_status: str = "unknown",
    trna_class: str = "unknown",
    mitochondrial_subtype: str = "unknown",
    enable_root_recovery: bool = False,
) -> Dict[str, Any]:
    """
    Run multiple alignments in one request and return per-sequence results.
    Errors are isolated to each sequence item.
    """
    seqs = [str(x or "").strip() for x in list(target_seqs or [])]
    workers = _resolve_batch_workers(batch_max_workers=batch_max_workers)
    started = time.perf_counter()

    results: List[Dict[str, Any]] = [None] * len(seqs)  # type: ignore[list-item]
    if workers <= 1 or len(seqs) <= 1:
        for i, seq in enumerate(seqs):
            results[i] = _run_batch_item(
                index=i,
                seq=seq,
                anticode=anticode,
                use_llm=use_llm,
                llm_reasoning=llm_reasoning,
                template_name=template_name,
                input_type=input_type,
                domain_hint=domain_hint,
                cca_status=cca_status,
                intron_status=intron_status,
                trna_class=trna_class,
                mitochondrial_subtype=mitochondrial_subtype,
                enable_root_recovery=enable_root_recovery,
            )
    else:
        max_workers = min(workers, len(seqs))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="align-batch") as pool:
            future_to_idx = {
                pool.submit(
                    _run_batch_item,
                    i,
                    seq,
                    anticode,
                    use_llm,
                    llm_reasoning,
                    template_name,
                    None,
                    input_type=input_type,
                    domain_hint=domain_hint,
                    cca_status=cca_status,
                    intron_status=intron_status,
                    trna_class=trna_class,
                    mitochondrial_subtype=mitochondrial_subtype,
                    enable_root_recovery=enable_root_recovery,
                ): i
                for i, seq in enumerate(seqs)
            }
            for fut in as_completed(future_to_idx):
                i = future_to_idx[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:
                    results[i] = {
                        "index": i,
                        "target_seq": seqs[i],
                        "ok": False,
                        "error": f"Internal batch worker error: {e}",
                    }

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    succeeded = sum(1 for x in results if bool((x or {}).get("ok")))
    failed = len(results) - succeeded
    return {
        "count": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "batch_workers": min(workers, max(1, len(results))) if results else 0,
        "elapsed_ms": elapsed_ms,
        "results": results,
    }


def _run_batch_item(
    index: int,
    seq: str,
    anticode: str,
    use_llm: bool,
    llm_reasoning: bool,
    template_name: Optional[str],
    status_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    *,
    input_type: str = "unknown",
    domain_hint: str = "unknown",
    cca_status: str = "unknown",
    intron_status: str = "unknown",
    trna_class: str = "unknown",
    mitochondrial_subtype: str = "unknown",
    enable_root_recovery: bool = False,
    bypass_cache: bool = False,
) -> Dict[str, Any]:
    if not seq:
        return {
            "index": index,
            "target_seq": seq,
            "ok": False,
            "error": "Empty sequence.",
        }
    try:
        tpl_name, csv_raw, warnings, meta = perform_full_alignment_with_meta(
            target_seq=seq,
            output_csv_path="/dev/null",
            anticode=anticode,
            use_llm=use_llm,
            llm_reasoning=llm_reasoning,
            template_name=template_name,
            status_callback=status_callback,
            input_type=input_type,
            domain_hint=domain_hint,
            cca_status=cca_status,
            intron_status=intron_status,
            trna_class=trna_class,
            mitochondrial_subtype=mitochondrial_subtype,
            enable_root_recovery=enable_root_recovery,
            bypass_cache=bypass_cache,
        )
        structure = {}
        llm_trace = {}
        agent = {}
        scientific_provenance = {}
        if isinstance(meta, dict):
            structure = dict(meta.get("structure_view") or {})
            llm_trace = dict(meta.get("llm_trace_view") or {})
            agent = dict(meta.get("agent_v2") or {})
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
            scientific_provenance = {
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
            }
        return {
            "index": index,
            "target_seq": seq,
            "ok": True,
            "template_name": tpl_name,
            "csv_content": csv_raw,
            "warnings": list(warnings or []),
            "structure": structure,
            "llm_trace": llm_trace,
            "agent": agent,
            "scientific_provenance": scientific_provenance,
        }
    except ValueError as e:
        return {
            "index": index,
            "target_seq": seq,
            "ok": False,
            "error": str(e),
        }
    except Exception as e:
        return {
            "index": index,
            "target_seq": seq,
            "ok": False,
            "error": f"Alignment failed: {e}",
        }


def _batch_status_event(
    *,
    stage: str,
    message: str,
    completed: int,
    total: int,
    succeeded: int,
    failed: int,
    active_workers: int,
    pending: int,
    transport: str,
    current_index: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "event": "status",
        "data": {
            "stage": stage,
            "message": message,
            "completed": completed,
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "active_workers": active_workers,
            "pending": pending,
            "transport": transport,
            "current_index": current_index,
        },
    }


def _batch_heartbeat_event(
    *,
    completed: int,
    total: int,
    succeeded: int,
    failed: int,
    active_workers: int,
    pending: int,
    transport: str,
) -> Dict[str, Any]:
    return {
        "event": "heartbeat",
        "data": {
            "message": (
                f"Backend heartbeat: {completed}/{total} finished, "
                f"{pending} pending, {active_workers} worker(s) active."
            ),
            "completed": completed,
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "active_workers": active_workers,
            "pending": pending,
            "transport": transport,
        },
    }


def _item_status_callback(
    status_queue: "Queue[Dict[str, Any]]",
    *,
    index: int,
    seq: str,
) -> Callable[[Dict[str, Any]], None]:
    def _callback(payload: Dict[str, Any]) -> None:
        data = dict(payload or {})
        data["index"] = index
        # Keep the sequence routing metadata, but never duplicate the input
        # sequence in every streamed event.
        data["target_seq"] = seq
        status_queue.put({"event": "status", "data": data})

    return _callback


def _drain_status_queue(status_queue: "Queue[Dict[str, Any]]") -> Iterator[Dict[str, Any]]:
    while True:
        try:
            evt = status_queue.get_nowait()
        except Empty:
            break
        yield evt


def iter_batch_alignment_events_with_meta(
    target_seqs: List[str],
    anticode: str = "",
    use_llm: bool = True,
    llm_reasoning: bool = False,
    template_name: str | None = None,
    batch_max_workers: Optional[int] = None,
    input_type: str = "unknown",
    domain_hint: str = "unknown",
    cca_status: str = "unknown",
    intron_status: str = "unknown",
    trna_class: str = "unknown",
    mitochondrial_subtype: str = "unknown",
    enable_root_recovery: bool = False,
    bypass_cache: bool = False,
) -> Iterator[Dict[str, Any]]:
    """
    Stream batch alignment events in near-real-time.
    Yields dicts in shape: {"event": "<meta|item|done>", "data": {...}}.
    """
    seqs = [str(x or "").strip() for x in list(target_seqs or [])]
    workers = _resolve_batch_workers(batch_max_workers=batch_max_workers)
    started = time.perf_counter()
    succeeded = 0
    failed = 0
    status_queue: Queue[Dict[str, Any]] = Queue()
    max_workers = min(workers, len(seqs)) if seqs else 0
    transport = "serial" if max_workers <= 1 else "parallel"

    yield {
        "event": "meta",
        "data": {
            "count": len(seqs),
            "batch_workers": max_workers,
        },
    }
    yield _batch_status_event(
        stage="accepted",
        message=f"Backend accepted {len(seqs)} sequence(s). Preparing alignment workers.",
        completed=0,
        total=len(seqs),
        succeeded=0,
        failed=0,
        active_workers=max_workers,
        pending=len(seqs),
        transport=transport,
    )

    if seqs:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="align-batch-stream") as pool:
            future_to_idx = {
                pool.submit(
                    _run_batch_item,
                    i,
                    seq,
                    anticode,
                    use_llm,
                    llm_reasoning,
                    template_name,
                    _item_status_callback(status_queue, index=i, seq=seq),
                    input_type=input_type,
                    domain_hint=domain_hint,
                    cca_status=cca_status,
                    intron_status=intron_status,
                    trna_class=trna_class,
                    mitochondrial_subtype=mitochondrial_subtype,
                    enable_root_recovery=enable_root_recovery,
                    bypass_cache=bypass_cache,
                ): i
                for i, seq in enumerate(seqs)
            }
            pending_futures = set(future_to_idx)
            yield _batch_status_event(
                stage="dispatching" if transport == "parallel" else "aligning",
                message=(
                    f"Backend dispatched {len(seqs)} sequence(s) across {max_workers} worker(s)."
                    if transport == "parallel"
                    else f"Backend started serial alignment for {len(seqs)} sequence(s)."
                ),
                completed=0,
                total=len(seqs),
                succeeded=0,
                failed=0,
                active_workers=max_workers,
                pending=len(seqs),
                transport=transport,
            )
            last_idle_status_at = time.perf_counter()
            last_heartbeat_at = time.perf_counter()
            while pending_futures:
                emitted_status = False
                for evt in _drain_status_queue(status_queue):
                    emitted_status = True
                    yield evt
                done_futures, pending_futures = wait(
                    pending_futures,
                    timeout=0.5,
                    return_when=FIRST_COMPLETED,
                )
                for evt in _drain_status_queue(status_queue):
                    emitted_status = True
                    yield evt
                if not done_futures:
                    now = time.perf_counter()
                    if (now - last_heartbeat_at) >= 2.0:
                        completed = succeeded + failed
                        pending_count = len(pending_futures)
                        active_workers = min(max_workers, pending_count)
                        yield _batch_heartbeat_event(
                            completed=completed,
                            total=len(seqs),
                            succeeded=succeeded,
                            failed=failed,
                            active_workers=active_workers,
                            pending=pending_count,
                            transport=transport,
                        )
                        last_heartbeat_at = now
                    if emitted_status or (now - last_idle_status_at) < 5.0:
                        continue
                    completed = succeeded + failed
                    pending_count = len(pending_futures)
                    active_workers = min(max_workers, pending_count)
                    yield _batch_status_event(
                        stage="working",
                        message=(
                            f"Backend is still aligning: {completed}/{len(seqs)} finished, "
                            f"{pending_count} pending, {active_workers} worker(s) active."
                        ),
                        completed=completed,
                        total=len(seqs),
                        succeeded=succeeded,
                        failed=failed,
                        active_workers=active_workers,
                        pending=pending_count,
                        transport=transport,
                    )
                    last_idle_status_at = now
                    continue
                last_idle_status_at = time.perf_counter()
                last_heartbeat_at = last_idle_status_at
                for fut in done_futures:
                    i = future_to_idx[fut]
                    try:
                        item = fut.result()
                    except Exception as e:
                        item = {
                            "index": i,
                            "target_seq": seqs[i],
                            "ok": False,
                            "error": f"Internal batch worker error: {e}",
                        }
                    if bool(item.get("ok")):
                        succeeded += 1
                    else:
                        failed += 1
                    yield {"event": "item", "data": item}
                    completed = succeeded + failed
                    pending_count = len(pending_futures)
                    active_workers = min(max_workers, pending_count)
                    yield _batch_status_event(
                        stage="progress",
                        message=(
                            f"Backend finished {completed}/{len(seqs)} sequence(s); "
                            f"{pending_count} still pending."
                        ),
                        completed=completed,
                        total=len(seqs),
                        succeeded=succeeded,
                        failed=failed,
                        active_workers=active_workers,
                        pending=pending_count,
                        transport=transport,
                        current_index=i,
                    )
            for evt in _drain_status_queue(status_queue):
                yield evt

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    yield {
        "event": "done",
        "data": {
            "count": len(seqs),
            "succeeded": succeeded,
            "failed": failed,
            "batch_workers": min(workers, max(1, len(seqs))) if seqs else 0,
            "elapsed_ms": elapsed_ms,
        },
    }
