from __future__ import annotations

import pytest

from llm.agent_llm_client import _date_tag_from_state, _prompt_hash
from llm.decision_cache import read_cached_decision, write_cached_decision


def test_date_tag_prefers_backtest_window_ts() -> None:
    state = {
        "ts_ms": 1,
        "shared_memory": {"backtest": {"window_last_ts_ms": 1_700_000_000_000}},
    }
    assert _date_tag_from_state(state) == "1700000000000"


def test_date_tag_falls_back_to_ts_ms() -> None:
    assert _date_tag_from_state({"ts_ms": 42}) == "42"


def test_date_tag_na_when_missing() -> None:
    assert _date_tag_from_state({}) == "na"


def test_decision_cache_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIMM_DECISION_CACHE_DIR", str(tmp_path))
    prompt_hash = _prompt_hash("sys\nuser")
    decision = {"stance": "bullish", "confidence": 0.7}
    write_cached_decision("technical_ta_engine", "BTC/USDT", "1", prompt_hash, decision)
    hit = read_cached_decision("technical_ta_engine", "BTC/USDT", "1", prompt_hash)
    assert hit == decision
    assert read_cached_decision("technical_ta_engine", "BTC/USDT", "2", prompt_hash) is None
