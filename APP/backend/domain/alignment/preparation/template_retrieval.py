"""
模块: find_best_match
功能: 给定一条输入序列，查找 data/tRNAs_flat.tsv 中与之比对得分最高的 tRNA，并返回详细信息。
    可被其他脚本通过 import 调用。接口保持不变:
    find_best_match(input_seq, tsv_path=..., match=..., mismatch=..., gap_open=..., gap_extend=...)
"""
from __future__ import annotations
import csv
import os
import pickle
import re
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from math import ceil
import time
from typing import List, Tuple, Dict, Any, Set

from ..engine.scoring import alignment_score_and_str
from ..debug_config import env_flag as _env_flag, env_int as _env_int, DEBUG_MATCH as _DEBUG_MATCH
from ..runtime_helpers import runtime_float, runtime_int, runtime_str

# --------------------------- 模块级缓存 ---------------------------
_CACHE = {
    "tsv_path": None,
    "records": None,        # 原始行(list[dict])
    "slim_rows": None,      # list[(seqname, flat_seq, extra)]
    "index": None,          # seqname -> row
    "size": 0,
}

# 为快速粗筛预留的索引缓存（bitset/inverted list 思路，类似 MySQL 中的 VARBINARY 预编码）
_FAST_CACHE: Dict[str, Any] = {
    "tsv_path": None,
    "k": None,
    "inverted": None,  # list[list[int]]，下标为编码后的 k-mer
    "size": 0,
}

_TEMPLATE_META_RE = re.compile(r"-(?P<isotype>[A-Za-z0-9]+?)(?P<anticodon>[ACGTU]{3})$")

def _load_tsv_if_needed(tsv_path: str):
    """只在路径变化或首次调用时加载 TSV 并构建 slim_rows；如有缓存则优先读缓存。"""
    global _CACHE
    if _CACHE["tsv_path"] == tsv_path and _CACHE["records"] is not None:
        return
    cache_path = Path(tsv_path).with_suffix(Path(tsv_path).suffix + ".slim.pkl")
    tsv_mtime = Path(tsv_path).stat().st_mtime if Path(tsv_path).exists() else 0.0

    # 优先尝试读取缓存（要求缓存比 TSV 新）
    if cache_path.exists() and cache_path.stat().st_mtime >= tsv_mtime:
        try:
            with cache_path.open("rb") as fin:
                data = pickle.load(fin)
            if isinstance(data, dict) and "slim_rows" in data and "records" in data:
                if "index" not in data:
                    data["index"] = {
                        row.get("seqname"): row
                        for row in data.get("records") or []
                        if row.get("seqname")
                    }
                _CACHE.update(data)
                _CACHE["tsv_path"] = tsv_path
                return
        except Exception:
            pass  # 读取失败则回退重建

    records: List[Dict[str, Any]] = []
    with open(tsv_path, 'r', encoding='utf-8') as fin:
        reader = csv.DictReader(fin, delimiter='\t')
        cols = [c for c in reader.fieldnames if c != 'seqname']
        for row in reader:
            # 预扁平化，后续无需再 join
            flat = ''.join(row[c] for c in cols)
            row['flat_seq'] = flat
            records.append(row)

    slim_rows: List[Tuple[str, str, Dict[str, Any]]] = []
    for row in records:
        seqname = row['seqname']
        flat_seq = row['flat_seq']
        extra = {k: v for k, v in row.items() if k not in ('seqname', 'flat_seq')}
        slim_rows.append((seqname, flat_seq, extra))

    _CACHE = {
        "tsv_path": tsv_path,
        "records": records,
        "slim_rows": slim_rows,
        "index": {row.get("seqname"): row for row in records if row.get("seqname")},
        "size": len(records),
    }

    # 将 slim_rows/records 落盘缓存，后续启动无需重新解析 TSV
    try:
        with cache_path.open("wb") as fout:
            pickle.dump(_CACHE, fout, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass

# --------------------------- 预筛 (k-mer) ---------------------------

def _kmerset(seq: str, k: int) -> Set[str]:
    s = seq.upper().replace('T', 'U')
    n = len(s)
    if n <= 0:
        return set()
    if n <= k:
        return {s}
    return {s[i:i+k] for i in range(n - k + 1)}

def _prefilter_params(total_records: int) -> tuple[int, int]:
    """
    返回 (k, topn)
    - k: TRNAALIGN_PREFILTER_K，默认 5
    - topn: 取前 max(200, frac*N)，但不超过 TRNAALIGN_PREFILTER_TOPN（默认 500）
    """
    k = runtime_int("TRNAALIGN_PREFILTER_K", 5, 1, 32)
    frac = runtime_float("TRNAALIGN_PREFILTER_FRAC", 0.05, 0.001, 1.0)  # 5%
    topn_abs = runtime_int("TRNAALIGN_PREFILTER_TOPN", 500, 1, 200000)
    topn_by_frac = int(max(1, total_records * frac))
    topn = min(topn_abs, max(200, topn_by_frac))
    return k, topn

def _prefilter_candidates(
    input_seq: str,
    slim_rows: List[Tuple[str, str, Dict[str, Any]]],
    total_records: int
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    用 k-mer overlap 粗筛，返回候选三元组列表 (seqname, flat_seq, extra)。
    如极端情况下 overlap=0，则回退取前 topn 个（按原顺序）。
    """
    k, topn = _prefilter_params(total_records)
    qset = _kmerset(input_seq, k)
    scored: List[Tuple[str, str, Dict[str, Any], int]] = []
    for seqname, flat_seq, extra in slim_rows:
        oscore = len(qset & _kmerset(flat_seq, k))
        if oscore > 0:
            scored.append((seqname, flat_seq, extra, oscore))

    if scored:
        scored.sort(key=lambda x: (-x[3], x[0]))
        return [(s, f, e) for (s, f, e, _o) in scored[:topn]]

    # 没有 overlap（非常罕见），保底：取前 topn 条
    return slim_rows[:topn]

# --------------------------- 快速粗筛（bitset/倒排表） ---------------------------

_BASE2INT = {"A": 0, "C": 1, "G": 2, "U": 3}

def _dprint(msg: str):
    if _DEBUG_MATCH:
        print(msg)


def _norm_isotype_token(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(raw or "").strip().lower())


def _norm_anticodon_token(raw: str) -> str:
    return str(raw or "").strip().upper().replace("T", "U")


def _parse_template_seqname_metadata(seqname: str) -> tuple[str, str]:
    text = str(seqname or "").strip()
    m = _TEMPLATE_META_RE.search(text)
    if not m:
        return "", ""
    return _norm_isotype_token(m.group("isotype")), _norm_anticodon_token(m.group("anticodon"))


def _template_match_bonus(
    seqname: str,
    *,
    preferred_isotype: str,
    preferred_anticodon: str,
    anticodon_bonus: float,
    isotype_bonus: float,
) -> tuple[float, int, int]:
    tmpl_isotype, tmpl_anticodon = _parse_template_seqname_metadata(seqname)
    anti_match = int(bool(preferred_anticodon) and tmpl_anticodon == preferred_anticodon)
    iso_match = int(bool(preferred_isotype) and tmpl_isotype == preferred_isotype)
    bonus = anti_match * anticodon_bonus + iso_match * isotype_bonus
    return bonus, anti_match, iso_match


def _family_match_rank(anti_match: int, iso_match: int) -> int:
    if anti_match and iso_match:
        return 3
    if anti_match:
        return 2
    if iso_match:
        return 1
    return 0


def _family_match_label(rank: int) -> str:
    return {
        3: "both",
        2: "anticodon_only",
        1: "isotype_only",
        0: "none",
    }.get(int(rank or 0), "none")


def _template_match_features(
    seqname: str,
    *,
    preferred_isotype: str,
    preferred_anticodon: str,
    anticodon_bonus: float,
    isotype_bonus: float,
) -> Dict[str, Any]:
    tmpl_isotype, tmpl_anticodon = _parse_template_seqname_metadata(seqname)
    anti_match = int(bool(preferred_anticodon) and tmpl_anticodon == preferred_anticodon)
    iso_match = int(bool(preferred_isotype) and tmpl_isotype == preferred_isotype)
    family_rank = _family_match_rank(anti_match, iso_match)
    return {
        "template_isotype": tmpl_isotype,
        "template_anticodon": tmpl_anticodon,
        "anticodon_match": anti_match,
        "isotype_match": iso_match,
        "family_match_rank": family_rank,
        "family_match_label": _family_match_label(family_rank),
        "metadata_bonus": anti_match * anticodon_bonus + iso_match * isotype_bonus,
    }


def _non_gap_len(seq: str) -> int:
    return sum(1 for ch in str(seq or "") if ch != "-")


def _exact_family_rescue_candidates(
    input_seq: str,
    slim_rows: List[Tuple[str, str, Dict[str, Any]]],
    *,
    preferred_isotype: str,
    preferred_anticodon: str,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    if not (preferred_isotype and preferred_anticodon):
        return []
    rescue_cap = runtime_int("TRNAALIGN_TEMPLATE_EXACT_FAMILY_RESCUE_CAP", 96, 1, 4096)
    want_len = len(str(input_seq or "").upper().replace("T", "U").replace("-", ""))
    matches: List[Tuple[int, int, str, str, Dict[str, Any]]] = []
    for seqname, flat_seq, extra in slim_rows:
        tmpl_isotype, tmpl_anticodon = _parse_template_seqname_metadata(seqname)
        if tmpl_isotype != preferred_isotype or tmpl_anticodon != preferred_anticodon:
            continue
        length_gap = abs(_non_gap_len(flat_seq) - want_len)
        matches.append((length_gap, -_non_gap_len(flat_seq), seqname, flat_seq, extra))
    if not matches:
        return []
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(seqname, flat_seq, extra) for _gap, _neg_len, seqname, flat_seq, extra in matches[:rescue_cap]]


def _rerank_scored_candidates(
    scored_rows: List[Tuple[float, str, str, Dict[str, Any], str]],
    *,
    preferred_isotype: str,
    preferred_anticodon: str,
) -> Tuple[float, str, str, Dict[str, Any], str] | None:
    if not scored_rows:
        return None

    raw_best = min(scored_rows, key=lambda row: (-row[0], row[1]))
    if not (preferred_isotype or preferred_anticodon):
        return raw_best

    rerank_margin = runtime_float("TRNAALIGN_TEMPLATE_RERANK_MARGIN", 4.0, 0.0, 50.0)
    anticodon_bonus = runtime_float("TRNAALIGN_TEMPLATE_ANTICODON_BONUS", 4.0, 0.0, 20.0)
    isotype_bonus = runtime_float("TRNAALIGN_TEMPLATE_ISOTYPE_BONUS", 2.0, 0.0, 20.0)

    shortlist = [row for row in scored_rows if (raw_best[0] - row[0]) <= rerank_margin]
    if len(shortlist) <= 1:
        return raw_best

    best = raw_best
    best_key = None
    any_bonus = False
    for row in shortlist:
        features = _template_match_features(
            row[1],
            preferred_isotype=preferred_isotype,
            preferred_anticodon=preferred_anticodon,
            anticodon_bonus=anticodon_bonus,
            isotype_bonus=isotype_bonus,
        )
        bonus = float(features["metadata_bonus"])
        anti_match = int(features["anticodon_match"])
        iso_match = int(features["isotype_match"])
        any_bonus = any_bonus or bonus > 0
        key = (
            -(row[0] + bonus),
            -anti_match,
            -iso_match,
            -row[0],
            row[1],
        )
        if best_key is None or key < best_key:
            best = row
            best_key = key

    if not any_bonus:
        return raw_best

    if best[1] != raw_best[1]:
        _dprint(
            "[find_best_match] metadata rerank switched template "
            f"{raw_best[1]} (raw={raw_best[0]:.2f}) -> {best[1]} (raw={best[0]:.2f}) "
            f"with hints isotype={preferred_isotype or '-'} anticodon={preferred_anticodon or '-'}"
        )
    return best

def _encode_kmer(kmer: str) -> int | None:
    """把 k-mer 编码成 base4 整数，遇到非 A/C/G/U 返回 None。"""
    val = 0
    for ch in kmer:
        v = _BASE2INT.get(ch)
        if v is None:
            return None
        val = (val << 2) | v
    return val

def _ensure_fast_index(tsv_path: str,
                       slim_rows: List[Tuple[str, str, Dict[str, Any]]],
                       k: int):
    """构建/复用倒排表，避免每次调用重复预处理；优先尝试缓存。"""
    global _FAST_CACHE
    if (_FAST_CACHE["tsv_path"] == tsv_path and
            _FAST_CACHE["k"] == k and
            _FAST_CACHE.get("inverted")):
        return

    cache_path = Path(tsv_path).with_suffix(Path(tsv_path).suffix + f".fastk{k}.pkl")
    tsv_mtime = Path(tsv_path).stat().st_mtime if Path(tsv_path).exists() else 0.0
    if cache_path.exists() and cache_path.stat().st_mtime >= tsv_mtime:
        try:
            with cache_path.open("rb") as fin:
                data = pickle.load(fin)
            if isinstance(data, dict) and data.get("k") == k and data.get("inverted"):
                _FAST_CACHE = data
                _FAST_CACHE["tsv_path"] = tsv_path
                return
        except Exception:
            pass  # 读取失败则重建

    vocab = 4 ** k
    inverted: List[List[int]] = [[] for _ in range(vocab)]

    for idx, (_seqname, flat_seq, _extra) in enumerate(slim_rows):
        s = flat_seq.upper().replace('T', 'U')
        n = len(s)
        if n < k:
            continue
        seen = set()
        for i in range(n - k + 1):
            enc = _encode_kmer(s[i:i + k])
            if enc is None or enc in seen:
                continue
            seen.add(enc)
            inverted[enc].append(idx)

    _FAST_CACHE = {
        "tsv_path": tsv_path,
        "k": k,
        "inverted": inverted,
        "size": len(slim_rows),
    }

    try:
        with cache_path.open("wb") as fout:
            pickle.dump(_FAST_CACHE, fout, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass

def _fast_prefilter_candidates(
    input_seq: str,
    slim_rows: List[Tuple[str, str, Dict[str, Any]]],
    total_records: int,
    tsv_path: str
) -> List[Tuple[str, str, Dict[str, Any]]] | None:
    """
    参考数据库 bitset 的思路：k-mer 倒排表 + 命中计数。
    仅返回 topN 候选，未命中时返回 None 以便回退到旧逻辑。
    """
    k = _env_int("TRNAALIGN_FAST_K", 6)
    # 防止内存暴涨，k 约束在 [3, 8]
    k = max(3, min(8, k))
    topn = _env_int("TRNAALIGN_FAST_TOPN", 120)
    topn = max(50, min(topn, total_records))

    _ensure_fast_index(tsv_path, slim_rows, k)
    inverted: List[List[int]] = _FAST_CACHE["inverted"]  # type: ignore
    if not inverted:
        return None

    s = input_seq.upper().replace("T", "U")
    n = len(s)
    if n < k:
        return None

    qset: Set[int] = set()
    for i in range(n - k + 1):
        enc = _encode_kmer(s[i:i + k])
        if enc is not None:
            qset.add(enc)

    if not qset:
        return None

    # 计数命中次数（相当于交集大小），避免扫描全库
    counts: Dict[int, int] = defaultdict(int)
    for enc in qset:
        posting = inverted[enc]
        for tpl_id in posting:
            counts[tpl_id] += 1

    if not counts:
        return None

    ranked = sorted(
        counts.items(),
        key=lambda kv: (-kv[1], str(slim_rows[kv[0]][0])),
    )
    top_ids = [idx for idx, _hit in ranked[:topn]]
    return [slim_rows[i] for i in top_ids]

# --------------------------- 并发策略 ---------------------------

def _decide_workers(total_tasks: int) -> int:
    """
    自适应线程数：
      - TRNAALIGN_WORKERS 优先；
      - 默认不超过 CPU 核数，可用 TRNAALIGN_MAX_WORKERS 设上限。
    """
    env = runtime_str("TRNAALIGN_WORKERS", "")
    if env:
        try:
            v = int(env)
            if v >= 1:
                return min(v, total_tasks) if total_tasks > 0 else 1
        except Exception:
            pass
    cpu = os.cpu_count() or 1
    default_cap = min(cpu, 64)  # 默认封顶 64，避免过度调度；可用 TRNAALIGN_MAX_WORKERS 调高或调低
    cap = runtime_int("TRNAALIGN_MAX_WORKERS", default_cap, 1, 4096)
    cap = max(1, cap)
    return max(1, min(cpu, total_tasks, cap))

# --------------------------- 打分包装 ---------------------------

def _score_one(pack, input_seq, match, mismatch, gap_open, gap_extend):
    seqname, flat_seq, extra = pack
    score, aln = alignment_score_and_str(
        input_seq,
        flat_seq,
        match,
        mismatch,
        gap_open,
        gap_extend,
        use_parasail=True,
    )
    return score, seqname, flat_seq, extra, aln


def _score_ranked_matches(
    input_seq: str,
    tsv_path: str,
    match: float,
    mismatch: float,
    gap_open: float,
    gap_extend: float,
    preferred_isotype: str,
    preferred_anticodon: str,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    _load_tsv_if_needed(tsv_path)
    slim_rows = _CACHE["slim_rows"]
    total = _CACHE["size"]
    if not slim_rows or total == 0:
        return []

    candidates = None
    used_fast = False
    if _env_flag("TRNAALIGN_FAST_PREFILTER", True):
        candidates = _fast_prefilter_candidates(input_seq, slim_rows, total, tsv_path)
        used_fast = candidates is not None
    if not candidates:
        candidates = _prefilter_candidates(input_seq, slim_rows, total)
        used_fast = False

    score_cap = _env_int("TRNAALIGN_SCORE_TOPN", 0)
    if score_cap > 0:
        candidates = candidates[:min(score_cap, len(candidates))]

    if preferred_isotype and preferred_anticodon:
        exact_family_candidates = _exact_family_rescue_candidates(
            input_seq=input_seq,
            slim_rows=slim_rows,
            preferred_isotype=preferred_isotype,
            preferred_anticodon=preferred_anticodon,
        )
        if exact_family_candidates:
            seen_names = {seqname for seqname, _flat_seq, _extra in candidates}
            added = 0
            for pack in exact_family_candidates:
                seqname = pack[0]
                if seqname in seen_names:
                    continue
                candidates.append(pack)
                seen_names.add(seqname)
                added += 1
            if added and _DEBUG_MATCH:
                _dprint(
                    f"[find_top_matches] exact_family_rescue added={added} "
                    f"isotype={preferred_isotype or '-'} anticodon={preferred_anticodon or '-'}"
                )

    n_tasks = len(candidates)
    n_workers = _decide_workers(n_tasks)
    approx = ceil(n_tasks / n_workers) if n_workers else n_tasks

    align_backend = runtime_str("TRNAALIGN_BACKEND_LOG", "")
    if align_backend and _DEBUG_MATCH:
        _dprint(f"[find_top_matches] backend_hint={align_backend}")
    _dprint(
        f"[find_top_matches] total={total}, prefilter_candidates={n_tasks}, "
        f"workers={n_workers}, approx_per_worker={approx}, fast_prefilter={used_fast}"
    )

    preferred_isotype = _norm_isotype_token(preferred_isotype)
    preferred_anticodon = _norm_anticodon_token(preferred_anticodon)
    scored_rows: List[Tuple[float, str, str, Dict[str, Any], str]] = []
    if n_workers <= 1 or n_tasks <= 1:
        for pack in candidates:
            scored_rows.append(_score_one(pack, input_seq, match, mismatch, gap_open, gap_extend))
    else:
        use_proc = _env_flag("TRNAALIGN_USE_PROCESS", False)
        ExecutorCls = ProcessPoolExecutor if use_proc else ThreadPoolExecutor
        try:
            with ExecutorCls(max_workers=n_workers) as ex:
                futs = [
                    ex.submit(_score_one, pack, input_seq, match, mismatch, gap_open, gap_extend)
                    for pack in candidates
                ]
                for fut in as_completed(futs):
                    scored_rows.append(fut.result())
        except PermissionError:
            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                futs = [
                    ex.submit(_score_one, pack, input_seq, match, mismatch, gap_open, gap_extend)
                    for pack in candidates
                ]
                for fut in as_completed(futs):
                    scored_rows.append(fut.result())

    if not scored_rows:
        return []

    rerank_margin = runtime_float("TRNAALIGN_TEMPLATE_RERANK_MARGIN", 4.0, 0.0, 50.0)
    family_rescue_margin = runtime_float("TRNAALIGN_TEMPLATE_FAMILY_RESCUE_MARGIN", 8.0, 0.0, 50.0)
    exact_family_rescue_margin = runtime_float("TRNAALIGN_TEMPLATE_EXACT_FAMILY_RESCUE_MARGIN", 16.0, 0.0, 80.0)
    anticodon_bonus = runtime_float("TRNAALIGN_TEMPLATE_ANTICODON_BONUS", 4.0, 0.0, 20.0)
    isotype_bonus = runtime_float("TRNAALIGN_TEMPLATE_ISOTYPE_BONUS", 2.0, 0.0, 20.0)
    raw_best_score = max(row[0] for row in scored_rows)
    ranked: List[Dict[str, Any]] = []
    for score, seqname, flat_seq, extra, aln in scored_rows:
        in_shortlist = (raw_best_score - score) <= rerank_margin
        features: Dict[str, Any] = {
            "template_isotype": "",
            "template_anticodon": "",
            "anticodon_match": 0,
            "isotype_match": 0,
            "family_match_rank": 0,
            "family_match_label": "none",
            "metadata_bonus": 0.0,
        }
        if preferred_isotype or preferred_anticodon:
            features = _template_match_features(
                seqname,
                preferred_isotype=preferred_isotype,
                preferred_anticodon=preferred_anticodon,
                anticodon_bonus=anticodon_bonus,
                isotype_bonus=isotype_bonus,
            )
            if not in_shortlist:
                features["metadata_bonus"] = 0.0
        item: Dict[str, Any] = {
            "seqname": seqname,
            "flat_seq": flat_seq,
            "score": score,
            "alignment": aln,
            "rerank_score": score + float(features["metadata_bonus"]),
            "family_rescue_selected": False,
            "family_rescue_candidate": False,
        }
        item.update(features)
        item.update(extra)
        ranked.append(item)

    if preferred_isotype or preferred_anticodon:
        raw_best_item = min(
            ranked,
            key=lambda row: (
                -float(row.get("score", float("-inf"))),
                str(row.get("seqname") or ""),
            ),
        )
        raw_best_rank = int(raw_best_item.get("family_match_rank") or 0)
        rescue_pool = []
        for row in ranked:
            gap = raw_best_score - float(row.get("score", 0.0) or 0.0)
            family_rank = int(row.get("family_match_rank") or 0)
            allowed_gap = family_rescue_margin
            if family_rank >= 3:
                allowed_gap = max(allowed_gap, exact_family_rescue_margin)
            if gap <= allowed_gap:
                rescue_pool.append(row)
        for row in rescue_pool:
            row["family_rescue_candidate"] = bool(int(row.get("family_match_rank") or 0) >= 2)
        if rescue_pool:
            best_family_rank = max(int(row.get("family_match_rank") or 0) for row in rescue_pool)
            if best_family_rank > raw_best_rank and best_family_rank >= 2:
                rescue_choice = min(
                    (
                        row for row in rescue_pool
                        if int(row.get("family_match_rank") or 0) == best_family_rank
                    ),
                    key=lambda row: (
                        -float(row.get("rerank_score", row.get("score", 0.0)) or 0.0),
                        -float(row.get("score", 0.0) or 0.0),
                        -int(row.get("anticodon_match") or 0),
                        -int(row.get("isotype_match") or 0),
                        str(row.get("seqname") or ""),
                    ),
                )
                rescue_choice["family_rescue_selected"] = True
                rescue_choice["rerank_score"] = max(
                    float(rescue_choice.get("rerank_score", rescue_choice.get("score", 0.0)) or 0.0),
                    raw_best_score + 0.01,
                )

    ranked.sort(
        key=lambda row: (
            -int(bool(row.get("family_rescue_selected"))),
            -float(row.get("rerank_score", row.get("score", 0.0)) or 0.0),
            -int(row.get("family_match_rank", 0) or 0),
            -int(row.get("anticodon_match", 0) or 0),
            -int(row.get("isotype_match", 0) or 0),
            -float(row.get("score", 0.0) or 0.0),
            str(row.get("seqname") or ""),
        ),
    )
    for idx, item in enumerate(ranked, start=1):
        item["rank"] = idx

    if limit and limit > 0:
        return ranked[:limit]
    return ranked

# --------------------------- 主函数（接口不变） ---------------------------

def find_best_match(input_seq: str,
                    tsv_path: str = os.path.join('data', 'tRNAs_flat.tsv'),
                    match: float = 2.0,
                    mismatch: float = -1.0,
                    gap_open: float = -2.0,
                    gap_extend: float = -0.5,
                    preferred_isotype: str = "",
                    preferred_anticodon: str = ""):
    """
    流程：
      1) 模块级缓存加载 TSV，构建 slim_rows（只做一次）
      2) k-mer 粗筛 → 大幅缩小候选集合
      3) 对候选做准确打分（edlib/DP），线程并发精排
      4) 返回得分最高的记录（含原 TSV 其它字段）
    """
    ranked = _score_ranked_matches(
        input_seq=input_seq,
        tsv_path=tsv_path,
        match=match,
        mismatch=mismatch,
        gap_open=gap_open,
        gap_extend=gap_extend,
        preferred_isotype=preferred_isotype,
        preferred_anticodon=preferred_anticodon,
        limit=1,
    )
    if not ranked:
        return None
    return ranked[0]


def find_top_matches(
    input_seq: str,
    tsv_path: str = os.path.join('data', 'tRNAs_flat.tsv'),
    match: float = 2.0,
    mismatch: float = -1.0,
    gap_open: float = -2.0,
    gap_extend: float = -0.5,
    preferred_isotype: str = "",
    preferred_anticodon: str = "",
    limit: int = 5,
) -> List[Dict[str, Any]]:
    return _score_ranked_matches(
        input_seq=input_seq,
        tsv_path=tsv_path,
        match=match,
        mismatch=mismatch,
        gap_open=gap_open,
        gap_extend=gap_extend,
        preferred_isotype=preferred_isotype,
        preferred_anticodon=preferred_anticodon,
        limit=limit,
    )


def list_template_names(tsv_path: str = os.path.join('data', 'tRNAs_flat.tsv')) -> List[str]:
    """Return all template seqname values from the TSV (cached)."""
    _load_tsv_if_needed(tsv_path)
    records = _CACHE.get("records") or []
    return [row.get("seqname") for row in records if row.get("seqname")]


def list_template_records(
    tsv_path: str = os.path.join('data', 'tRNAs_flat.tsv'),
    q: str | None = None,
    limit: int | None = None
) -> List[Dict[str, Any]]:
    """
    Return template metadata records for UI selection.
    Each record contains: seqname, length, gc.
    """
    _load_tsv_if_needed(tsv_path)
    records = _CACHE.get("records") or []
    q_lower = (q or "").strip().lower()
    results: List[Dict[str, Any]] = []
    for row in records:
        seqname = row.get("seqname")
        if not seqname:
            continue
        if q_lower and q_lower not in seqname.lower():
            continue
        flat_seq = row.get("flat_seq") or ""
        length = len(flat_seq)
        if length:
            gc = sum(1 for ch in flat_seq.upper() if ch in ("G", "C"))
            gc_pct = int(round(gc * 100 / max(1, length)))
        else:
            gc_pct = 0
        results.append(
            {
                "seqname": seqname,
                "length": length,
                "gc": gc_pct,
            }
        )
        if limit and limit > 0 and len(results) >= limit:
            break
    return results


def get_template_by_name(
    template_name: str,
    tsv_path: str = os.path.join('data', 'tRNAs_flat.tsv'),
    preferred_isotype: str = "",
    preferred_anticodon: str = "",
) -> Dict[str, Any] | None:
    """Fetch a template row by seqname (cached), returning the same shape as find_best_match."""
    if not template_name:
        return None
    _load_tsv_if_needed(tsv_path)
    index = _CACHE.get("index") or {}
    row = index.get(template_name)
    if not row:
        return None
    result = dict(row)
    if "flat_seq" not in result:
        # Fallback: build flat_seq from the non-seqname columns
        number_columns = [k for k in result.keys() if k not in ("seqname", "flat_seq")]
        result["flat_seq"] = "".join((result.get(k, "") or "") for k in number_columns)
    result.setdefault("score", None)
    result.setdefault("alignment", None)
    features = _template_match_features(
        str(result.get("seqname") or ""),
        preferred_isotype=_norm_isotype_token(preferred_isotype),
        preferred_anticodon=_norm_anticodon_token(preferred_anticodon),
        anticodon_bonus=runtime_float("TRNAALIGN_TEMPLATE_ANTICODON_BONUS", 4.0, 0.0, 20.0),
        isotype_bonus=runtime_float("TRNAALIGN_TEMPLATE_ISOTYPE_BONUS", 2.0, 0.0, 20.0),
    )
    result.update(features)
    result.setdefault("rerank_score", result.get("score"))
    result.setdefault("family_rescue_selected", False)
    result.setdefault("family_rescue_candidate", bool(int(result.get("family_match_rank") or 0) >= 2))
    return result


if __name__ == '__main__':
    seq = "GGUCUCGUGGCCCAAUGGUUAAGGCGCUUGACUACGGAUCAAGAGAUUCCAGGUUCGACUCCUGGCGGGAUCG"
    t0 = time.time()
    info = find_best_match(seq, tsv_path='data/tRNAs_flat.tsv')
    elapsed = time.time() - t0
    if info is None:
        print('未找到匹配。')
    else:
        print(f"最佳匹配 seqname: {info['seqname']}")
        print(f"匹配序列    : {info['flat_seq']}")
        print(f"比对分数    : {info['score']}")
        print("对齐结果:\n", info['alignment'])
        print("其他字段:")
        for k, v in info.items():
            if k in ('seqname', 'flat_seq', 'score', 'alignment'):
                continue
            print(f"  {k}: {v}")
        print(f"耗时: {elapsed:.3f} 秒")
