from __future__ import annotations

from types import SimpleNamespace

from llm.agent_llm_client import infer_agent
from workflow.weighted_arbitrator import _inject_llm_signals


class _FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        text = self.contents.pop(0) if self.contents else ""
        msg = SimpleNamespace(content=text)
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        return SimpleNamespace(choices=[choice])


def _fake_client(contents: list[str]) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(contents)))


def test_infer_agent_parses_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "llm.agent_llm_client._get_client",
        lambda: _fake_client(['{"ta_indicators": {"rsi": 61}}']),
    )
    monkeypatch.setattr("llm.agent_llm_client.get_default_model", lambda: "test-model")
    monkeypatch.setattr("llm.agent_llm_client._get_decision_cache", lambda: {})
    out = infer_agent("technical_ta_engine", {"ticker": "BTC/USDT"}, ticker="BTC/USDT")
    assert out["source"] == "agent_llm"
    assert out["ta_indicators"]["rsi"] == 61


def test_infer_agent_retries_unparseable(monkeypatch) -> None:
    monkeypatch.setattr(
        "llm.agent_llm_client._get_client",
        lambda: _fake_client(["not json", '{"ta_indicators": {"rsi": 61}}']),
    )
    monkeypatch.setattr("llm.agent_llm_client.get_default_model", lambda: "test-model")
    monkeypatch.setattr("llm.agent_llm_client._get_decision_cache", lambda: {})
    out = infer_agent("technical_ta_engine", {"ticker": "BTC/USDT"}, ticker="BTC/USDT")
    assert out["source"] == "agent_llm"
    assert out["ta_indicators"]["rsi"] == 61


def test_infer_agent_disables_thinking(monkeypatch) -> None:
    fake = _fake_client(['{"ta_indicators": {"rsi": 50}}'])
    monkeypatch.setattr("llm.agent_llm_client._get_client", lambda: fake)
    monkeypatch.setattr("llm.agent_llm_client.get_default_model", lambda: "test-model")
    monkeypatch.setattr("llm.agent_llm_client._get_decision_cache", lambda: {})
    infer_agent("technical_ta_engine", {"ticker": "BTC/USDT"}, ticker="BTC/USDT")
    assert fake.chat.completions.kwargs[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_llm_error_keeps_deterministic_contract(monkeypatch) -> None:
    monkeypatch.setattr("workflow.weighted_arbitrator.check_api_key", lambda: None)
    monkeypatch.setattr(
        "workflow.weighted_arbitrator._get_llm_enabled_agents",
        lambda _s: ["technical_ta_engine"],
    )
    monkeypatch.setattr(
        "workflow.weighted_arbitrator.infer_agent",
        lambda *_a, **_k: {
            "agent_id": "technical_ta_engine",
            "source": "error",
            "composite": 50,
            "confidence": 0.0,
        },
    )
    state = {
        "use_llm_synthesis": True,
        "arbitrator_mode": "agent_llm",
        "tier0_contracts": [
            {
                "agent_id": "technical_ta_engine",
                "source": "tier0",
                "ta_indicators": {"rsi": 61.0},
            }
        ],
    }
    out, deltas = _inject_llm_signals(state)
    assert out["tier0_contracts"][0]["ta_indicators"]["rsi"] == 61.0
    assert deltas[0]["source"] == "error"


def test_apply_llm_arbitration_alignment_blocks_buy(monkeypatch) -> None:
    from schemas.arbitration import ArbitrationResult
    from workflow.weighted_arbitrator import _apply_llm_arbitration

    monkeypatch.setattr("workflow.weighted_arbitrator.check_api_key", lambda: None)
    monkeypatch.setattr(
        "workflow.weighted_arbitrator.infer_arbitrator_decision",
        lambda *_a, **_k: {
            "source": "agent_llm",
            "action": "BUY",
            "stance": "bullish",
            "confidence": 0.8,
            "reasons": ["llm buy"],
        },
    )
    math = ArbitrationResult(
        composite_score=0.4,
        confidence=0.2,
        stance="neutral",
        conviction_level="low",
        reasons=["math hold"],
        alignment_gated=True,
        buy_triggered=False,
        sell_triggered=False,
        hold_triggered=True,
    )
    out, overlay = _apply_llm_arbitration({"arbitrator_llm": True, "ticker": "BTC/USDT"}, math)
    assert overlay["source"] == "agent_llm"
    assert out.buy_triggered is False
    assert out.hold_triggered is True
    assert any("alignment_gated" in r for r in out.reasons)
