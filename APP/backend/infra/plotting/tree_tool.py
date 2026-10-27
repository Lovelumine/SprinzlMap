#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alignment CSV → Newick tree
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Dict, List, Tuple


def _load_alignment_from_bytes(csv_bytes: bytes) -> Tuple[List[str], List[List[str]]]:
    text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    data_rows = rows[1:]
    names: List[str] = []
    seqs: List[List[str]] = []
    for row in data_rows:
        if not row:
            continue
        names.append(row[0])
        seqs.append(row[1:])
    return names, seqs


def _pairwise_distance(a: List[str], b: List[str]) -> float:
    mismatches = 0
    compared = 0
    for x, y in zip(a, b):
        if x == "-" and y == "-":
            continue
        compared += 1
        if x != y:
            mismatches += 1
    if compared == 0:
        return 0.0
    return mismatches / compared


def _newick_escape(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return "seq"
    for ch in " ,:();":
        raw = raw.replace(ch, "_")
    raw = raw.replace("'", "")
    return raw or "seq"


@dataclass
class Cluster:
    newick: str
    size: int
    height: float


def _upgma(names: List[str], seqs: List[List[str]]) -> str:
    if len(names) == 1:
        return f"{_newick_escape(names[0])};"

    clusters: Dict[str, Cluster] = {}
    for i, name in enumerate(names):
        clusters[str(i)] = Cluster(newick=_newick_escape(name), size=1, height=0.0)

    dist: Dict[Tuple[str, str], float] = {}
    keys = list(clusters.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            k1, k2 = keys[i], keys[j]
            d = _pairwise_distance(seqs[i], seqs[j])
            dist[(k1, k2)] = d

    counter = len(keys)
    while len(clusters) > 1:
        (a, b), dmin = min(dist.items(), key=lambda x: x[1])
        ca, cb = clusters[a], clusters[b]

        new_height = dmin / 2.0
        branch_a = max(new_height - ca.height, 0.0)
        branch_b = max(new_height - cb.height, 0.0)
        newick = f"({ca.newick}:{branch_a:.6f},{cb.newick}:{branch_b:.6f})"
        new_size = ca.size + cb.size
        new_key = f"u{counter}"
        counter += 1

        current_dist = dist
        # update distances to new cluster (average linkage)
        new_dist: Dict[Tuple[str, str], float] = {}
        for (k1, k2), v in current_dist.items():
            if a in (k1, k2) or b in (k1, k2):
                continue
            new_dist[(k1, k2)] = v

        for k, ck in clusters.items():
            if k in (a, b):
                continue
            da = current_dist.get((a, k)) or current_dist.get((k, a)) or 0.0
            db = current_dist.get((b, k)) or current_dist.get((k, b)) or 0.0
            dk = (da * ca.size + db * cb.size) / new_size
            key = (k, new_key) if k < new_key else (new_key, k)
            new_dist[key] = dk

        # update cluster set
        clusters.pop(a, None)
        clusters.pop(b, None)
        clusters[new_key] = Cluster(newick=newick, size=new_size, height=new_height)
        dist = new_dist

    final = next(iter(clusters.values()))
    return final.newick + ";"


def csv_bytes_to_newick(csv_bytes: bytes) -> str:
    names, seqs = _load_alignment_from_bytes(csv_bytes)
    if not names or not seqs:
        raise ValueError("Empty alignment CSV.")
    if any(len(s) != len(seqs[0]) for s in seqs):
        raise ValueError("Alignment rows have inconsistent lengths.")
    return _upgma(names, seqs)
