"""Universe tiers and data-root helpers for offline history."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CATALOG = _ROOT / "config" / "universe_tiers.json"


def repo_root() -> Path:
    return _ROOT


def data_root() -> Path:
    return _ROOT / "data"


@lru_cache(maxsize=1)
def load_universe_tiers() -> dict[str, list[str]]:
    path = _DEFAULT_CATALOG
    if not path.is_file():
        return {
            "core": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
            "portfolio": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"],
        }
    obj = json.loads(path.read_text(encoding="utf-8"))
    return {
        "core": list(obj.get("core") or []),
        "portfolio": list(obj.get("portfolio") or []),
    }


def symbols_for_tier(tier: str) -> list[str]:
    tiers = load_universe_tiers()
    key = (tier or "core").strip().lower()
    if key in tiers and tiers[key]:
        return list(tiers[key])
    return list(tiers.get("core") or ["BTCUSDT"])


def vision_symbol_to_ccxt(sym: str) -> str:
    s = sym.strip().upper().replace("/", "")
    if s.endswith("USDT") and "/" not in sym:
        base = s[: -len("USDT")]
        return f"{base}/USDT"
    if "/" in sym:
        return sym.strip().upper()
    return f"{s}/USDT"


def ccxt_to_vision_symbol(sym: str) -> str:
    return sym.strip().upper().replace("/", "").replace(":", "")


__all__ = [
    "repo_root",
    "data_root",
    "load_universe_tiers",
    "symbols_for_tier",
    "vision_symbol_to_ccxt",
    "ccxt_to_vision_symbol",
]
