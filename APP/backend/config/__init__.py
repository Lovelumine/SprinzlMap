from pathlib import Path


_BASE = Path(__file__).resolve().parents[1]
__path__ = [str(_BASE / "infra" / "config")]
