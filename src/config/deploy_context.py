"""Per-thread deploy JSON path so concurrent jobs do not share deploy.active.json."""

from __future__ import annotations

import threading
from pathlib import Path

_local = threading.local()


def set_thread_deploy_path(path: str | Path | None) -> None:
    _local.path = str(path) if path else None


def get_thread_deploy_path() -> str | None:
    raw = getattr(_local, "path", None)
    return str(raw) if raw else None


class thread_deploy_path:
    """Context manager: bind a deploy JSON file for this thread only."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._prev: str | None = None

    def __enter__(self) -> str:
        self._prev = get_thread_deploy_path()
        set_thread_deploy_path(self.path)
        return self.path

    def __exit__(self, *args: object) -> None:
        set_thread_deploy_path(self._prev)
