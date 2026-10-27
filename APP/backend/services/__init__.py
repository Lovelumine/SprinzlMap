from pathlib import Path


_BASE = Path(__file__).resolve().parents[1]
__path__ = [
    str(_BASE / "application" / "alignment"),
    str(_BASE / "application" / "generation"),
    str(_BASE / "application" / "regions"),
    str(_BASE / "infra" / "config"),
    str(_BASE / "infra" / "storage"),
    str(_BASE / "infra" / "llm"),
]
