"""Compatibility facade — prefer ``nexus_data.historical.store`` / provider.

Kept so existing imports continue to work during the paper data migration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus_data.historical.catalog import data_root
from nexus_data.historical.store import (
    load_fixture_for_date,
    ms_to_utc_date,
)


def load_nexus_for_date(date: str, *, data_dir: Path | None = None) -> dict[str, Any] | None:
    root = data_dir if data_dir is not None else data_root()
    return load_fixture_for_date(date, root=root)


def load_fear_greed_series(*, data_dir: Path | None = None) -> dict[str, int]:

    # thin series loader
    import csv

    path = (data_dir or data_root()) / "macro" / "fear_greed_daily.csv"
    out: dict[str, int] = {}
    if not path.is_file():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row["date"]] = int(float(row["value"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def nexus_context_for_bar(
    bar_ts_ms: float | int,
    *,
    data_dir: Path | None = None,
) -> dict[str, Any] | None:
    return load_nexus_for_date(ms_to_utc_date(bar_ts_ms), data_dir=data_dir)


__all__ = [
    "data_root",
    "load_nexus_for_date",
    "load_fear_greed_series",
    "ms_to_utc_date",
    "nexus_context_for_bar",
]
