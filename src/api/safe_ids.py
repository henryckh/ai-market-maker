from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RUN_ALIASES = frozenset(
    {
        "latest",
        "latest-paper",
        "latest_paper",
        "paper",
        "latest-backtest",
        "latest_backtest",
        "backtest",
    }
)


def require_safe_id(value: str, *, name: str = "id") -> str:
    v = (value or "").strip()
    if v in _RUN_ALIASES:
        return v
    if not _SAFE_ID_RE.fullmatch(v):
        raise HTTPException(status_code=400, detail=f"invalid {name}")
    return v


def path_under(root: Path, *parts: str) -> Path:
    root_r = root.resolve()
    candidate = root_r.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root_r)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    return candidate
