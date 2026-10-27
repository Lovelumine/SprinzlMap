from __future__ import annotations

from typing import Iterable


def _normalized_bases(sequence: str) -> str:
    valid_bases = set("AUGCN")
    bases: list[str] = []
    for char in sequence:
        base = char.upper().replace("T", "U")
        if base in valid_bases:
            bases.append(base)
    return "".join(bases)


def validate_alignment(target_seq: str, aligned_seq: Iterable[str] | str) -> bool:
    tgt = _normalized_bases(target_seq)
    if isinstance(aligned_seq, str):
        aligned_str = aligned_seq
    else:
        aligned_str = "".join(aligned_seq)
    ali = _normalized_bases(aligned_str)
    return tgt == ali


__all__ = ["validate_alignment"]
