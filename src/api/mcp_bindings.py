"""Map user-level X-API-KEY → strategy binding for OlaXBT Nexus MCP.

Bindings are loaded from (first match wins per key):
1. Env ``MCP_API_KEYS_JSON`` — JSON object keyed by api_key
2. File ``MCP_API_KEYS_PATH`` or ``.runs/mcp/api_keys.json``

Example entry::

    {
      "demo-nexus-key": {
        "strategy_id": "demo-btc-agentic",
        "run_id": "journey_llm_builder_default",
        "estimated_aum_usdt": 12500,
        "label": "Hackathon demo strategy"
      }
    }
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.runs_paths import runs_dir

_LOCK = threading.Lock()


@dataclass(frozen=True)
class McpBinding:
    api_key: str
    strategy_id: str
    run_id: str = ""
    estimated_aum_usdt: float | None = None
    label: str = ""
    user_id: str = ""
    key_hash: str = ""

    def as_public(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "run_id": self.run_id or None,
            "label": self.label or None,
            "user_id": self.user_id or None,
            "estimated_aum_usdt": self.estimated_aum_usdt,
        }

    def as_file_entry(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "run_id": self.run_id,
            "label": self.label,
            "user_id": self.user_id,
            "key_hash": self.key_hash or hash_api_key(self.api_key),
        }
        if self.estimated_aum_usdt is not None:
            out["estimated_aum_usdt"] = self.estimated_aum_usdt
        return out


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()


# Process-local overlay so bind/unbind is visible immediately (file is the restart source).
_OVERLAY: dict[str, McpBinding] = {}
_DELETED: set[str] = set()


def _default_keys_path() -> Path:
    raw = (os.getenv("MCP_API_KEYS_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return runs_dir() / "mcp" / "api_keys.json"


def _parse_entry(api_key: str, raw: Any) -> McpBinding | None:
    if not isinstance(raw, dict):
        return None
    strategy_id = str(raw.get("strategy_id") or "").strip()
    if not strategy_id:
        return None
    run_id = str(raw.get("run_id") or raw.get("backtest_id") or "").strip()
    aum_raw = raw.get("estimated_aum_usdt")
    aum: float | None
    try:
        aum = float(aum_raw) if aum_raw is not None else None
    except (TypeError, ValueError):
        aum = None
    return McpBinding(
        api_key=api_key,
        strategy_id=strategy_id,
        run_id=run_id,
        estimated_aum_usdt=aum,
        label=str(raw.get("label") or "").strip(),
        user_id=str(raw.get("user_id") or "").strip(),
        key_hash=str(raw.get("key_hash") or "").strip() or hash_api_key(api_key),
    )


def load_bindings() -> dict[str, McpBinding]:
    out: dict[str, McpBinding] = {}

    env_raw = (os.getenv("MCP_API_KEYS_JSON") or "").strip()
    if env_raw:
        try:
            payload = json.loads(env_raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key, entry in payload.items():
                k = str(key).strip()
                binding = _parse_entry(k, entry)
                if k and binding:
                    out[k] = binding

    path = _default_keys_path()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            for key, entry in payload.items():
                k = str(key).strip()
                if k in out:
                    continue
                binding = _parse_entry(k, entry)
                if k and binding:
                    out[k] = binding

    return out


def resolve_binding(api_key: str | None) -> McpBinding | None:
    key = (api_key or "").strip()
    if not key:
        return None
    with _LOCK:
        if key in _DELETED:
            return None
        overlay = _OVERLAY.get(key)
        if overlay is not None:
            return overlay
    return load_bindings().get(key)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _read_file_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def upsert_binding(
    api_key: str,
    *,
    strategy_id: str,
    user_id: str = "",
    label: str = "",
    run_id: str = "",
    estimated_aum_usdt: float | None = None,
) -> McpBinding:
    key = (api_key or "").strip()
    sid = (strategy_id or "").strip()
    if not key or len(key) < 12:
        raise ValueError("api_key is required")
    if not sid:
        raise ValueError("strategy_id is required")

    binding = McpBinding(
        api_key=key,
        strategy_id=sid,
        run_id=(run_id or "").strip(),
        estimated_aum_usdt=estimated_aum_usdt,
        label=(label or "").strip(),
        user_id=(user_id or "").strip(),
        key_hash=hash_api_key(key),
    )
    path = _default_keys_path()
    with _LOCK:
        payload = _read_file_payload(path)
        existing = payload.get(key) if isinstance(payload.get(key), dict) else {}
        if isinstance(existing, dict) and (not binding.run_id or estimated_aum_usdt is None):
            keep_aum = estimated_aum_usdt
            if keep_aum is None:
                try:
                    keep_aum = (
                        float(existing["estimated_aum_usdt"])
                        if existing.get("estimated_aum_usdt") is not None
                        else None
                    )
                except (TypeError, ValueError):
                    keep_aum = None
            binding = McpBinding(
                api_key=key,
                strategy_id=sid,
                run_id=binding.run_id or str(existing.get("run_id") or "").strip(),
                estimated_aum_usdt=keep_aum,
                label=binding.label or str(existing.get("label") or "").strip(),
                user_id=binding.user_id or str(existing.get("user_id") or "").strip(),
                key_hash=binding.key_hash,
            )
        payload[key] = binding.as_file_entry()
        payload[key]["bound_at"] = int(time.time())
        _atomic_write_json(path, payload)
        _OVERLAY[key] = binding
        _DELETED.discard(key)
    return binding


def delete_binding(*, api_key: str | None = None, key_hash: str | None = None) -> int:
    """Remove bindings by plaintext key or SHA-256 hex. Returns number removed."""
    key = (api_key or "").strip()
    digest = (key_hash or "").strip().lower()
    if key:
        digest = digest or hash_api_key(key)
    if not key and not digest:
        raise ValueError("api_key or key_hash is required")

    path = _default_keys_path()
    removed = 0
    with _LOCK:
        payload = _read_file_payload(path)
        drop: list[str] = []
        for k, entry in list(payload.items()):
            if key and k == key:
                drop.append(k)
                continue
            if digest:
                stored = ""
                if isinstance(entry, dict):
                    stored = str(entry.get("key_hash") or "").strip().lower()
                if stored == digest or hash_api_key(str(k)) == digest:
                    drop.append(k)
        for k in drop:
            payload.pop(k, None)
            _OVERLAY.pop(k, None)
            _DELETED.add(k)
            removed += 1
        if key:
            _OVERLAY.pop(key, None)
            _DELETED.add(key)
        if removed:
            _atomic_write_json(path, payload)
    return removed


def set_run_id_for_strategy(
    strategy_id: str,
    run_id: str,
    *,
    estimated_aum_usdt: float | None = None,
) -> int:
    """Stamp the latest Flow run_id onto every key bound to this strategy."""
    sid = (strategy_id or "").strip()
    rid = (run_id or "").strip()
    if not sid or not rid:
        return 0
    path = _default_keys_path()
    updated = 0
    with _LOCK:
        payload = _read_file_payload(path)
        for k, entry in list(payload.items()):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("strategy_id") or "").strip() != sid:
                continue
            entry["run_id"] = rid
            if estimated_aum_usdt is not None:
                entry["estimated_aum_usdt"] = estimated_aum_usdt
            payload[k] = entry
            parsed = _parse_entry(str(k), entry)
            if parsed:
                _OVERLAY[str(k)] = parsed
            updated += 1
        if updated:
            _atomic_write_json(path, payload)
    return updated
