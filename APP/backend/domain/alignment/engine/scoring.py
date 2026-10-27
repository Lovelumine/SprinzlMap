# pythonscript/alignment_score.py
from importlib.metadata import PackageNotFoundError, version
import re

from Bio.Align import PairwiseAligner

from ..debug_config import env_flag as _env_flag

# ---------------- debug 控制 ----------------
DEBUG = _env_flag("TRNAALIGN_BACKEND_LOG", False)
_LAST_BACKEND = None
PARASAIL_BACKEND_NAME = "parasail.nw_trace_scan_16"


def required_alignment_backend_provenance() -> dict[str, object]:
    try:
        package_version = version("parasail")
        available = True
    except PackageNotFoundError:
        package_version = ""
        available = False
    return {
        "name": PARASAIL_BACKEND_NAME,
        "package": "parasail",
        "version": package_version,
        "required": True,
        "available": available,
    }

def _log_once_backend(backend: str):
    """只在后端发生变化时打印一次"""
    global _LAST_BACKEND
    if DEBUG and backend != _LAST_BACKEND:
        print(f"[alignment_score] backend={backend}")
        _LAST_BACKEND = backend

def _log(msg: str):
    if DEBUG:
        print(f"[alignment_score] {msg}")

# ---------------- util: safe set & tail fix ----------------

def _safe_set(obj, name, value) -> bool:
    try:
        setattr(obj, name, value)
        return True
    except Exception:
        return False

def _prefer_template_right_end_gaps(aligner: PairwiseAligner):
    """端点缺口偏置"""
    ok = False
    ok |= _safe_set(aligner, "query_left_gap_score",  -1e6)
    ok |= _safe_set(aligner, "query_right_gap_score",  0.0)
    _safe_set(aligner, "target_left_gap_score",  0.0)
    _safe_set(aligner, "target_right_gap_score", 0.0)
    if not ok:
        _safe_set(aligner, "query_end_gap_score", 0.0)

def _shift_tail_gaps_after_CCA(flat_target: str, flat_template: str):
    """---CCA → CCA---"""
    m = re.search(r'(-+)(CCA)$', flat_template)
    if not m:
        return flat_target, flat_template
    gaps = len(m.group(1))
    tail_len = gaps + 3
    if tail_len > len(flat_template) or tail_len > len(flat_target):
        return flat_target, flat_template

    t_tail = flat_target[-tail_len:]
    q_tail = flat_template[-tail_len:]
    t_tail_new = t_tail[gaps:] + t_tail[:gaps]
    q_tail_new = q_tail[gaps:] + q_tail[:gaps]
    return flat_target[:-tail_len] + t_tail_new, flat_template[:-tail_len] + q_tail_new


# -------- 新增：目标序列 -XCCA → XCCA --------
def _fix_target_tail_minus_xcca(flat_target: str, flat_template: str):
    """
    如果目标行（target）尾部形如 '-XCCA' (X∈{AUGCN})，改成 'XCCA'。
    模板行保持不动。
    """
    if len(flat_target) < 5:
        return flat_target, flat_template

    tail_tgt = flat_target[-5:]
    if re.fullmatch(r'-[ACGUN]CCA', tail_tgt):
        # 去掉最前面的 '-'，只保留后 4 个字符
        new_tail_tgt = tail_tgt[1:]
        return flat_target[:-5] + new_tail_tgt, flat_template
    return flat_target, flat_template


# ---------------- fast path via edlib (optional) ----------------

def _expand_by_cigar(q: str, t: str, cigar: str) -> tuple[str, str]:
    """展开 CIGAR"""
    qi = ti = 0
    qa, ta = [], []
    num = ""

    def flush(n: int, op: str):
        nonlocal qi, ti
        if n <= 0:
            return
        if op == "M":
            qa.extend(q[qi:qi+n]); ta.extend(t[ti:ti+n])
            qi += n; ti += n
        elif op == "I":
            qa.extend(q[qi:qi+n]); ta.extend("-" * n)
            qi += n
        elif op == "D":
            qa.extend("-" * n); ta.extend(t[ti:ti+n])
            ti += n
        else:  # '=' 或 'X'
            qa.extend(q[qi:qi+n]); ta.extend(t[ti:ti+n])
            qi += n; ti += n

    for ch in cigar:
        if ch.isdigit():
            num += ch
        else:
            flush(int(num or "1"), ch)
            num = ""
    return "".join(qa), "".join(ta)


# ---------------- main API ----------------

def alignment_score_and_str(
    seq1: str, seq2: str,
    match: float, mismatch: float,
    gap_open: float, gap_extend: float,
    *,
    use_edlib: bool | None = None,
    use_parasail: bool | None = None
):
    """
    全局对齐，返回 (score, flattened_alignment)。

    参数:
        use_edlib:  是否使用 edlib 快速路径。
                    - None (默认): 读取环境变量 ALIGN_USE_EDLIB（默认开启）
                    - True:       使用 edlib（线性缺口路径 + 你自己的仿射重计分）
                    - False:      跳过 edlib，直接用 Biopython PairwiseAligner（仿射缺口最优）
        use_parasail: 是否使用 parasail SIMD 快速路径（优先于 edlib）。
                      - None (默认): 读取环境变量 ALIGN_USE_PARASAIL（默认开启）

    环境变量:
        ALIGN_USE_EDLIB=0/false/False/off -> 关闭 edlib
        其它或未设置 -> 开启 edlib
        ALIGN_USE_PARASAIL 同上，控制 parasail
    """
    require_parasail = use_parasail is True

    # 解析 parasail 开关（函数参数优先，其次环境变量，默认开启）
    if use_parasail is None:
        vp = "1" if _env_flag("ALIGN_USE_PARASAIL", True) else "0"
        use_parasail = vp not in ("0", "false", "off", "no")

    # 解析总开关（函数参数优先，其次环境变量，默认开启）
    if use_edlib is None:
        v = "1" if _env_flag("ALIGN_USE_EDLIB", True) else "0"
        use_edlib = v not in ("0", "false", "off", "no")

    # ---------- fast path: parasail ----------
    if use_parasail:
        try:
            import parasail
            # parasail 仅接受整数评分，放大 10 以保留 0.5
            scale = 10.0
            m_int = int(round(match * scale))
            mm_int = int(round(mismatch * scale))
            open_int = int(round(abs(gap_open) * scale))
            extend_int = int(round(abs(gap_extend) * scale))
            matrix = parasail.matrix_create("ACGUTN", m_int, mm_int)
            res = parasail.nw_trace_scan_16(seq1, seq2, open_int, extend_int, matrix)
            tb = res.traceback
            q_aln, t_aln = tb.query, tb.ref  # query=seq1, ref=seq2

            # 末端修正
            q_aln, t_aln = _shift_tail_gaps_after_CCA(q_aln, t_aln)
            q_aln, t_aln = _fix_target_tail_minus_xcca(q_aln, t_aln)

            L = min(len(q_aln), len(t_aln))
            match_line = ''.join('|' if q_aln[i] == t_aln[i] else ' ' for i in range(L))
            _log_once_backend("parasail")
            return res.score / scale, f"target {q_aln}\n{match_line}\nquery  {t_aln}"
        except Exception as exc:
            if require_parasail:
                raise RuntimeError(
                    f"Required alignment backend {PARASAIL_BACKEND_NAME} is unavailable or failed"
                ) from exc
            # Compatibility callers that did not require a backend may still fall back.

    # ---------- fast path: edlib ----------
    if use_edlib:
        try:
            import edlib
            res = edlib.align(seq1, seq2, mode="NW", task="path")
            cigar = res.get("cigar")
            if cigar:
                _log_once_backend("edlib")
                q_aln, t_aln = _expand_by_cigar(seq1, seq2, cigar)

                # 打分（按仿射参数重计）
                score = 0.0
                prev_gap_q = prev_gap_t = False
                for qc, tc in zip(q_aln, t_aln):
                    if qc != "-" and tc != "-":
                        score += match if qc == tc else mismatch
                        prev_gap_q = prev_gap_t = False
                    elif qc == "-" and tc != "-":
                        score += (gap_extend if prev_gap_q else gap_open)
                        prev_gap_q, prev_gap_t = True, False
                    elif qc != "-" and tc == "-":
                        score += (gap_extend if prev_gap_t else gap_open)
                        prev_gap_q, prev_gap_t = False, True

                # 末端修正
                q_aln, t_aln = _shift_tail_gaps_after_CCA(q_aln, t_aln)
                q_aln, t_aln = _fix_target_tail_minus_xcca(q_aln, t_aln)

                L = min(len(q_aln), len(t_aln))
                match_line = ''.join('|' if q_aln[i] == t_aln[i] else ' ' for i in range(L))
                _log_once_backend("edlib")
                return score, f"target {q_aln}\n{match_line}\nquery  {t_aln}"
        except Exception:
            # 静默降级到 Biopython
            pass

    # ---------- fallback: Biopython ----------
    aligner = PairwiseAligner()
    aligner.mode = 'global'
    aligner.match_score = match
    aligner.mismatch_score = mismatch
    aligner.open_gap_score = gap_open
    aligner.extend_gap_score = gap_extend
    _prefer_template_right_end_gaps(aligner)

    alns = aligner.align(seq1, seq2)
    if len(alns) == 0:
        return 0.0, ''

    _log_once_backend("biopython")
    best = alns[0]
# 某些版本/类型存根下没有 .score；用 getattr 安全获取，失败则用 aligner.score 重算
    _score_attr = getattr(best, "score", None)
    try:
        score = float(_score_attr) if _score_attr is not None else aligner.score(seq1, seq2)
    except Exception:
    # 万一 _score_attr 不是数字或其他异常，再兜底
        score = aligner.score(seq1, seq2)
    # rebuild
    a_blocks, b_blocks = best.aligned
    i1 = i2 = 0
    q_parts, t_parts = [], []
    for (s1, e1), (s2, e2) in zip(a_blocks, b_blocks):
        if s1 > i1 and s2 == i2:
            q_parts.append(seq1[i1:s1]); t_parts.append('-' * (s1 - i1))
        elif s2 > i2 and s1 == i1:
            q_parts.append('-' * (s2 - i2)); t_parts.append(seq2[i2:s2])
        elif s1 > i1 and s2 > i2:
            step = min(s1 - i1, s2 - i2)
            q_parts.append(seq1[i1:i1+step]); t_parts.append(seq2[i2:i2+step])
        q_parts.append(seq1[s1:e1]); t_parts.append(seq2[s2:e2])
        i1, i2 = e1, e2

    if i1 < len(seq1):
        q_parts.append(seq1[i1:]); t_parts.append('-' * (len(seq1) - i1))
    if i2 < len(seq2):
        q_parts.append('-' * (len(seq2) - i2)); t_parts.append(seq2[i2:])

    q_aln, t_aln = ''.join(q_parts), ''.join(t_parts)

    # 末端修正
    q_aln, t_aln = _shift_tail_gaps_after_CCA(q_aln, t_aln)
    q_aln, t_aln = _fix_target_tail_minus_xcca(q_aln, t_aln)

    L = min(len(q_aln), len(t_aln))
    match_line = ''.join('|' if q_aln[i] == t_aln[i] else ' ' for i in range(L))
    return score, f"target {q_aln}\n{match_line}\nquery  {t_aln}"

# ---------------- self test ----------------
if __name__ == "__main__":
    tests = [
        (
            "GGGCUCGUAGAUCAGCGGUAGAUCGCUUCCUUCGCAAGGAAGAGGCCCUGGGUUCAAAUCCCAGCGAGUCCACCA",
            "-GGGCUCGUAGAUCAGC--GGU--AGAUCGCUUCCUUCGCAAGGAAGAG-------------------GCCCUGGGUUCAAAUCCCAGCGAGUCCA",
            2, -1, -2, -0.5
        ),
        (
            "GGUCUCGUGGGGGGGGCCCAAUGGUUAAGGCGCUUGACUACGGAUCAAGAGAUUCCAGGUUCGACUCCUGGCGGGAUCG",
            "-GGUCUCGUGGCCCAAU--GGUU-AAGGCGCUUGACUACGGAUCAAGAG-------------------AUUCCAGGUUCGACUCCUGGCGGGAUCG",
            2, -1, -2, -0.5
        ),
        ("AAAA", "TTTT", 1, -1, -2, -0.5),
        ("ACGTA", "ACGT", 2, -1, -2, -0.5),
        ("ACGT", "ACGTA", 2, -1, -2, -0.5),
    ]
    for seq1, seq2, m, mm, go, ge in tests:
        score, aln = alignment_score_and_str(seq1, seq2, m, mm, go, ge)
        print(f"Seq1: {seq1}\nSeq2: {seq2}\nScore: {score}\n{aln}\n" + "-"*40)
