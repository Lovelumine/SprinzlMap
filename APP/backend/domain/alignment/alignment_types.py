from __future__ import annotations

from typing import Any, Dict, List, TypedDict


AlignmentTokens = List[str]
AlignmentLines = List[AlignmentTokens]


class AlignmentBlock(TypedDict, total=False):
    region: str
    lines: AlignmentLines
    meta: Dict[str, Any]
