"""Thread-safe LLM usage counters for billed backtests / desk ticks.

Desk inference runs on a thread pool, so counters cannot live on
``threading.local`` — the worker that snapshots would always see 0.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_calls = 0
_prompt_tokens = 0
_completion_tokens = 0


def reset_usage() -> None:
    global _calls, _prompt_tokens, _completion_tokens
    with _lock:
        _calls = 0
        _prompt_tokens = 0
        _completion_tokens = 0


def record_usage(*, prompt_tokens: int = 0, completion_tokens: int = 0, calls: int = 1) -> None:
    global _calls, _prompt_tokens, _completion_tokens
    with _lock:
        _calls += int(calls)
        _prompt_tokens += max(0, int(prompt_tokens))
        _completion_tokens += max(0, int(completion_tokens))


def snapshot_usage() -> dict[str, Any]:
    with _lock:
        return {
            "llm_calls": int(_calls),
            "prompt_tokens": int(_prompt_tokens),
            "completion_tokens": int(_completion_tokens),
        }
