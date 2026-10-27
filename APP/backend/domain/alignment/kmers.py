# pythonscript/kmers.py
from __future__ import annotations
from collections import Counter
from typing import Iterable, Dict, List, Tuple, Set

def tokenize_kmers(seq: str, k: int) -> List[str]:
    s = seq.upper().replace("T", "U")
    n = len(s)
    if n < k:
        return [s] if n > 0 else []
    return [s[i:i+k] for i in range(n - k + 1)]

def kmerset(seq: str, k: int) -> Set[str]:
    return set(tokenize_kmers(seq, k))

def overlap_score(qset: Set[str], tset: Set[str]) -> int:
    # 简单、快速：交集大小就是打分（也可换 Jaccard）
    return len(qset & tset)

def topk_by_kmer_overlap(
    query_seq: str,
    candidates: Iterable[Tuple[str, str, dict]],
    k: int,
    topn: int
) -> List[Tuple[str, str, dict, int]]:
    """
    candidates: 迭代 (seqname, flat_seq, extra) 三元组
    返回: [(seqname, flat_seq, extra, overlap_score)]，按 overlap 降序取前 topn
    """
    qset = kmerset(query_seq, k)
    scored = []
    for seqname, flat_seq, extra in candidates:
        oscore = overlap_score(qset, kmerset(flat_seq, k))
        if oscore > 0:
            scored.append((seqname, flat_seq, extra, oscore))
    # 没有 overlap 的也给一点机会（可选），这里直接保留 overlap>0 的
    scored.sort(key=lambda x: x[3], reverse=True)
    return scored[:topn]