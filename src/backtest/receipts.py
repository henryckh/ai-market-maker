"""Compact per-bar receipts: historical as-of data and desk CoT."""

from __future__ import annotations

from typing import Any


def _nexus_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    sm = state.get("shared_memory") or {}
    nexus = sm.get("nexus") if isinstance(sm, dict) else {}
    return nexus if isinstance(nexus, dict) else {}


def nexus_asof_receipt(state: dict[str, Any] | None) -> dict[str, Any]:
    """Stamp what desks/LLM actually saw this bar (post-market_scan output)."""
    nexus = _nexus_from_state(state)
    if not nexus:
        return {}
    eps = nexus.get("endpoints") if isinstance(nexus.get("endpoints"), dict) else {}
    out: dict[str, Any] = {
        "source": nexus.get("source"),
        "as_of_date": nexus.get("as_of_date"),
    }
    mo = eps.get("market_overview") if isinstance(eps.get("market_overview"), dict) else {}
    mo_data = mo.get("data") if isinstance(mo.get("data"), dict) else {}
    if mo_data.get("fear_greed_index") is not None:
        out["fear_greed"] = mo_data.get("fear_greed_index")
        out["fear_greed_label"] = mo_data.get("fear_greed_label")
    for key in (
        "vix",
        "effective_fed_funds_pct",
        "us_10y_yield_pct",
        "trade_weighted_usd_index",
        "stablecoin_change_7d_pct",
        "all_chain_tvl_change_7d_pct",
        "onchain_liquidity_score",
    ):
        if mo_data.get(key) is not None:
            out[key] = mo_data.get(key)
    news = eps.get("news") if isinstance(eps.get("news"), dict) else {}
    news_data = news.get("data") if isinstance(news.get("data"), dict) else {}
    items = news_data.get("news")
    if isinstance(items, list):
        out["news_n"] = len(items)
        titles = [
            str(x.get("title") or "")[:80]
            for x in items[:3]
            if isinstance(x, dict) and x.get("title")
        ]
        if titles:
            out["news_titles"] = titles
    oi = eps.get("oi_top_ranking") if isinstance(eps.get("oi_top_ranking"), dict) else {}
    oi_data = oi.get("data") if isinstance(oi.get("data"), dict) else {}
    inner = oi_data.get("data") if isinstance(oi_data.get("data"), dict) else oi_data
    positions = inner.get("positions") if isinstance(inner, dict) else None
    if isinstance(positions, list) and positions and isinstance(positions[0], dict):
        if positions[0].get("funding_rate") is not None:
            out["funding_rate"] = positions[0].get("funding_rate")

    ticker = state.get("ticker") if isinstance(state, dict) else None
    md = state.get("market_data") if isinstance(state, dict) else None
    if isinstance(ticker, str) and isinstance(md, dict):
        row = md.get(ticker)
        ohlcv = row.get("ohlcv") if isinstance(row, dict) else None
        if isinstance(ohlcv, list) and ohlcv:
            last = ohlcv[-1]
            if isinstance(last, (list, tuple)) and len(last) > 5:
                try:
                    out["last_volume"] = float(last[5])
                except (TypeError, ValueError):
                    pass
                try:
                    vols = [
                        float(b[5])
                        for b in ohlcv[-30:]
                        if isinstance(b, (list, tuple)) and len(b) > 5
                    ]
                    if vols:
                        avg = sum(vols) / len(vols)
                        last_v = vols[-1]
                        out["volume_vs_avg"] = round(last_v / avg, 3) if avg else 0.0
                except (TypeError, ValueError):
                    pass
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def compact_agent_contract(contract: dict[str, Any]) -> dict[str, Any]:
    aid = str(contract.get("agent_id", contract.get("agent", "?")))
    skip = {
        "agent",
        "agent_id",
        "label",
        "source",
        "llm_enabled",
        "llm_error",
        "cached",
        "reasoning",
        "composite",
        "confidence",
        "schema_version",
        "ticker",
        "status",
    }
    signal: dict[str, Any] = {}
    for k, v in contract.items():
        if k in skip or v is None:
            continue
        signal[k] = v
    entry: dict[str, Any] = {
        "agent_id": aid,
        "source": contract.get("source"),
        "composite": contract.get("composite"),
        "confidence": contract.get("confidence"),
    }
    if signal:
        entry["signal"] = signal
    reasoning = contract.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        entry["reasoning"] = reasoning[:400]
    err = contract.get("llm_error")
    if err:
        entry["llm_error"] = str(err)[:200]
    return entry


def _arbitration_scores_by_agent(wf_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    logs = wf_output.get("reasoning_logs")
    if not isinstance(logs, list):
        return scores
    for row in logs:
        if not isinstance(row, dict):
            continue
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        aid = extra.get("agent_id")
        if not aid:
            continue
        dec = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        scores[str(aid)] = {
            "composite": dec.get("composite"),
            "confidence": dec.get("confidence"),
            "stance": dec.get("stance"),
        }
    return scores


def build_tier0_summary(wf_output: dict[str, Any]) -> list[dict[str, Any]]:
    from schemas.tier0_contract import tier0_contracts_by_agent

    arb_scores = _arbitration_scores_by_agent(wf_output)
    contracts = wf_output.get("tier0_contracts")
    if isinstance(contracts, list) and contracts:
        idx = tier0_contracts_by_agent(wf_output)
        tier0 = [compact_agent_contract(c) for c in idx.values()]
        for entry in tier0:
            scores = arb_scores.get(str(entry.get("agent_id", "")))
            if not scores:
                continue
            for key in ("composite", "confidence", "stance"):
                if entry.get(key) is None and scores.get(key) is not None:
                    entry[key] = scores[key]
        if tier0:
            return tier0

    if arb_scores:
        return [
            {"agent_id": aid, "source": "arbitration", **scores}
            for aid, scores in arb_scores.items()
        ]

    signals = wf_output.get("proposed_signal", {}).get("params", {}).get("agent_signals")
    if isinstance(signals, list):
        return [
            {
                "agent_id": s.get("agent_id", "?"),
                "composite": s.get("composite"),
                "confidence": s.get("confidence"),
                "stance": s.get("stance"),
            }
            for s in signals
            if isinstance(s, dict)
        ]
    return []


def cot_snippets(wf_output: dict[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
    """Desk / arbitrator thought chains for the audit JSONL."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in wf_output.get("tier0_contracts") or []:
        if not isinstance(c, dict):
            continue
        aid = str(c.get("agent_id") or c.get("agent") or "")
        reasoning = c.get("reasoning")
        if aid and isinstance(reasoning, str) and reasoning.strip() and aid not in seen:
            seen.add(aid)
            out.append(
                {
                    "agent_id": aid,
                    "source": c.get("source") or "tier0",
                    "thought": reasoning.strip()[:400],
                }
            )
    for row in wf_output.get("reasoning_logs") or []:
        if not isinstance(row, dict) or len(out) >= limit:
            break
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        aid = str(extra.get("agent_id") or row.get("node") or "")
        thought = str(row.get("thought_process") or row.get("reasoning_chain") or "").strip()
        if not thought or aid in seen:
            continue
        if aid.startswith("market_scan") or thought.startswith("Backtest mode:"):
            continue
        seen.add(aid)
        out.append({"agent_id": aid, "source": "reasoning_log", "thought": thought[:400]})
    intent = wf_output.get("trade_intent")
    if isinstance(intent, dict):
        reasons = intent.get("reasons")
        if isinstance(reasons, list) and reasons:
            joined = "; ".join(str(x) for x in reasons[:4] if x)
            if joined:
                out.append(
                    {
                        "agent_id": "signal_arbitrator",
                        "source": "trade_intent",
                        "thought": joined[:400],
                    }
                )
    return out[:limit]
