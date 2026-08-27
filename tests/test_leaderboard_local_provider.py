"""E2E-style checks: global leaderboard vs ``/leadpage/providers/local/*`` inner routes."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def lb_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIMM_RUNS_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    run_id = "bt_e2e_local_provider"
    out_dir = tmp_path / "backtests" / run_id
    out_dir.mkdir(parents=True)
    summary = {
        "run_id": run_id,
        "initial_cash": 10000.0,
        "final_equity": 10100.0,
        "metrics": {
            "total_return_pct": 1.0,
            "sharpe": 0.5,
            "max_drawdown_pct": 2.5,
            "total_trades": 4,
            "win_rate_pct": 50.0,
        },
        "benchmark": {"excess_return_vs_buy_hold_equity_pct": 0.25},
        "end_ts": 1_700_000_000_000,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    import api.leadpage_routes as lr

    importlib.reload(lr)
    app = FastAPI()
    app.include_router(lr.router)
    return TestClient(app), run_id


def test_global_leaderboard_includes_local_run(lb_client: tuple[TestClient, str]) -> None:
    client, run_id = lb_client
    r = client.get("/leadpage/leaderboard?limit=100&sort_by=return")
    assert r.status_code == 200, r.text
    rows = r.json().get("rows") or []
    ids = [x.get("run_id") for x in rows]
    assert run_id in ids
    row = next(x for x in rows if x.get("run_id") == run_id)
    assert row.get("provider") == "local"
    assert row.get("total_return_pct") == pytest.approx(1.0)
    assert row.get("trade_count") == 4
    assert row.get("change_pct") == pytest.approx(0.25)


def test_inner_local_provider_leaderboard_matches(lb_client: tuple[TestClient, str]) -> None:
    client, run_id = lb_client
    r = client.get("/leadpage/providers/local/leaderboard?limit=100&sort_by=return")
    assert r.status_code == 200, r.text
    body = r.json()
    assert int(body.get("count") or 0) >= 1
    rows = body.get("rows") or []
    assert any(x.get("run_id") == run_id for x in rows), rows
    row = next(x for x in rows if x.get("run_id") == run_id)
    assert row.get("provider") == "local"
    assert row.get("total_return_pct") == pytest.approx(1.0)


def test_leaderboard_title_from_deploy_description_not_ticker() -> None:
    from api.leadpage_routes import _leaderboard_row_from_summary

    row = _leaderboard_row_from_summary(
        run_id="bt-title-check",
        summary={
            "symbols": ["BTC/USDT"],
            "initial_cash": 100_000.0,
            "final_equity": 101_000.0,
            "metrics": {"win_rate_pct": 40.0, "total_trades": 5},
            "resolved_config": {
                "deploy_description": "LLM BTC swing",
                "deploy_path": "/tmp/deploy.json",
            },
        },
    )
    assert row["title"] == "LLM BTC swing"
    assert row["ticker"] == "BTC/USDT"
    assert row["win_rate"] == pytest.approx(40.0)


def test_leaderboard_win_rate_fraction_to_percent() -> None:
    from api.leadpage_routes import _leaderboard_row_from_summary

    row = _leaderboard_row_from_summary(
        run_id="bt-wr",
        summary={"metrics": {"win_rate": 0.2, "total_trades": 5}},
    )
    assert row["win_rate"] == pytest.approx(20.0)


def test_private_tenant_summaries_stay_off_public_leaderboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIMM_RUNS_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    run_id = "bt_tenant_hidden"
    out_dir = tmp_path / "backtests" / run_id
    out_dir.mkdir(parents=True)
    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "initial_cash": 10_000.0,
                "final_equity": 11_000.0,
                "metrics": {"total_return_pct": 10.0, "total_trades": 3, "win_rate_pct": 33.0},
                "resolved_config": {
                    "deploy_path": str(tmp_path / "tenants" / "user1" / run_id / "deploy.json"),
                    "deploy_description": "Secret tenant strategy",
                },
                "end_ts": 1_700_000_000_000,
            }
        ),
        encoding="utf-8",
    )

    import api.leadpage_routes as lr

    importlib.reload(lr)
    app = FastAPI()
    app.include_router(lr.router)
    client = TestClient(app)
    r = client.get("/leadpage/leaderboard?limit=100&sort_by=return")
    assert r.status_code == 200, r.text
    ids = [x.get("run_id") for x in (r.json().get("rows") or [])]
    assert run_id not in ids


def test_inner_local_provider_rows_history(lb_client: tuple[TestClient, str]) -> None:
    client, run_id = lb_client
    r = client.get("/leadpage/providers/local/rows?limit=100")
    assert r.status_code == 200, r.text
    body = r.json()
    assert int(body.get("count") or 0) >= 1
    rows = body.get("rows") or []
    assert any(x.get("run_id") == run_id for x in rows), rows
    hit = next(x for x in rows if x.get("run_id") == run_id)
    assert hit.get("total_return_pct") == pytest.approx(1.0)
    meta = hit.get("meta")
    assert isinstance(meta, dict)
    assert meta.get("kind") == "local_backtest_summary"
    assert isinstance(meta.get("summary"), dict)
