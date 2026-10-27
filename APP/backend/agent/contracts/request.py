from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass(slots=True)
class AlignmentRequest:
    """
    Agent v2 request contract for one alignment invocation.
    """

    target_seq: str
    output_csv_path: str = "/dev/null"
    anticode: str = ""
    use_llm: bool = True
    llm_reasoning: bool = False
    template_name: Optional[str] = None
    input_type: str = "unknown"
    domain_hint: str = ""
    cca_status: str = "unknown"
    intron_status: str = "unknown"
    trna_class: str = "unknown"
    mitochondrial_subtype: str = "unknown"
    enable_root_recovery: bool = False
    return_meta: bool = False
    runtime_values_override: Optional[Dict[str, Any]] = None
    status_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def normalized_target_seq(self) -> str:
        return (self.target_seq or "").upper().replace(" ", "").replace("\n", "").replace("\r", "")


__all__ = ["AlignmentRequest"]
