"""Profile Agent — One-time LLM onboarding wizard.

Generates personalized Tier-0 agent weight deltas based on user trading style.

Usage:
    agent = ProfileAgent()
    result = agent.process({
        "risk_tolerance": "moderate",          # conservative|moderate|aggressive
        "time_horizon": "swing",               # scalping|intraday|swing|position
        "preferred_signals": "technical",      # technical|onchain|news|sentiment|mixed
        "leverage_comfort": "1-3x",            # 1x|1-3x|3-5x|5x+
        "assets": "majors_only"                # majors_only|majors_alts|full_universe
    })

Output:
    {
        "profile_id": "user_<hash>",
        "base": "AGENT_WEIGHTS_DEFAULT",
        "deltas": {"pattern_recognition_bot": "+0.10", "news_narrative_miner": "-0.05", ...},
        "effective_weights": {"monetary_sentinel": 0.05, "pattern_recognition_bot": 0.35, ...},
        "reasoning": "Pattern-based trader with news confirmation bias...",
        "narrative": "Technician"
    }
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from agents.registry import get_registry
from config.llm_env import llm_key_available

_reg = get_registry()
AGENT_WEIGHTS_DEFAULT: dict[str, float] = _reg.default_weights()
AGENT_LABELS: dict[str, str] = {s.name: f"{s.name} ({s.label})" for s in _reg.all_specs()}

# 5-Question weight delta rules
# Format: (risk, horizon, signals, leverage, assets) -> {agent_id: delta}
# Each delta is added to the base weight; final weights are re-normalized.

_WEIGHT_RULES: list[tuple[str, str, str, str, str, dict[str, float]]] = [
    # === CONSERVATIVE profiles ===
    # Conservative + long horizon: tilt → macro + fundamentals
    (
        "conservative",
        "position",
        "*",
        "*",
        "*",
        {
            "monetary_sentinel": 0.10,
            "technical_ta_engine": 0.05,
            "retail_hype_tracker": -0.03,
            "liquidity_order_flow": -0.05,
        },
    ),
    (
        "conservative",
        "swing",
        "*",
        "*",
        "*",
        {
            "monetary_sentinel": 0.05,
            "technical_ta_engine": 0.05,
            "retail_hype_tracker": -0.02,
            "liquidity_order_flow": -0.03,
        },
    ),
    # === AGGRESSIVE profiles ===
    # Aggressive + short horizon: tilt → momentum, retail, whale
    (
        "aggressive",
        "scalping",
        "*",
        "*",
        "*",
        {
            "pattern_recognition_bot": 0.10,
            "liquidity_order_flow": 0.10,
            "monetary_sentinel": -0.03,
            "news_narrative_miner": -0.03,
        },
    ),
    (
        "aggressive",
        "intraday",
        "*",
        "*",
        "*",
        {
            "pattern_recognition_bot": 0.05,
            "liquidity_order_flow": 0.05,
            "retail_hype_tracker": 0.03,
            "monetary_sentinel": -0.02,
        },
    ),
    (
        "aggressive",
        "*",
        "*",
        "5x+",
        "*",
        {
            "pattern_recognition_bot": 0.08,
            "liquidity_order_flow": 0.05,
            "retail_hype_tracker": 0.05,
            "news_narrative_miner": -0.03,
        },
    ),
    # === SIGNAL preference profiles ===
    (
        "*",
        "*",
        "technical",
        "*",
        "*",
        {
            "technical_ta_engine": 0.10,
            "pattern_recognition_bot": 0.05,
            "retail_hype_tracker": -0.05,
            "news_narrative_miner": -0.05,
        },
    ),
    (
        "*",
        "*",
        "onchain",
        "*",
        "*",
        {
            "whale_behavior_analyst": 0.15,
            "liquidity_order_flow": 0.05,
            "technical_ta_engine": -0.10,
            "pattern_recognition_bot": -0.05,
        },
    ),
    (
        "*",
        "*",
        "news",
        "*",
        "*",
        {
            "news_narrative_miner": 0.15,
            "retail_hype_tracker": 0.05,
            "technical_ta_engine": -0.10,
            "pattern_recognition_bot": -0.05,
        },
    ),
    (
        "*",
        "*",
        "sentiment",
        "*",
        "*",
        {
            "retail_hype_tracker": 0.15,
            "pro_bias_analyst": 0.05,
            "technical_ta_engine": -0.10,
            "pattern_recognition_bot": -0.05,
        },
    ),
    (
        "*",
        "*",
        "mixed",
        "*",
        "*",
        {
            "news_narrative_miner": 0.03,
            "retail_hype_tracker": 0.03,
            "pro_bias_analyst": 0.03,
            "technical_ta_engine": -0.05,
        },
    ),
    # === LEVERAGE profiles ===
    (
        "*",
        "*",
        "*",
        "1x",
        "*",
        {
            "technical_ta_engine": 0.05,
            "monetary_sentinel": 0.05,
            "retail_hype_tracker": -0.02,
            "liquidity_order_flow": -0.02,
        },
    ),
    (
        "*",
        "*",
        "*",
        "3-5x",
        "*",
        {
            "pattern_recognition_bot": 0.05,
            "retail_hype_tracker": 0.03,
            "liquidity_order_flow": 0.03,
            "monetary_sentinel": -0.03,
        },
    ),
    # === ASSET profiles ===
    (
        "*",
        "*",
        "*",
        "*",
        "majors_only",
        {
            "technical_ta_engine": 0.05,
            "retail_hype_tracker": -0.03,
            "whale_behavior_analyst": -0.03,
        },
    ),
    (
        "*",
        "*",
        "*",
        "*",
        "full_universe",
        {
            "retail_hype_tracker": 0.08,
            "pro_bias_analyst": 0.05,
            "whale_behavior_analyst": 0.05,
            "technical_ta_engine": -0.10,
        },
    ),
    (
        "*",
        "*",
        "*",
        "*",
        "majors_alts",
        {"pattern_recognition_bot": 0.03, "pro_bias_analyst": 0.03, "news_narrative_miner": 0.02},
    ),
]

# === Narrative labels for user-facing profile ===
_NARRATIVES: list[tuple[str, dict[str, str]]] = [
    (
        "0.65,0.70,0.55",
        {
            "conservative": "Defensive Investor",
            "moderate": "Balanced Technician",
            "aggressive": "Aggressive Momentum Trader",
        },
    ),
]

_NARRATIVE_BY_STYLE: dict[str, str] = {
    ("conservative", "position", "fundamental"): "Defensive Value Investor",
    ("conservative", "swing", "technical"): "Conservative Technician",
    ("moderate", "swing", "technical"): "Balanced Technician",
    ("moderate", "intraday", "mixed"): "Adaptive Trader",
    ("aggressive", "scalping", "technical"): "Scalping Technician",
    ("aggressive", "intraday", "sentiment"): "Sentiment Momentum Trader",
    ("aggressive", "swing", "onchain"): "On-Chain Alpha Hunter",
    ("aggressive", "intraday", "technical"): "Aggressive Momentum Trader",
    ("moderate", "intraday", "technical"): "Technical Swing Trader",
    ("conservative", "intraday", "technical"): "Conservative Swing Trader",
    ("moderate", "swing", "mixed"): "Diversified Analyst",
}

_NARRATIVE_DEFAULT = "Custom Profile"


def _match_wildcard(pattern: str, value: str) -> bool:
    """Simple wildcard match: '*' matches anything."""
    return pattern == "*" or pattern == value


def _compute_deltas(
    risk: str, horizon: str, signals: str, leverage: str, assets: str
) -> dict[str, float]:
    """Apply all matching rules, summing deltas."""
    out: dict[str, float] = {}
    for r, h, s, lev, a, delta in _WEIGHT_RULES:
        if (
            _match_wildcard(r, risk)
            and _match_wildcard(h, horizon)
            and _match_wildcard(s, signals)
            and _match_wildcard(lev, leverage)
            and _match_wildcard(a, assets)
        ):
            for k, v in delta.items():
                out[k] = out.get(k, 0.0) + v
    return out


def _apply_deltas(base: dict[str, float], deltas: dict[str, float]) -> dict[str, float]:
    """Apply deltas to base weights and re-normalize to sum=1.0."""
    temp = dict(base)
    for k, v in deltas.items():
        temp[k] = max(0.01, min(0.50, temp.get(k, 0.05) + v))

    # Normalize
    total = sum(temp.values())
    if total <= 0:
        return dict(base)
    return {k: round(v / total, 4) for k, v in temp.items()}


def _resolve_narrative(risk: str, horizon: str, signals: str) -> str:
    key = (risk, horizon, signals)
    direct = _NARRATIVE_BY_STYLE.get(key)
    if direct:
        return direct
    # Fuzzy fallback
    if risk == "aggressive":
        return "Aggressive Momentum Trader"
    if risk == "conservative":
        return "Defensive Investor"
    return _NARRATIVE_DEFAULT


class ProfileAgent:
    """One-shot LLM-assisted profile generator.

    Operates in two modes:
    1. **Rule-based** (default): deterministic weight deltas from question matrix
    2. **LLM-enhanced** (when AIMM_LLM_PROFILE=1): uses Hermes/OpenAI for richer reasoning
    """

    def __init__(self) -> None:
        self.llm_enabled = llm_key_available() and (os.getenv("AIMM_LLM_PROFILE", "0") == "1")

    # ------------------------------------------------------------------
    def _build_prompt(self, answers: dict[str, str]) -> str:
        return (
            "You are a trading profile analyst. Based on these user answers, "
            "output a JSON object with agent weight deltas and reasoning.\n\n"
            f"User Profile:\n"
            f"- Risk Tolerance: {answers.get('risk_tolerance', 'moderate')}\n"
            f"- Time Horizon: {answers.get('time_horizon', 'swing')}\n"
            f"- Preferred Signals: {answers.get('preferred_signals', 'technical')}\n"
            f"- Leverage Comfort: {answers.get('leverage_comfort', '3-5x')}\n"
            f"- Asset Focus: {answers.get('assets', 'majors_only')}\n\n"
            "Available agents with current base weights:\n"
            + "\n".join(
                f"  {aid}: {AGENT_LABELS.get(aid, aid)} (base={AGENT_WEIGHTS_DEFAULT.get(aid, 0.05):.2f})"
                for aid in sorted(AGENT_WEIGHTS_DEFAULT)
            )
            + "\n\n"
            "Rules:\n"
            "1. Deltas are ADDITIVE to base weights (e.g., +0.10 means add 0.10)\n"
            "2. Final effective weight = max(0.01, min(0.50, base + delta))\n"
            "3. Deltas must be in the range [-0.15, +0.15]\n"
            "4. The final weights will be re-normalized to sum 1.0\n"
            "5. Provide a concise reasoning string (1-2 sentences)\n"
            "6. Output valid JSON ONLY, no markdown:\n"
            '{"deltas": {"pattern_recognition_bot": 0.10, "technical_ta_engine": -0.05}, "reasoning": "...", "narrative": "..."}'
        )

    # ------------------------------------------------------------------
    def _llm_generate(self, answers: dict[str, str]) -> dict[str, Any] | None:
        """Call LLM for profile generation.

        Falls back to None if LLM unavailable / error → caller uses rule-based fallback.
        """
        try:
            from llm.openai_client import run_tool_calling_chat

            prompt = self._build_prompt(answers)
            response = run_tool_calling_chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=800,
            )
            content = response.choices[0].message.content if response else None
            if not content:
                return None
            # Strip markdown fences if present
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
                cleaned = cleaned.rsplit("```", 1)[0].strip()
            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                return None
            return parsed
        except Exception:
            return None

    # ------------------------------------------------------------------
    def process(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Generate personalized agent weights.

        Args:
            input_data: dict with keys:
                - risk_tolerance: conservative|moderate|aggressive
                - time_horizon: scalping|intraday|swing|position
                - preferred_signals: technical|onchain|news|sentiment|mixed
                - leverage_comfort: 1x|1-3x|3-5x|5x+
                - assets: majors_only|majors_alts|full_universe

        Returns:
            Profile JSON (see module docstring)
        """
        risk = str(input_data.get("risk_tolerance", "moderate")).lower()
        horizon = str(input_data.get("time_horizon", "swing")).lower()
        signals = str(input_data.get("preferred_signals", "technical")).lower()
        leverage = str(input_data.get("leverage_comfort", "1-3x")).lower()
        assets = str(input_data.get("assets", "majors_only")).lower()

        # Try LLM path first (optional, controlled by env)
        llm_result: dict[str, Any] | None = None
        if self.llm_enabled:
            llm_result = self._llm_generate(
                {
                    "risk_tolerance": risk,
                    "time_horizon": horizon,
                    "preferred_signals": signals,
                    "leverage_comfort": leverage,
                    "assets": assets,
                }
            )

        if llm_result and isinstance(llm_result.get("deltas"), dict):
            deltas_raw = {str(k): float(v) for k, v in llm_result["deltas"].items()}
            deltas = deltas_raw
            reasoning = str(llm_result.get("reasoning", "LLM-generated profile."))
            narrative = str(llm_result.get("narrative", _resolve_narrative(risk, horizon, signals)))
            source = "llm"
        else:
            # Rule-based deterministic path
            deltas = _compute_deltas(risk, horizon, signals, leverage, assets)
            reasoning = (
                f"Rule-based profile: {risk} risk, {horizon} horizon, "
                f"{signals} signals, {leverage} leverage, {assets} assets."
            )
            narrative = _resolve_narrative(risk, horizon, signals)
            source = "rule"

        effective = _apply_deltas(AGENT_WEIGHTS_DEFAULT, deltas)

        # Profile ID
        raw_id = f"{risk}|{horizon}|{signals}|{leverage}|{assets}|{source}|{time.time()}"
        profile_id = "user_" + hashlib.sha256(raw_id.encode()).hexdigest()[:12]

        return {
            "profile_id": profile_id,
            "base": "AGENT_WEIGHTS_DEFAULT",
            "deltas": {k: f"{v:+0.2f}" for k, v in sorted(deltas.items())},
            "effective_weights": effective,
            "narrative": narrative,
            "source": source,
            "reasoning": reasoning,
            "timestamp": int(time.time()),
            "input": {
                "risk_tolerance": risk,
                "time_horizon": horizon,
                "preferred_signals": signals,
                "leverage_comfort": leverage,
                "assets": assets,
            },
        }


__all__ = ["ProfileAgent", "AGENT_WEIGHTS_DEFAULT", "AGENT_LABELS"]
