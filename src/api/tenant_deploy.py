"""Write per-run tenant deploy JSON under .runs/tenants — never config/deploy.active.json."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from api.safe_ids import path_under, require_safe_id
from config.runs_paths import runs_dir

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_part(value: str, fallback: str) -> str:
    cleaned = _SAFE.sub("-", (value or "").strip())[:80].strip("-")
    return cleaned or fallback


def write_tenant_deploy(
    *,
    deploy: dict[str, Any],
    run_id: str,
    user_id: str = "",
) -> Path:
    rid = require_safe_id(str(run_id), name="run_id")
    uid = _safe_part(user_id, "anon")
    root = runs_dir() / "tenants" / uid
    dest = path_under(root, rid, "deploy.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(deploy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest
