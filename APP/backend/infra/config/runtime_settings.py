# -*- coding: utf-8 -*-
"""
Runtime settings manager for a small whitelist of tunable options.

Design goals:
- Only allow editing explicitly whitelisted keys.
- Validate/coerce types before persisting.
- Persist non-prompt runtime overrides to backend/infra/config/runtime_settings.json.
- Persist prompt overrides to MySQL table runtime_prompt_settings.
- Apply settings to process env so existing code paths reading os.getenv()
  can pick up updates without introducing wide invasive changes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set

import pymysql
from backend.infra.config.mysql_settings import load_mysql_config


RUNTIME_SETTINGS_PATH = Path(__file__).resolve().with_name("runtime_settings.json")
PROMPT_SETTINGS_TABLE = "runtime_prompt_settings"
DEFAULT_VARLOOP_SYSTEM_PROMPT = (
    "You are a strict RNA alignment post-editor.\n"
    "CRITICAL RULES:\n"
    "1) NEVER move columns; NEVER change numbering (LINE1) or template (LINE2).\n"
    "2) ONLY produce a new TARGET token list for the VARIABLE BLOCK.\n"
    "3) Preserve the EXACT left-to-right order of ALL non-gap bases from the original variable block.\n"
    "4) Standard Sprinzl variable-loop labels have priority over unsupported extension labels.\n"
    "   Between the variable arm and T-stem 49, one residue uses 48, two use 46/48, and three use 46/47/48; never retain 47 at the expense of 48.\n"
    "   If the structure pair hint is 0, fill an unpaired short region in occupancy order 44, 48, 46, 47, 45; then exhaust V1-V5, then extend into variable-stem labels only as balanced mirror shells from k=1 outward (for example V11/V21 before V12/V22).\n"
    "5) Stem mirror rule:\n"
    "   - If BOTH labels V1k and V2k exist, keep the fixed mirror correspondence. One side may be a base while the other side is '-'.\n"
    "   - If ONLY ONE side label exists (mate missing), that side MUST be '-' (do not place a base there).\n"
    "6) Loop labels (V1..V5) take at most one base each; otherwise '-'.\n"
    "7) Use only A/U/G/C/N or '-' (dash). Return ONLY JSON with key 'var_target' (array of strings)."
)

# Whitelisted keys editable from internal admin panel only.
RUNTIME_SETTINGS_SCHEMA: Dict[str, Dict[str, Any]] = {
    "TRNAALIGN_MAX_WORKERS": {
        "type": "int",
        "default": 4,
        "min": 1,
        "max": 128,
        "description": "Max worker count for template ranking.",
        "category": "alignment",
    },
    "TRNAALIGN_FAST_PREFILTER": {
        "type": "bool",
        "default": True,
        "description": "Enable fast k-mer inverted-index prefilter.",
        "category": "alignment",
    },
    "TRNAALIGN_FAST_K": {
        "type": "int",
        "default": 6,
        "min": 3,
        "max": 8,
        "description": "k used by fast prefilter.",
        "category": "alignment",
    },
    "TRNAALIGN_FAST_TOPN": {
        "type": "int",
        "default": 120,
        "min": 50,
        "max": 5000,
        "description": "Top candidate count after fast prefilter.",
        "category": "alignment",
    },
    "TRNAALIGN_SCORE_TOPN": {
        "type": "int",
        "default": 0,
        "min": 0,
        "max": 10000,
        "description": "Cap candidate count for fine alignment scoring (0 means no cap).",
        "category": "alignment",
    },
    "TRNAALIGN_PREFILTER_K": {
        "type": "int",
        "default": 5,
        "min": 3,
        "max": 8,
        "description": "k used by fallback overlap prefilter.",
        "category": "alignment",
    },
    "TRNAALIGN_PREFILTER_FRAC": {
        "type": "float",
        "default": 0.05,
        "min": 0.001,
        "max": 1.0,
        "description": "Fraction-based candidate ratio for fallback prefilter.",
        "category": "alignment",
    },
    "TRNAALIGN_PREFILTER_TOPN": {
        "type": "int",
        "default": 500,
        "min": 50,
        "max": 20000,
        "description": "Absolute top-N cap for fallback prefilter.",
        "category": "alignment",
    },
    "ALIGN_BATCH_MAX_WORKERS": {
        "type": "int",
        "default": 20,
        "min": 1,
        "max": 64,
        "description": "Max worker count for sequence-level /align/batch parallelism.",
        "category": "alignment",
    },
    "ALIGN_BATCH_MAX_SIZE": {
        "type": "int",
        "default": 128,
        "min": 1,
        "max": 1000,
        "description": "Maximum number of sequences allowed in one /align/batch request.",
        "category": "alignment",
    },
    "AGENT_ENABLE_CM_TOOL": {
        "type": "bool",
        "default": False,
        "description": "Expose an additional numbering-CM action in legacy-only ablations.",
        "category": "alignment",
    },
    "AGENT_CANONICAL_ROOT_STRATEGY": {
        "type": "str",
        "default": "agent_toolbox",
        "max_len": 32,
        "description": "Initial-tool policy: agent_toolbox (default), cm_first/cm_only, or legacy_only ablation.",
        "category": "alignment",
    },
    "AGENT_MAX_PLANNER_CALLS": {
        "type": "int",
        "default": 6,
        "min": 1,
        "max": 32,
        "description": "Maximum constrained LLM planning calls per alignment request.",
        "category": "alignment",
    },
    "AGENT_LLM_TRIAGE_MODE": {
        "type": "str",
        "default": "uncertain_only",
        "max_len": 32,
        "options": ["uncertain_only", "dual_trigger", "always"],
        "description": (
            "Use hard-conflict-only review, dual hard/soft-risk review, "
            "or call the LLM planner for every planning decision."
        ),
        "category": "llm",
    },
    "AGENT_SOFT_RISK_MIN_CM_POSTERIOR": {
        "type": "float",
        "default": 0.995,
        "min": 0.0,
        "max": 1.0,
        "description": (
            "Dual-trigger review threshold for the CM average posterior. "
            "Candidates below this value may be escalated without a hard-rule failure."
        ),
        "category": "llm",
    },
    "AGENT_SOFT_RISK_REVIEW_INSERTIONS": {
        "type": "bool",
        "default": True,
        "description": (
            "Allow dual-trigger review when an otherwise legal CM path contains "
            "one or more insertion states."
        ),
        "category": "llm",
    },
    "AGENT_SOFT_RISK_REVIEW_BROAD_CM": {
        "type": "bool",
        "default": True,
        "description": (
            "Allow dual-trigger review when an otherwise legal candidate uses "
            "the cross-domain all-num CM."
        ),
        "category": "llm",
    },
    "AGENT_SOFT_RISK_REVIEW_LOCAL_COORDINATE_AMBIGUITY": {
        "type": "bool",
        "default": True,
        "description": (
            "Review a nonconforming CM variable-to-T bridge only when the "
            "candidate itself provides local evidence, using agreement across "
            "distinct templates without reading benchmark coordinates."
        ),
        "category": "llm",
    },
    "AGENT_SOFT_RISK_REVIEW_ACCEPTOR_TAIL": {
        "type": "bool",
        "default": True,
        "description": (
            "Review CM acceptor-arm endpoint disagreements using tRNAscan-SE "
            "secondary-structure evidence before assigning positions 66-76."
        ),
        "category": "llm",
    },
    "AGENT_SOFT_RISK_REVIEW_VARIABLE_BOUNDARY": {
        "type": "bool",
        "default": True,
        "description": (
            "Review CM variable-arm and 46-48 boundary ambiguity using an "
            "independent tRNAscan-SE secondary-structure projection."
        ),
        "category": "llm",
    },
    "AGENT_ACCEPTOR_TAIL_REFERENCE_TOP_K": {
        "type": "int",
        "default": 5,
        "min": 3,
        "max": 10,
        "description": (
            "Maximum distinct reference templates checked when independent "
            "structure evidence leaves multiple acceptor-endpoint possibilities."
        ),
        "category": "alignment",
    },
    "AGENT_ACCEPTOR_TAIL_MIN_REFERENCE_SUPPORT": {
        "type": "int",
        "default": 3,
        "min": 3,
        "max": 10,
        "description": (
            "Minimum distinct non-identical valid templates that must agree on "
            "the 66-76 endpoint coordinate partition."
        ),
        "category": "alignment",
    },
    "AGENT_VARIABLE_BOUNDARY_REFERENCE_TOP_K": {
        "type": "int",
        "default": 5,
        "min": 2,
        "max": 10,
        "description": (
            "Maximum independent reference templates checked for a long "
            "variable-arm structure projection."
        ),
        "category": "alignment",
    },
    "AGENT_VARIABLE_BOUNDARY_MIN_REFERENCE_SUPPORT": {
        "type": "int",
        "default": 2,
        "min": 2,
        "max": 10,
        "description": (
            "Minimum distinct non-identical template sequences that must agree "
            "with a long variable-arm structure projection."
        ),
        "category": "alignment",
    },
    "AGENT_LOCAL_AMBIGUITY_REFERENCE_TOP_K": {
        "type": "int",
        "default": 5,
        "min": 2,
        "max": 10,
        "description": (
            "Maximum distinct reference templates evaluated for a local "
            "variable-to-T bridge coordinate review."
        ),
        "category": "alignment",
    },
    "AGENT_LOCAL_AMBIGUITY_MIN_REFERENCE_SUPPORT": {
        "type": "int",
        "default": 2,
        "min": 2,
        "max": 10,
        "description": (
            "Minimum number of distinct valid reference templates that must "
            "support the same canonical 46-48 bridge assignment before a candidate "
            "can replace the initial structural-alignment projection."
        ),
        "category": "alignment",
    },
    "AGENT_SOFT_RISK_MIN_LOCAL_RESOLUTION_SCORE_GAIN": {
        "type": "float",
        "default": 0.1,
        "min": 0.1,
        "max": 500.0,
        "description": (
            "Minimum global structural-score gain required after distinct "
            "templates agree on resolving the local variable-bridge ambiguity."
        ),
        "category": "alignment",
    },
    "AGENT_SOFT_RISK_MIN_CROSS_BACKEND_SCORE_GAIN": {
        "type": "float",
        "default": 51.0,
        "min": 0.1,
        "max": 500.0,
        "description": (
            "Frozen development-calibrated score margin required before a valid "
            "template/regional-DP candidate may replace a valid CM candidate."
        ),
        "category": "llm",
    },
    "AGENT_MEMORY_MODE": {
        "type": "str",
        "default": "off",
        "max_len": 32,
        "options": ["off", "read_only"],
        "description": "Structured experience memory mode. Online use is limited to frozen read-only snapshots.",
        "category": "alignment",
    },
    "AGENT_MEMORY_PATH": {
        "type": "str",
        "default": "data/agent_memory/patterns.v2.jsonl",
        "max_len": 512,
        "description": "Path to the structured Agent experience snapshot.",
        "category": "alignment",
    },
    "AGENT_MEMORY_REQUIRE_FROZEN": {
        "type": "bool",
        "default": True,
        "description": "Require an immutable manifest before memory can be read outside development.",
        "category": "alignment",
    },
    "AGENT_ENABLE_LLM_POSTCHECK": {
        "type": "bool",
        "default": False,
        "description": "Enable explicit Agent actions that use the experimental regional LLM candidate generator.",
        "category": "llm",
    },
    "AGENT_ENABLE_LLM_BOUNDARY_REFINEMENT": {
        "type": "bool",
        "default": False,
        "description": "Deprecated no-op retained for compatibility; LLM boundary proposals were retired.",
        "category": "llm",
    },
    "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT": {
        "type": "bool",
        "default": True,
        "description": "Enable explicit deterministic multi-evidence boundary refinement actions.",
        "category": "alignment",
    },
    "AGENT_PLANNER_MODEL": {
        "type": "str",
        "default": "",
        "max_len": 128,
        "description": "Optional model override for constrained Agent tool planning.",
        "category": "llm",
    },
    "AGENT_ENABLE_LEGACY_FALLBACK": {
        "type": "bool",
        "default": True,
        "description": "Allow a deterministic legacy reference candidate when CM cannot be accepted.",
        "category": "alignment",
    },
    "AGENT_ENABLE_CM_ROOT_RECOVERY": {
        "type": "bool",
        "default": False,
        "description": "Legacy-only compatibility recovery after root construction fails.",
        "category": "alignment",
    },
    "CMALIGN_CPU": {
        "type": "int",
        "default": 1,
        "min": 1,
        "max": 16,
        "description": "Infernal CPU threads used per CM tool call.",
        "category": "alignment",
    },
    "LLM_PROVIDER": {
        "type": "str",
        "default": "ollama",
        "max_len": 32,
        "options": ["ollama", "deepseek", "minimax", "mimo"],
        "description": "LLM provider for postcheck pipeline.",
        "category": "llm",
    },
    "LLM_POSTCHECK": {
        "type": "bool",
        "default": True,
        "description": "Enable variable-loop LLM postcheck.",
        "category": "llm",
    },
    "OLLAMA_BASE_URL": {
        "type": "str",
        "default": "http://localhost:11434",
        "max_len": 256,
        "description": "Base URL for Ollama API.",
        "category": "llm",
    },
    "OLLAMA_MODEL": {
        "type": "str",
        "default": "qwen3:32b",
        "max_len": 128,
        "description": "Model name used when provider is ollama.",
        "category": "llm",
    },
    "DEEPSEEK_BASE_URL": {
        "type": "str",
        "default": "https://api.deepseek.com",
        "max_len": 256,
        "description": "Base URL for DeepSeek OpenAI-compatible API.",
        "category": "llm",
    },
    "DEEPSEEK_MODEL": {
        "type": "str",
        "default": "deepseek-chat",
        "max_len": 128,
        "description": "Model name used when provider is deepseek.",
        "category": "llm",
    },
    "DEEPSEEK_API_KEY": {
        "type": "str",
        "default": "",
        "max_len": 512,
        "sensitive": True,
        "description": "DeepSeek API key used when provider is deepseek.",
        "category": "llm",
    },
    "MINIMAX_BASE_URL": {
        "type": "str",
        "default": "https://api.minimaxi.com/v1",
        "max_len": 256,
        "description": "Base URL for MiniMax OpenAI-compatible API. China: https://api.minimaxi.com/v1 ; Global: https://api.minimax.io/v1",
        "category": "llm",
    },
    "MINIMAX_MODEL": {
        "type": "str",
        "default": "MiniMax-M2.5-highspeed",
        "max_len": 128,
        "description": "Model name used when provider is minimax.",
        "category": "llm",
    },
    "MINIMAX_REASONING_MODEL": {
        "type": "str",
        "default": "MiniMax-M2.7",
        "max_len": 128,
        "description": "Optional reasoning-oriented model used when llm_reasoning=true and provider is minimax.",
        "category": "llm",
    },
    "MINIMAX_API_KEY": {
        "type": "str",
        "default": "",
        "max_len": 512,
        "sensitive": True,
        "description": "MiniMax API key used when provider is minimax.",
        "category": "llm",
    },
    "MIMO_BASE_URL": {
        "type": "str",
        "default": "https://api.xiaomimimo.com/v1",
        "max_len": 256,
        "description": "OpenAI-compatible API base URL used for planning and postcheck.",
        "category": "llm",
    },
    "MIMO_MODEL": {
        "type": "str",
        "default": "mimo-v2.6-pro",
        "max_len": 128,
        "description": "Model identifier used for planning and postcheck.",
        "category": "llm",
    },
    "MIMO_API_KEY": {
        "type": "str",
        "default": "",
        "max_len": 512,
        "sensitive": True,
        "description": "Mimo API key used when provider is mimo.",
        "category": "llm",
    },
    "MIMO_API_KEYS": {
        "type": "str",
        "default": "",
        "max_len": 8192,
        "sensitive": True,
        "description": "Optional ordered API-key pool for this endpoint, separated by commas or newlines.",
        "category": "llm",
    },
    "MIMO_TOKEN_PLAN_API_KEY": {
        "type": "str",
        "default": "",
        "max_len": 512,
        "sensitive": True,
        "description": "Optional permitted Token Plan key used only with its dedicated endpoint.",
        "category": "llm",
    },
    "MIMO_TOKEN_PLAN_BASE_URL": {
        "type": "str",
        "default": "https://token-plan-cn.xiaomimimo.com/v1",
        "max_len": 256,
        "description": "Dedicated OpenAI-compatible endpoint paired only with the Token Plan key.",
        "category": "llm",
    },
    "LLM_VERIFY_SSL": {
        "type": "bool",
        "default": True,
        "description": "Verify SSL certificate for LLM HTTP calls.",
        "category": "llm",
    },
    "LLM_CONNECT_TIMEOUT": {
        "type": "float",
        "default": 8.0,
        "min": 0.5,
        "max": 120.0,
        "description": "HTTP connect timeout in seconds.",
        "category": "llm",
    },
    "LLM_READ_TIMEOUT": {
        "type": "float",
        "default": 20.0,
        "min": 1.0,
        "max": 300.0,
        "description": "HTTP read timeout in seconds.",
        "category": "llm",
    },
    "LLM_HARD_BUDGET": {
        "type": "float",
        "default": 30.0,
        "min": 1.0,
        "max": 600.0,
        "description": "Hard wall time budget for one LLM attempt loop (seconds).",
        "category": "llm",
    },
    "LLM_MAX_TOKENS": {
        "type": "int",
        "default": 262144,
        "min": 128,
        "max": 262144,
        "description": "Completion max_tokens for OpenAI-compatible LLM calls (DeepSeek/Ollama-openai path). 262144 = 256K.",
        "category": "llm",
    },
    "LLM_STREAM": {
        "type": "bool",
        "default": True,
        "description": "Enable streaming response mode for LLM calls.",
        "category": "llm",
    },
    "LLM_RETRIES": {
        "type": "int",
        "default": 4,
        "min": 0,
        "max": 10,
        "description": "Retry count when LLM call fails or response is invalid.",
        "category": "llm",
    },
    "LLM_MIN_REQUEST_INTERVAL": {
        "type": "float",
        "default": 0.0,
        "min": 0.0,
        "max": 60.0,
        "description": (
            "Minimum seconds between HTTP request starts for the same LLM credential; "
            "429 responses add an endpoint-advised per-credential cooldown."
        ),
        "category": "llm",
    },
    "LLM_POSTCHECK_MAX_WORKERS": {
        "type": "int",
        "default": 1,
        "min": 1,
        "max": 8,
        "description": "Max parallel workers for region-level postcheck dispatch.",
        "category": "llm",
    },
    "LLM_POSTCHECK_SCHEDULER": {
        "type": "str",
        "default": "all",
        "max_len": 32,
        "options": ["all", "cap"],
        "description": "Postcheck scheduling policy: all=submit all region tasks; cap=respect LLM_POSTCHECK_MAX_WORKERS per request.",
        "category": "llm",
    },
    "LLM_GLOBAL_MAX_WORKERS": {
        "type": "int",
        "default": 64,
        "min": 1,
        "max": 512,
        "description": "Global per-process worker count for shared LLM dispatcher.",
        "category": "llm",
    },
    "LLM_DISPATCHER_MAX_INFLIGHT": {
        "type": "int",
        "default": 0,
        "min": 0,
        "max": 100000,
        "description": "Global max in-flight LLM tasks in dispatcher queue (0 means unlimited).",
        "category": "llm",
    },
    "LLM_BACKOFF_BASE": {
        "type": "float",
        "default": 0.5,
        "min": 0.0,
        "max": 10.0,
        "description": "Exponential backoff base seconds for LLM retry.",
        "category": "llm",
    },
    "ALIGNMENT_SYSTEM_PROMPT": {
        "type": "str",
        "default": "",
        "max_len": 8000,
        "multiline": True,
        "description": "Reserved system prompt for alignment pipeline (not wired yet).",
        "category": "prompt",
    },
    "ALIGNMENT_USER_PROMPT_TEMPLATE": {
        "type": "str",
        "default": "",
        "max_len": 8000,
        "multiline": True,
        "description": "Reserved user prompt template for alignment pipeline (not wired yet).",
        "category": "prompt",
    },
    "LLM_VARLOOP_SYSTEM_PROMPT": {
        "type": "str",
        "default": "",
        "max_len": 8000,
        "multiline": True,
        "description": "System prompt used by variable-loop LLM postcheck.",
        "category": "prompt",
    },
    "LLM_AA5_SYSTEM_PROMPT": {
        "type": "str",
        "default": "",
        "max_len": 8000,
        "multiline": True,
        "description": "System prompt for aminoacyl arm 5' end postcheck.",
        "category": "prompt",
    },
    "LLM_D_SYSTEM_PROMPT": {
        "type": "str",
        "default": "",
        "max_len": 8000,
        "multiline": True,
        "description": "System prompt for D loop + D stem postcheck.",
        "category": "prompt",
    },
    "LLM_ANTICODON_SYSTEM_PROMPT": {
        "type": "str",
        "default": "",
        "max_len": 8000,
        "multiline": True,
        "description": "System prompt for anticodon loop + anticodon stem postcheck.",
        "category": "prompt",
    },
    "LLM_T_SYSTEM_PROMPT": {
        "type": "str",
        "default": "",
        "max_len": 8000,
        "multiline": True,
        "description": "System prompt for T loop + T stem postcheck.",
        "category": "prompt",
    },
    "LLM_AA3_SYSTEM_PROMPT": {
        "type": "str",
        "default": "",
        "max_len": 8000,
        "multiline": True,
        "description": "System prompt for aminoacyl arm 3' end postcheck.",
        "category": "prompt",
    },
    "LLM_VARLOOP_USER_PROMPT_SUFFIX": {
        "type": "str",
        "default": "",
        "max_len": 2000,
        "multiline": True,
        "description": "Optional extra instruction appended to variable-loop user prompt.",
        "category": "prompt",
    },
}


def _to_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"Invalid boolean value: {raw!r}")


def _coerce_value(key: str, raw: Any) -> Any:
    spec = RUNTIME_SETTINGS_SCHEMA[key]
    typ = spec.get("type")

    if typ == "int":
        val = int(raw)
        if "min" in spec and val < spec["min"]:
            raise ValueError(f"{key} must be >= {spec['min']}")
        if "max" in spec and val > spec["max"]:
            raise ValueError(f"{key} must be <= {spec['max']}")
        return val

    if typ == "float":
        val = float(raw)
        if "min" in spec and val < spec["min"]:
            raise ValueError(f"{key} must be >= {spec['min']}")
        if "max" in spec and val > spec["max"]:
            raise ValueError(f"{key} must be <= {spec['max']}")
        return val

    if typ == "bool":
        return _to_bool(raw)

    if typ == "str":
        val = str(raw).strip()
        if "max_len" in spec and len(val) > int(spec["max_len"]):
            raise ValueError(f"{key} length must be <= {spec['max_len']}")
        options = spec.get("options")
        if isinstance(options, list) and options and val not in options:
            raise ValueError(f"{key} must be one of {options}")
        return val

    raise ValueError(f"Unsupported schema type for {key}: {typ}")


def _serialize_env_value(val: Any, typ: str) -> str:
    if typ == "bool":
        return "1" if bool(val) else "0"
    return str(val)


def get_schema_payload() -> Dict[str, Dict[str, Any]]:
    return {k: dict(v) for k, v in RUNTIME_SETTINGS_SCHEMA.items()}


MYSQL_CONFIG: Optional[Dict[str, Any]] = load_mysql_config()
_PROMPT_TABLE_READY = False
_LEGACY_PROMPT_MIGRATION_DONE = False


def _prompt_setting_keys() -> Set[str]:
    return {
        key for key, spec in RUNTIME_SETTINGS_SCHEMA.items()
        if str(spec.get("category", "")).lower() == "prompt"
    }


def _is_prompt_key(key: str) -> bool:
    return key in _prompt_setting_keys()


def is_prompt_store_enabled() -> bool:
    return MYSQL_CONFIG is not None


def _prompt_store_label() -> str:
    if not is_prompt_store_enabled():
        return str(RUNTIME_SETTINGS_PATH)
    return f"{RUNTIME_SETTINGS_PATH} + mysql:{PROMPT_SETTINGS_TABLE}"


def _connect_prompt_store():
    if MYSQL_CONFIG is None:
        return None
    try:
        return pymysql.connect(**MYSQL_CONFIG)
    except Exception:
        return None


def _ensure_prompt_table(conn) -> None:
    global _PROMPT_TABLE_READY
    if _PROMPT_TABLE_READY:
        return
    sql = f"""
        CREATE TABLE IF NOT EXISTS `{PROMPT_SETTINGS_TABLE}` (
            `prompt_key` VARCHAR(128) NOT NULL,
            `prompt_value` MEDIUMTEXT NOT NULL,
            `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`prompt_key`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    _PROMPT_TABLE_READY = True


def load_prompt_overrides() -> Dict[str, Any]:
    if not is_prompt_store_enabled():
        return {}
    conn = _connect_prompt_store()
    if conn is None:
        return {}
    prompt_keys = _prompt_setting_keys()
    try:
        _ensure_prompt_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT prompt_key AS k, prompt_value AS v FROM `{PROMPT_SETTINGS_TABLE}`"
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    cleaned: Dict[str, Any] = {}
    for row in rows or []:
        key = str(row.get("k") or "")
        if key not in prompt_keys:
            continue
        try:
            cleaned[key] = _coerce_value(key, row.get("v", ""))
        except Exception:
            continue
    return cleaned


def _migrate_legacy_prompt_overrides_if_needed() -> None:
    """
    One-time compatibility path:
    if prompt keys still exist in backend/infra/config/runtime_settings.json from old versions,
    move them into MySQL prompt store and then remove them from file overrides.
    """
    global _LEGACY_PROMPT_MIGRATION_DONE
    if _LEGACY_PROMPT_MIGRATION_DONE:
        return
    if not is_prompt_store_enabled():
        _LEGACY_PROMPT_MIGRATION_DONE = True
        return

    all_overrides = load_runtime_overrides(include_prompt=True)
    prompt_keys = _prompt_setting_keys()
    legacy_prompt_overrides = {
        key: value
        for key, value in all_overrides.items()
        if key in prompt_keys
    }
    if not legacy_prompt_overrides:
        _LEGACY_PROMPT_MIGRATION_DONE = True
        return

    # Persist prompt rows first. Only clean file after data is readable from DB.
    _save_prompt_overrides(
        updates=legacy_prompt_overrides,
        reset_keys=[],
        reset_all=False,
    )
    persisted = load_prompt_overrides()
    if not all(persisted.get(k) == v for k, v in legacy_prompt_overrides.items()):
        # Keep legacy data in file if MySQL write was not confirmed.
        return

    non_prompt_overrides = {
        key: value
        for key, value in all_overrides.items()
        if key not in prompt_keys
    }
    save_runtime_overrides(non_prompt_overrides)
    _LEGACY_PROMPT_MIGRATION_DONE = True


def get_runtime_overrides_path_label() -> str:
    return _prompt_store_label()


def _save_prompt_overrides(
    updates: Dict[str, Any],
    reset_keys: list[str],
    reset_all: bool,
) -> None:
    if not is_prompt_store_enabled():
        return
    conn = _connect_prompt_store()
    if conn is None:
        return
    prompt_keys = _prompt_setting_keys()
    clean_reset = [k for k in reset_keys if k in prompt_keys]
    clean_updates = {k: v for k, v in updates.items() if k in prompt_keys}
    try:
        _ensure_prompt_table(conn)
        with conn.cursor() as cur:
            if reset_all:
                cur.execute(f"DELETE FROM `{PROMPT_SETTINGS_TABLE}`")
            elif clean_reset:
                placeholders = ", ".join(["%s"] * len(clean_reset))
                cur.execute(
                    f"DELETE FROM `{PROMPT_SETTINGS_TABLE}` WHERE prompt_key IN ({placeholders})",
                    tuple(clean_reset),
                )
            for key, value in clean_updates.items():
                cur.execute(
                    f"""
                    INSERT INTO `{PROMPT_SETTINGS_TABLE}` (prompt_key, prompt_value)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE
                        prompt_value = VALUES(prompt_value),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, str(value)),
                )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_runtime_overrides(include_prompt: bool = True) -> Dict[str, Any]:
    if not RUNTIME_SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(RUNTIME_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    for key, raw in data.items():
        if key not in RUNTIME_SETTINGS_SCHEMA:
            continue
        if not include_prompt and _is_prompt_key(key):
            continue
        try:
            cleaned[key] = _coerce_value(key, raw)
        except Exception:
            continue
    return cleaned


def save_runtime_overrides(overrides: Dict[str, Any]) -> None:
    RUNTIME_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: overrides[k] for k in sorted(overrides.keys())}
    RUNTIME_SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def get_effective_runtime_settings() -> Dict[str, Any]:
    effective: Dict[str, Any] = {
        key: spec["default"] for key, spec in RUNTIME_SETTINGS_SCHEMA.items()
    }
    # 1) persisted runtime overrides managed by admin console.
    # non-prompt keys are stored in file, prompt keys are stored in MySQL table.
    # CLI / one-off benchmark runs often need to override these values via process env,
    # so env is applied last below.
    if is_prompt_store_enabled():
        _migrate_legacy_prompt_overrides_if_needed()
        effective.update(load_runtime_overrides(include_prompt=False))
        effective.update(load_prompt_overrides())
    else:
        effective.update(load_runtime_overrides(include_prompt=True))
    # 2) process env (highest priority for deployment / CLI overrides)
    for key in RUNTIME_SETTINGS_SCHEMA:
        env_raw = os.getenv(key)
        if env_raw in (None, ""):
            continue
        try:
            effective[key] = _coerce_value(key, env_raw)
        except Exception:
            # keep last good value
            continue
    return effective


def apply_runtime_settings_to_env(settings: Dict[str, Any]) -> None:
    for key, spec in RUNTIME_SETTINGS_SCHEMA.items():
        if key not in settings:
            continue
        os.environ[key] = _serialize_env_value(settings[key], spec["type"])


def initialize_runtime_settings() -> Dict[str, Any]:
    effective = get_effective_runtime_settings()
    apply_runtime_settings_to_env(effective)
    return effective


def update_runtime_settings(
    updates: Dict[str, Any] | None = None,
    reset_keys: list[str] | None = None,
    reset_all: bool = False,
) -> Dict[str, Any]:
    prompt_store_on = is_prompt_store_enabled()
    if prompt_store_on:
        _migrate_legacy_prompt_overrides_if_needed()
    overrides = load_runtime_overrides(include_prompt=(not prompt_store_on))
    prompt_updates: Dict[str, Any] = {}
    prompt_reset_keys: list[str] = []
    prompt_keys = _prompt_setting_keys()

    if reset_all:
        overrides = {}
        if prompt_store_on:
            prompt_reset_keys = sorted(prompt_keys)

    for key in reset_keys or []:
        if key not in RUNTIME_SETTINGS_SCHEMA:
            raise ValueError(f"Unsupported setting key: {key}")
        if prompt_store_on and key in prompt_keys:
            prompt_reset_keys.append(key)
            continue
        overrides.pop(key, None)

    for key, raw in (updates or {}).items():
        if key not in RUNTIME_SETTINGS_SCHEMA:
            raise ValueError(f"Unsupported setting key: {key}")
        value = _coerce_value(key, raw)
        if prompt_store_on and key in prompt_keys:
            prompt_updates[key] = value
            continue
        overrides[key] = value

    if prompt_store_on:
        # keep file clean: prompt keys are stored in MySQL only.
        for key in prompt_keys:
            overrides.pop(key, None)

    save_runtime_overrides(overrides)
    _save_prompt_overrides(
        updates=prompt_updates,
        reset_keys=prompt_reset_keys,
        reset_all=bool(reset_all),
    )
    effective = get_effective_runtime_settings()
    apply_runtime_settings_to_env(effective)
    return effective


def coerce_runtime_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, raw in (updates or {}).items():
        if key not in RUNTIME_SETTINGS_SCHEMA:
            raise ValueError(f"Unsupported setting key: {key}")
        cleaned[key] = _coerce_value(key, raw)
    return cleaned
