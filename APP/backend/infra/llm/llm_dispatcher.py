# -*- coding: utf-8 -*-
"""
Process-local global dispatcher for LLM-related tasks.

Goals:
- Reuse one thread pool per process (Gunicorn worker) instead of creating
  temporary pools in each request path.
- Provide optional in-flight throttling as a queue-like backpressure.
- Expose lightweight metrics for observability.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional
import os
import threading
import time

try:
    from backend.infra.config.runtime_settings import get_effective_runtime_settings
except Exception:  # pragma: no cover
    get_effective_runtime_settings = None  # type: ignore[assignment]


_LOCK = threading.Lock()
_EXECUTOR: Optional[ThreadPoolExecutor] = None
_EXECUTOR_WORKERS = 0
_INFLIGHT_SEM: Optional[threading.Semaphore] = None
_INFLIGHT_LIMIT = 0
_METRICS: Dict[str, Any] = {
    "submitted": 0,
    "completed": 0,
    "failed": 0,
    "inflight": 0,
    "total_latency_ms": 0.0,
    "last_task_tag": "",
    "last_error": "",
}


def _as_int(raw: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        v = int(raw)
    except Exception:
        v = default
    return max(min_value, min(v, max_value))


def _load_runtime_values() -> Dict[str, Any]:
    if get_effective_runtime_settings is None:
        return {}
    try:
        values = get_effective_runtime_settings()
        return values if isinstance(values, dict) else {}
    except Exception:
        return {}


def _resolve_limits(runtime_values_override: Optional[Dict[str, Any]] = None) -> tuple[int, int]:
    runtime_values = _load_runtime_values()
    if isinstance(runtime_values_override, dict) and runtime_values_override:
        runtime_values = {**runtime_values, **runtime_values_override}

    workers_raw = runtime_values.get("LLM_GLOBAL_MAX_WORKERS")
    if workers_raw in (None, ""):
        workers_raw = os.getenv("LLM_GLOBAL_MAX_WORKERS", "64")
    workers = _as_int(workers_raw, default=64, min_value=1, max_value=512)

    inflight_raw = runtime_values.get("LLM_DISPATCHER_MAX_INFLIGHT")
    if inflight_raw in (None, ""):
        inflight_raw = os.getenv("LLM_DISPATCHER_MAX_INFLIGHT", "0")
    inflight_limit = _as_int(inflight_raw, default=0, min_value=0, max_value=100000)
    return workers, inflight_limit


def _ensure_executor(workers: int, inflight_limit: int) -> tuple[ThreadPoolExecutor, Optional[threading.Semaphore]]:
    global _EXECUTOR, _EXECUTOR_WORKERS, _INFLIGHT_SEM, _INFLIGHT_LIMIT
    with _LOCK:
        if _EXECUTOR is None or _EXECUTOR_WORKERS != workers:
            old = _EXECUTOR
            _EXECUTOR = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="llm-dispatch")
            _EXECUTOR_WORKERS = workers
            if old is not None:
                old.shutdown(wait=False, cancel_futures=False)

        if _INFLIGHT_LIMIT != inflight_limit:
            _INFLIGHT_LIMIT = inflight_limit
            _INFLIGHT_SEM = threading.Semaphore(inflight_limit) if inflight_limit > 0 else None

        return _EXECUTOR, _INFLIGHT_SEM


def submit_llm_task(
    fn: Callable[..., Any],
    *args: Any,
    task_tag: str = "",
    runtime_values_override: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Future:
    workers, inflight_limit = _resolve_limits(runtime_values_override)
    executor, sem = _ensure_executor(workers, inflight_limit)

    if sem is not None:
        sem.acquire()

    start = time.perf_counter()
    with _LOCK:
        _METRICS["submitted"] = int(_METRICS.get("submitted", 0)) + 1
        _METRICS["inflight"] = int(_METRICS.get("inflight", 0)) + 1
        _METRICS["last_task_tag"] = str(task_tag or "")
        _METRICS["last_error"] = ""

    try:
        fut = executor.submit(fn, *args, **kwargs)
    except Exception:
        if sem is not None:
            sem.release()
        with _LOCK:
            _METRICS["inflight"] = max(0, int(_METRICS.get("inflight", 0)) - 1)
            _METRICS["failed"] = int(_METRICS.get("failed", 0)) + 1
            _METRICS["last_error"] = "submit_failed"
        raise

    def _on_done(done: Future) -> None:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        failed = done.exception() is not None
        with _LOCK:
            _METRICS["inflight"] = max(0, int(_METRICS.get("inflight", 0)) - 1)
            _METRICS["completed"] = int(_METRICS.get("completed", 0)) + (0 if failed else 1)
            _METRICS["failed"] = int(_METRICS.get("failed", 0)) + (1 if failed else 0)
            _METRICS["total_latency_ms"] = float(_METRICS.get("total_latency_ms", 0.0)) + float(elapsed_ms)
            if failed:
                _METRICS["last_error"] = repr(done.exception())
        if sem is not None:
            sem.release()

    fut.add_done_callback(_on_done)
    return fut


def get_dispatcher_metrics() -> Dict[str, Any]:
    workers, inflight_limit = _resolve_limits(None)
    with _LOCK:
        data = dict(_METRICS)
    data["max_workers"] = workers
    data["max_inflight"] = inflight_limit
    return data
