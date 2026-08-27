"""Tests for OlaXBT Nexus MCP low-latency read tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runs = tmp_path / ".runs"
    bt = runs / "backtests" / "demo_run"
    bt.mkdir(parents=True)
    summary = {
        "run_id": "demo_run",
        "initial_cash": 10000.0,
        "start_ts": 1_700_000_000_000,
        "end_ts": 1_700_000_000_000 + 45 * 86400_000,
        "symbols": ["BTC/USDT"],
        "metrics": {
            "sharpe_ratio": 2.25,
            "profit_factor": 1.43,
            "max_drawdown_pct": 3.85,
        },
    }
    (bt / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    iter_row = {
        "ts": 1_787_788_800,
        "run_id": "demo_run",
        "symbol": "BTC/USDT",
        "action": "BUY",
        "stance": "bullish",
        "confidence": 0.4,
        "composite_score": 0.62,
        "target_weight": 0.1,
        "desk_scores": {
            "technical_ta_engine": {"BTC/USDT": 0.7},
            "news_narrative_miner": {"BTC/USDT": 0.65},
        },
        "deploy_profile_weights": {
            "technical_ta_engine": 0.35,
            "news_narrative_miner": 0.20,
        },
        "arbitrator_mode": "agent_llm",
    }
    (bt / "iterations.jsonl").write_text(json.dumps(iter_row) + "\n", encoding="utf-8")

    keys = {
        "test-mcp-key": {
            "strategy_id": "demo-btc",
            "run_id": "demo_run",
            "estimated_aum_usdt": 12500,
        }
    }
    keys_path = runs / "mcp" / "api_keys.json"
    keys_path.parent.mkdir(parents=True, exist_ok=True)
    keys_path.write_text(json.dumps(keys), encoding="utf-8")

    monkeypatch.setenv("AIMM_RUNS_DIR", str(runs))
    monkeypatch.setenv("MCP_API_KEYS_PATH", str(keys_path))
    monkeypatch.delenv("MCP_API_KEYS_JSON", raising=False)
    monkeypatch.delenv("MCP_CREDITS_ENABLED", raising=False)
    monkeypatch.delenv("AIMM_WEB_API_URL", raising=False)
    monkeypatch.delenv("AIMM_WEB_API_BASE_URL", raising=False)
    # Flow middleware still needs a key for non-/mcp paths; MCP is exempt.
    monkeypatch.setenv("AIMM_API_KEY", "test-aimm-key-for-middleware")
    yield {"runs": runs, "api_key": "test-mcp-key"}
    from api import mcp_bindings

    mcp_bindings._OVERLAY.clear()
    mcp_bindings._DELETED.clear()


def test_mcp_tools_and_call(mcp_env, monkeypatch):
    import api.mcp_datalayer as dl
    from api.flow_stream_server import app

    client = TestClient(app)
    headers = {"X-API-KEY": mcp_env["api_key"]}

    health = client.get("/mcp/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json().get("credits") is False

    tools = client.get("/mcp/tools", headers=headers)
    assert tools.status_code == 200
    names = {t["name"] for t in tools.json()["tools"]}
    assert names >= {
        "get_strategy_metrics",
        "get_strategy_signal",
        "get_strategy_equity",
        "get_strategy_trades",
        "run_backtest",
        "get_backtest_job",
        "get_historical_ohlcv",
        "get_historical_funding",
        "get_open_interest",
        "get_vcp",
        "get_macro",
        "get_market_snapshot",
        "get_historical_coverage",
        "get_etf_flow",
        "get_oi_ranking",
        "get_fear_greed",
        "get_news",
        "get_sentiment",
    }
    cats = {t["name"]: t.get("category") for t in tools.json()["tools"]}
    assert cats["get_strategy_signal"] == "trading"
    assert cats["get_historical_ohlcv"] == "historical"

    metrics = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_strategy_metrics", "arguments": {}},
    )
    assert metrics.status_code == 200
    content = metrics.json()["content"]
    assert content["sharpe_ratio"] == 2.25
    assert content["trading_period_days"] == 45
    assert content["estimated_aum_usdt"] == 12500
    assert content["status"] == "QUALIFIED_FOR_OKX_LISTING"

    signal = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_strategy_signal", "arguments": {"symbol": "BTC/USDT"}},
    )
    assert signal.status_code == 200
    sig = signal.json()["content"]
    assert sig["trade_intent"] == "BUY"
    assert sig["symbol"] == "BTC/USDT"
    assert "technical" in sig["reasoning_log"].lower() or "ta" in sig["reasoning_log"].lower()

    def fake_dl(path, *, params=None, api_key=""):
        if "fear-greed" in path:
            return {"success": True, "data": {"as_of_date": "2024-01-15", "fear_greed": 64}}
        if path.endswith("/api/news"):
            return {
                "news": [{"title": "hello", "source": "test", "url": "https://x", "published": "t"}]
            }
        if "sentiment" in path:
            return {"success": True, "data": {"symbol": "BTC", "score": 0.1}}
        if "historical/nexus" in path:
            return {
                "success": True,
                "data": {
                    "as_of_date": "2024-01-15",
                    "source": "datalayer:historical",
                    "per_symbol": {
                        "by_symbol": {
                            "BTC/USDT": {
                                "quant_summary": {
                                    "ok": True,
                                    "data": {
                                        "interval": "1d",
                                        "ticker": {"lastPrice": 42515},
                                        "klines": [
                                            [1705276800000, 42800, 43376, 41720, 42515, 240059]
                                        ],
                                        "funding": {"lastFundingRate": 0.0001},
                                    },
                                },
                                "funding": {"ok": True, "data": {"lastFundingRate": 0.0001}},
                                "technical_analysis": {
                                    "ok": True,
                                    "data": {"indicators": {"rsi": 46.1, "sma_20": 43841}},
                                },
                                "coin": {
                                    "ok": True,
                                    "data": {
                                        "price": 42515,
                                        "funding_rate": 0.0001,
                                        "long_short_ratio": 2.65,
                                        "oi": {
                                            "binance": {
                                                "current_oi": 77082,
                                                "current_oi_usd": 3.2e9,
                                            }
                                        },
                                    },
                                },
                                "vcp": {
                                    "last_close": 42515,
                                    "scan_tf": "1d",
                                    "trend_template": [
                                        {"name": "TT1_price_above_150_and_200", "passed": True},
                                        {"name": "TT5_price_above_50ma", "passed": False},
                                    ],
                                },
                            }
                        }
                    },
                    "endpoints": {
                        "market_overview": {
                            "ok": True,
                            "data": {
                                "vix": 13.25,
                                "us_10y_yield_pct": 3.96,
                                "effective_fed_funds_pct": 5.33,
                            },
                        }
                    },
                    "vcp_universe": {"n_tokens_scanned": 5, "n_passed_strict": 0},
                },
            }
        if "date-range" in path:
            return {"success": True, "data": {"min_date": "2021-01-01", "max_date": "2026-08-26"}}
        if path.endswith("/api/etf/inflow"):
            return {"success": True, "data": [{"date": "2024-01-15", "btc": 120}]}
        if "oi/top-ranking" in path:
            return {
                "success": True,
                "data": {
                    "positions": [
                        {
                            "symbol": "BTCUSDT",
                            "rank": 1,
                            "oi_usd": 3.2e9,
                            "funding_rate": 0.0001,
                            "score": 55,
                        }
                    ]
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(dl, "datalayer_get", fake_dl)
    fg = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_fear_greed", "arguments": {"as_of": "2024-01-15"}},
    )
    assert fg.status_code == 200
    assert fg.json()["content"]["fear_greed"] == 64
    snap = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_market_snapshot", "arguments": {"as_of": "2024-01-15"}},
    )
    assert snap.status_code == 200
    assert snap.json()["content"]["as_of_date"] == "2024-01-15"
    news = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_news", "arguments": {"symbol": "BTC", "limit": 5}},
    )
    assert news.status_code == 200
    assert news.json()["content"]["count"] == 1
    ohlcv = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={
            "name": "get_historical_ohlcv",
            "arguments": {"as_of": "2024-01-15", "symbol": "BTC/USDT"},
        },
    )
    assert ohlcv.status_code == 200
    candles = ohlcv.json()["content"]["candles"]
    assert candles and candles[0]["c"] == 42515
    fund = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={
            "name": "get_historical_funding",
            "arguments": {"as_of": "2024-01-15", "symbol": "BTC/USDT"},
        },
    )
    assert fund.status_code == 200
    assert fund.json()["content"]["last_funding_rate"] == 0.0001
    oi = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={
            "name": "get_open_interest",
            "arguments": {"as_of": "2024-01-15", "symbol": "BTC/USDT"},
        },
    )
    assert oi.status_code == 200
    assert oi.json()["content"]["open_interest_usd"] == 3.2e9
    vcp = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_vcp", "arguments": {"as_of": "2024-01-15", "symbol": "BTC/USDT"}},
    )
    assert vcp.status_code == 200
    assert "TT1_price_above_150_and_200" in vcp.json()["content"]["passed"]
    macro = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_macro", "arguments": {"as_of": "2024-01-15"}},
    )
    assert macro.status_code == 200
    assert macro.json()["content"]["macro"]["vix"] == 13.25
    cov = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_historical_coverage", "arguments": {}},
    )
    assert cov.status_code == 200
    assert cov.json()["content"]["min_date"] == "2021-01-01"
    etf = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_etf_flow", "arguments": {}},
    )
    assert etf.status_code == 200
    assert etf.json()["content"]["count"] == 1
    rank = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_oi_ranking", "arguments": {"limit": 10}},
    )
    assert rank.status_code == 200
    assert rank.json()["content"]["positions"][0]["symbol"] == "BTCUSDT"


def test_mcp_rejects_unknown_key(mcp_env):
    from api.flow_stream_server import app

    client = TestClient(app)
    r = client.get("/mcp/tools", headers={"X-API-KEY": "nope"})
    assert r.status_code == 401


def test_mcp_admin_bind_publish_unbind(mcp_env):
    from api.flow_stream_server import app
    from api.mcp_bindings import hash_api_key

    client = TestClient(app)
    ops = {"X-API-KEY": "test-aimm-key-for-middleware"}
    user_key = "nxk_bindtestkey_abcdefghijklmnopqrstuvwxyz"
    user = {"X-API-KEY": user_key}

    forbidden = client.post(
        "/mcp/admin/bind",
        headers=user,
        json={"api_key": user_key, "strategy_id": "demo-btc"},
    )
    assert forbidden.status_code == 403

    bound = client.post(
        "/mcp/admin/bind",
        headers=ops,
        json={
            "api_key": user_key,
            "strategy_id": "demo-btc",
            "user_id": "usr_test",
            "label": "hackathon",
        },
    )
    assert bound.status_code == 200
    assert bound.json()["ok"] is True
    assert bound.json()["strategy_id"] == "demo-btc"
    assert bound.json()["key_hash"] == hash_api_key(user_key)

    tools = client.get("/mcp/tools", headers=user)
    assert tools.status_code == 200
    assert tools.json()["strategy_id"] == "demo-btc"

    published = client.post(
        "/mcp/admin/publish",
        headers=ops,
        json={"strategy_id": "demo-btc", "run_id": "demo_run", "symbol": "BTC/USDT"},
    )
    assert published.status_code == 200
    assert published.json()["metrics"]["sharpe_ratio"] == 2.25
    cache = mcp_env["runs"] / "mcp" / "demo-btc" / "metrics.json"
    assert cache.is_file()

    gone = client.post(
        "/mcp/admin/unbind",
        headers=ops,
        json={"key_hash": hash_api_key(user_key)},
    )
    assert gone.status_code == 200
    assert gone.json()["removed"] >= 1

    after = client.get("/mcp/tools", headers=user)
    assert after.status_code == 401


def test_mcp_two_accounts_isolated(mcp_env):
    """Different API keys resolve to different strategies and cannot read each other."""
    from api.flow_stream_server import app
    from api.mcp_cache import write_signal_cache

    write_signal_cache(
        "user-a-strat",
        {
            "symbol": "BTC/USDT",
            "trade_intent": "BUY",
            "reasoning_log": "user A long",
            "timestamp": 1_700_000_100,
        },
    )
    write_signal_cache(
        "user-b-strat",
        {
            "symbol": "BTC/USDT",
            "trade_intent": "SELL",
            "reasoning_log": "user B short",
            "timestamp": 1_700_000_200,
        },
    )

    client = TestClient(app)
    ops = {"X-API-KEY": "test-aimm-key-for-middleware"}
    key_a = "nxk_userA_isolation_key_abcdefghijklmn"
    key_b = "nxk_userB_isolation_key_abcdefghijklmn"

    assert (
        client.post(
            "/mcp/admin/bind",
            headers=ops,
            json={"api_key": key_a, "strategy_id": "user-a-strat", "user_id": "usr_a"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/mcp/admin/bind",
            headers=ops,
            json={"api_key": key_b, "strategy_id": "user-b-strat", "user_id": "usr_b"},
        ).status_code
        == 200
    )

    call_a = client.post(
        "/mcp/tools/call",
        headers={"X-API-KEY": key_a},
        json={"name": "get_strategy_signal", "arguments": {"symbol": "BTC/USDT"}},
    )
    call_b = client.post(
        "/mcp/tools/call",
        headers={"X-API-KEY": key_b},
        json={"name": "get_strategy_signal", "arguments": {"symbol": "BTC/USDT"}},
    )
    assert call_a.status_code == 200
    assert call_b.status_code == 200
    ja, jb = call_a.json(), call_b.json()
    assert ja["strategy_id"] == "user-a-strat"
    assert jb["strategy_id"] == "user-b-strat"
    assert ja["content"]["trade_intent"] == "BUY"
    assert jb["content"]["trade_intent"] == "SELL"
    assert ja["content"]["reasoning_log"] != jb["content"]["reasoning_log"]


def test_mcp_run_backtest_and_poll(mcp_env, monkeypatch):
    from api.flow_stream_server import app

    runs = mcp_env["runs"]
    prior = runs / "backtests" / "bt_prior_studio"
    prior.mkdir(parents=True, exist_ok=True)
    (prior / "job.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "strategy_id": "demo-btc",
                "request": {
                    "strategy_id": "demo-btc",
                    "ticker": "BTC/USDT",
                    "n_bars": 80,
                    "interval_sec": 3600,
                    "initial_cash": 10000,
                    "deploy": {"agents": {"ta": {"enabled": True, "weight": 1.0}}},
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_enqueue(req):
        rid = "bt-mcp-new"
        job_dir = runs / "backtests" / rid
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.json").write_text(
            json.dumps(
                {
                    "status": "running",
                    "step": 12,
                    "total_steps": 80,
                    "trade_count": 1,
                    "equity": 10050,
                    "strategy_id": "demo-btc",
                    "request": req.model_dump() if hasattr(req, "model_dump") else {},
                }
            ),
            encoding="utf-8",
        )
        return {"run_id": rid, "poll": f"/backtests/jobs/{rid}"}

    monkeypatch.setattr("api.backtest_routes.post_strategy_backtest_run", fake_enqueue)

    client = TestClient(app)
    headers = {"X-API-KEY": mcp_env["api_key"]}
    started = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "run_backtest", "arguments": {"n_bars": 80}},
    )
    assert started.status_code == 200
    body = started.json()["content"]
    assert body["run_id"] == "bt-mcp-new"
    assert body["status"] == "queued"

    polled = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_backtest_job", "arguments": {"run_id": "bt-mcp-new"}},
    )
    assert polled.status_code == 200
    job = polled.json()["content"]
    assert job["status"] == "running"
    assert job["step"] == 12
    assert job["total_steps"] == 80
    assert job["run_id"] == "bt-mcp-new"


def test_mcp_run_backtest_real_enqueue_is_tenant_scoped(mcp_env, monkeypatch):
    """Real Flow enqueue (no LangGraph): job + tenant deploy, other keys cannot poll it."""
    from api.flow_stream_server import app

    monkeypatch.setenv("AIMM_BACKTEST_WORKER_EMBEDDED", "0")
    runs = mcp_env["runs"]
    prior = runs / "backtests" / "bt_prior_studio"
    prior.mkdir(parents=True, exist_ok=True)
    deploy = {"agents": {"ta": {"enabled": True, "weight": 1.0}}}
    (prior / "job.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "strategy_id": "demo-btc",
                "request": {
                    "user_id": "usr_a",
                    "strategy_id": "demo-btc",
                    "ticker": "BTC/USDT",
                    "n_bars": 40,
                    "deploy": deploy,
                },
            }
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    headers = {"X-API-KEY": mcp_env["api_key"]}
    started = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "run_backtest", "arguments": {"n_bars": 40}},
    )
    assert started.status_code == 200, started.text
    rid = started.json()["content"]["run_id"]
    assert rid.startswith("bt-")
    job_path = runs / "backtests" / rid / "job.json"
    assert job_path.is_file()
    stored = json.loads(job_path.read_text(encoding="utf-8"))
    assert stored["status"] == "queued"
    assert stored["strategy_id"] == "demo-btc"
    assert stored["user_id"] == "usr_a"
    tenant = runs / "tenants" / "usr_a" / rid / "deploy.json"
    assert tenant.is_file()
    assert json.loads(tenant.read_text())["agents"]["ta"]["enabled"] is True

    polled = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_backtest_job", "arguments": {"run_id": rid}},
    )
    assert polled.status_code == 200
    assert polled.json()["content"]["run_id"] == rid
    assert polled.json()["content"]["status"] == "queued"

    other_key = "nxk_other_tenant_key_abcdefghijklmn"
    ops = {"X-API-KEY": "test-aimm-key-for-middleware"}
    assert (
        client.post(
            "/mcp/admin/bind",
            headers=ops,
            json={"api_key": other_key, "strategy_id": "other-strat", "user_id": "usr_b"},
        ).status_code
        == 200
    )
    blocked = client.post(
        "/mcp/tools/call",
        headers={"X-API-KEY": other_key},
        json={"name": "get_backtest_job", "arguments": {"run_id": rid}},
    )
    assert blocked.status_code == 404

    # Completed job publishes metrics for this strategy only
    done_id = "bt-done-demo"
    done_dir = runs / "backtests" / done_id
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / "job.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "strategy_id": "demo-btc",
                "request": {"strategy_id": "demo-btc", "ticker": "BTC/USDT"},
            }
        ),
        encoding="utf-8",
    )
    (done_dir / "summary.json").write_text(
        (runs / "backtests" / "demo_run" / "summary.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (done_dir / "iterations.jsonl").write_text(
        (runs / "backtests" / "demo_run" / "iterations.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    published = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_backtest_job", "arguments": {"run_id": done_id}},
    )
    assert published.status_code == 200
    assert published.json()["content"]["published"] is True
    metrics = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_strategy_metrics", "arguments": {}},
    )
    assert metrics.status_code == 200
    assert metrics.json()["content"]["sharpe_ratio"] == 2.25


def _seed_prior_studio_job(runs: Path, strategy_id: str = "demo-btc") -> None:
    prior = runs / "backtests" / "bt_prior_studio"
    prior.mkdir(parents=True, exist_ok=True)
    (prior / "job.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "strategy_id": strategy_id,
                "request": {
                    "strategy_id": strategy_id,
                    "ticker": "BTC/USDT",
                    "n_bars": 80,
                    "interval_sec": 3600,
                    "initial_cash": 10000,
                    "deploy": {"agents": {"ta": {"enabled": True, "weight": 1.0}}},
                },
            }
        ),
        encoding="utf-8",
    )


def test_mcp_run_backtest_credits_unconfigured(mcp_env, monkeypatch):
    monkeypatch.setenv("MCP_CREDITS_ENABLED", "true")
    monkeypatch.delenv("AIMM_WEB_API_URL", raising=False)
    _seed_prior_studio_job(mcp_env["runs"])
    from api.flow_stream_server import app

    client = TestClient(app)
    started = client.post(
        "/mcp/tools/call",
        headers={"X-API-KEY": mcp_env["api_key"]},
        json={"name": "run_backtest", "arguments": {"n_bars": 80}},
    )
    assert started.status_code == 503
    detail = started.json()["detail"]
    assert detail["error"] == "credits_unconfigured"


def test_mcp_run_backtest_reserves_credits_when_enabled(mcp_env, monkeypatch):
    monkeypatch.setenv("MCP_CREDITS_ENABLED", "true")
    monkeypatch.setenv("AIMM_WEB_API_URL", "http://web-api:3002")
    _seed_prior_studio_job(mcp_env["runs"])
    attaches: list[str] = []

    monkeypatch.setattr(
        "api.mcp_jobs.reserve_mcp_credits",
        lambda binding, n_bars: {
            "ok": True,
            "reservation_id": "mcp_testreserve",
            "credits_reserved": 10,
            "remaining": 90,
        },
    )
    monkeypatch.setattr(
        "api.mcp_jobs.attach_mcp_flow_run",
        lambda binding, reservation, flow_run_id: attaches.append(flow_run_id),
    )

    def fake_enqueue(req):
        rid = "bt-mcp-credits"
        job_dir = mcp_env["runs"] / "backtests" / rid
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.json").write_text(
            json.dumps({"status": "queued", "strategy_id": "demo-btc", "step": 0}),
            encoding="utf-8",
        )
        return {"run_id": rid, "poll": f"/backtests/jobs/{rid}"}

    monkeypatch.setattr("api.backtest_routes.post_strategy_backtest_run", fake_enqueue)

    from api.flow_stream_server import app

    client = TestClient(app)
    started = client.post(
        "/mcp/tools/call",
        headers={"X-API-KEY": mcp_env["api_key"]},
        json={"name": "run_backtest", "arguments": {"n_bars": 80}},
    )
    assert started.status_code == 200, started.text
    body = started.json()["content"]
    assert body["run_id"] == "bt-mcp-credits"
    assert body["credits_reserved"] == 10
    assert body["reservation_id"] == "mcp_testreserve"
    stamped = json.loads(
        (mcp_env["runs"] / "backtests" / "bt-mcp-credits" / "job.json").read_text(encoding="utf-8")
    )
    assert stamped["mcp_credits"]["reservation_id"] == "mcp_testreserve"
    assert stamped["mcp_credits"]["credits_reserved"] == 10
    assert attaches == ["bt-mcp-credits"]


def test_mcp_run_backtest_insufficient_credits(mcp_env, monkeypatch):
    from api.mcp_credits import McpCreditsError

    monkeypatch.setenv("MCP_CREDITS_ENABLED", "true")
    monkeypatch.setenv("AIMM_WEB_API_URL", "http://web-api:3002")
    _seed_prior_studio_job(mcp_env["runs"])
    enqueued = {"n": 0}

    def no_credits(binding, n_bars):
        raise McpCreditsError(
            402,
            "insufficient_credits",
            "insufficient_credits",
            {"required": 10, "available": 2},
        )

    def fake_enqueue(req):
        enqueued["n"] += 1
        return {"run_id": "should-not-run"}

    monkeypatch.setattr("api.mcp_jobs.reserve_mcp_credits", no_credits)
    monkeypatch.setattr("api.backtest_routes.post_strategy_backtest_run", fake_enqueue)
    from api.flow_stream_server import app

    client = TestClient(app)
    started = client.post(
        "/mcp/tools/call",
        headers={"X-API-KEY": mcp_env["api_key"]},
        json={"name": "run_backtest", "arguments": {"n_bars": 80}},
    )
    assert started.status_code == 402
    detail = started.json()["detail"]
    assert detail["error"] == "insufficient_credits"
    assert detail["required"] == 10
    assert detail["available"] == 2
    assert enqueued["n"] == 0


def test_mcp_run_backtest_refunds_when_enqueue_fails(mcp_env, monkeypatch):
    monkeypatch.setenv("MCP_CREDITS_ENABLED", "true")
    monkeypatch.setenv("AIMM_WEB_API_URL", "http://web-api:3002")
    _seed_prior_studio_job(mcp_env["runs"])
    refunds: list[dict] = []

    monkeypatch.setattr(
        "api.mcp_jobs.reserve_mcp_credits",
        lambda binding, n_bars: {
            "reservation_id": "mcp_fail_enqueue",
            "credits_reserved": 10,
            "remaining": 5,
        },
    )
    monkeypatch.setattr(
        "api.mcp_jobs.refund_mcp_credits",
        lambda binding, reservation: refunds.append(dict(reservation)),
    )

    def boom(req):
        raise RuntimeError("enqueue down")

    monkeypatch.setattr("api.backtest_routes.post_strategy_backtest_run", boom)
    from api.flow_stream_server import app

    client = TestClient(app)
    started = client.post(
        "/mcp/tools/call",
        headers={"X-API-KEY": mcp_env["api_key"]},
        json={"name": "run_backtest", "arguments": {"n_bars": 80}},
    )
    assert started.status_code == 502
    detail = started.json()["detail"]
    assert detail["error"] == "enqueue_failed"
    assert refunds
    assert refunds[0]["reservation_id"] == "mcp_fail_enqueue"


def test_mcp_get_job_refunds_failed_unstarted(mcp_env, monkeypatch):
    monkeypatch.setenv("MCP_CREDITS_ENABLED", "true")
    monkeypatch.setenv("AIMM_WEB_API_URL", "http://web-api:3002")
    refunds: list[dict] = []
    monkeypatch.setattr(
        "api.mcp_jobs.refund_mcp_credits",
        lambda binding, reservation: refunds.append(dict(reservation)),
    )
    rid = "bt-failed-credits"
    job_dir = mcp_env["runs"] / "backtests" / rid
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "step": 0,
                "strategy_id": "demo-btc",
                "error": "worker crash before start",
                "mcp_credits": {
                    "reservation_id": "mcp_failjob",
                    "credits_reserved": 10,
                    "refunded": False,
                },
            }
        ),
        encoding="utf-8",
    )
    from api.flow_stream_server import app

    client = TestClient(app)
    headers = {"X-API-KEY": mcp_env["api_key"]}
    polled = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_backtest_job", "arguments": {"run_id": rid}},
    )
    assert polled.status_code == 200
    assert len(refunds) == 1
    assert refunds[0]["reservation_id"] == "mcp_failjob"
    stamped = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert stamped["mcp_credits"]["refunded"] is True

    again = client.post(
        "/mcp/tools/call",
        headers=headers,
        json={"name": "get_backtest_job", "arguments": {"run_id": rid}},
    )
    assert again.status_code == 200
    assert len(refunds) == 1


def test_mcp_credits_http_maps_402(monkeypatch):
    from api.mcp_bindings import McpBinding
    from api.mcp_credits import McpCreditsError, _post

    class FakeResp:
        status_code = 402
        text = '{"error":"insufficient_credits","required":10,"available":1}'

        def json(self):
            return {"error": "insufficient_credits", "required": 10, "available": 1}

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return FakeResp()

    monkeypatch.setenv("AIMM_WEB_API_URL", "http://web-api:3002")
    monkeypatch.setenv("AIMM_API_KEY", "flow-shared")
    monkeypatch.setattr("api.mcp_credits.httpx.Client", FakeClient)
    binding = McpBinding(api_key="nxk_testkey_abcdefghij", strategy_id="demo-btc")
    with pytest.raises(McpCreditsError) as exc:
        _post("/api/internal/mcp/credits/reserve", binding, {"strategy_id": "demo-btc"})
    assert exc.value.status == 402
    assert exc.value.error == "insufficient_credits"
    assert exc.value.extra["required"] == 10
