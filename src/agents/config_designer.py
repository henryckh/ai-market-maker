"""Config Designer — outside the trading graph.

Chat to seed, review, and refine deploy JSON. Does not place trades.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Config Designer for an agentic crypto trading system (AIMM).

Your job is to help the user design a clean deploy JSON configuration. You are
NOT inside the live trading graph. You only produce configuration.

## Deploy JSON schema (preferred)

```json
{
  "profile": { "profile_id": "macro_tilt" },
  "agents": {
    "technical_ta_engine": { "weight": 0.55, "llm_enabled": true, "enabled": true },
    "pattern_recognition_bot": { "weight": 0.15, "llm_enabled": false, "enabled": true },
    "monetary_sentinel": { "weight": 0.25, "llm_enabled": false, "enabled": true }
  },
  "execution": {
    "use_llm_synthesis": true,
    "desk_debate_llm": false,
    "arbitrator_llm": true,
    "allows_short": true,
    "leverage": 2.0
  },
  "decision_threshold": {
    "buy": { "min_composite": 53, "min_confidence": 16 },
    "sell": { "max_composite": 41, "min_confidence": 26 },
    "hold": { "else": true },
    "alignment_gating": {
      "enabled": true,
      "min_factors_for_directional": 2,
      "risk_override_if_blocked": true
    },
    "ta_led": {
      "enabled": true,
      "agent_id": "technical_ta_engine",
      "buy_min_composite": 57,
      "sell_max_composite": 43,
      "min_confidence": 14
    }
  }
}
```

## Available agents (use these keys only)

- monetary_sentinel — Macro / liquidity regime
- news_narrative_miner — News & narrative shock
- pattern_recognition_bot — Chart patterns / setup quality
- statistical_alpha_engine — Cross-sectional / factor alpha
- technical_ta_engine — TA-Lib indicators (RSI, MACD, …)
- retail_hype_tracker — Retail FOMO / divergence
- pro_bias_analyst — Smart money / funding / OI
- whale_behavior_analyst — Whale concentration / dump risk
- liquidity_order_flow — Depth, slippage, imbalance

## Style guidance

- scalp: tight thresholds, high TA weight, short horizon, low leverage
- daytrade: balanced TA + pattern, moderate thresholds
- swing: more macro + news weight, looser thresholds
- buffett / conservative: few agents, high confidence bars, low leverage
- macro_tilt: TA-led with macro support (default research profile)

## Rules

1. Always reply with a short explanation, then a single JSON block when proposing a config.
2. Agent weights for enabled agents should sum roughly to 1.0 (0.9–1.1 ok).
3. Prefer readable agent names — never invent numeric ids like "2.3" in new configs.
4. Never include API keys or secrets in the JSON.
5. If the user asks to review an existing JSON, point out risks (over-concentration, too many LLM agents, thresholds that never fire, etc.).
6. Keep leverage modest unless the user explicitly wants aggressive settings.
7. Agentic hedge-fund configs should set execution.arbitrator_llm true;
   measurement/conservative configs should set it false. Desk CoT is
   agents.*.llm_enabled. Desk weights are one-time config, not retuned each bar.
"""


STYLE_SEEDS: dict[str, dict[str, Any]] = {
    "macro_tilt": {
        "profile": {"profile_id": "macro_tilt"},
        "agents": {
            "technical_ta_engine": {"weight": 0.55, "llm_enabled": True, "enabled": True},
            "pattern_recognition_bot": {"weight": 0.15, "llm_enabled": False, "enabled": True},
            "monetary_sentinel": {"weight": 0.25, "llm_enabled": False, "enabled": True},
        },
        "execution": {
            "use_llm_synthesis": True,
            "desk_debate_llm": False,
            "arbitrator_llm": True,
            "allows_short": True,
            "leverage": 2.0,
        },
        "decision_threshold": {
            "buy": {"min_composite": 53, "min_confidence": 16},
            "sell": {"max_composite": 41, "min_confidence": 26},
            "hold": {"else": True},
            "alignment_gating": {
                "enabled": True,
                "min_factors_for_directional": 2,
                "risk_override_if_blocked": True,
            },
            "ta_led": {
                "enabled": True,
                "agent_id": "technical_ta_engine",
                "buy_min_composite": 57,
                "sell_max_composite": 43,
                "min_confidence": 14,
            },
        },
    },
    "ohlcv_measurement": {
        "profile": {"profile_id": "ohlcv_only"},
        "agents": {
            "technical_ta_engine": {"weight": 0.75, "llm_enabled": False, "enabled": True},
            "pattern_recognition_bot": {"weight": 0.25, "llm_enabled": False, "enabled": True},
        },
        "execution": {
            "use_llm_synthesis": False,
            "desk_debate_llm": False,
            "arbitrator_llm": False,
            "allows_short": True,
            "leverage": 1.5,
        },
    },
    "conservative": {
        "profile": {"profile_id": "conservative"},
        "agents": {
            "technical_ta_engine": {"weight": 0.5, "llm_enabled": False, "enabled": True},
            "monetary_sentinel": {"weight": 0.3, "llm_enabled": False, "enabled": True},
            "liquidity_order_flow": {"weight": 0.2, "llm_enabled": False, "enabled": True},
        },
        "execution": {
            "use_llm_synthesis": False,
            "desk_debate_llm": False,
            "arbitrator_llm": False,
            "allows_short": False,
            "leverage": 1.0,
        },
        "decision_threshold": {
            "buy": {"min_composite": 60, "min_confidence": 40},
            "sell": {"max_composite": 40, "min_confidence": 40},
            "hold": {"else": True},
            "alignment_gating": {
                "enabled": True,
                "min_factors_for_directional": 3,
                "risk_override_if_blocked": True,
            },
        },
    },
}


@dataclass
class ConfigDesignerTurn:
    role: str  # user | assistant | system
    content: str


@dataclass
class ConfigDesignerResult:
    reply: str
    draft_config: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    style: str | None = None


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Pull the last fenced or raw JSON object from model output."""
    fence = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidates = fence or re.findall(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", text, flags=re.S)
    for raw in reversed(candidates):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and ("agents" in obj or "execution" in obj):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def validate_deploy_draft(cfg: dict[str, Any]) -> list[str]:
    """Lightweight validation — returns human warnings, not hard errors."""
    warnings: list[str] = []
    from agents.registry import get_registry

    known = set(get_registry().names())

    agents = cfg.get("agents")
    if isinstance(agents, dict):
        total_w = 0.0
        llm_count = 0
        for key, meta in agents.items():
            if key not in known:
                warnings.append(f"Unknown agent key: {key}")
            if not isinstance(meta, dict):
                continue
            if meta.get("enabled", True) is False:
                continue
            try:
                total_w += float(meta.get("weight") or 0)
            except (TypeError, ValueError):
                warnings.append(f"Invalid weight for {key}")
            if meta.get("llm_enabled"):
                llm_count += 1
        if total_w and (total_w < 0.85 or total_w > 1.15):
            warnings.append(f"Enabled agent weights sum to {total_w:.2f} (prefer ~1.0)")
        if llm_count > 3:
            warnings.append(f"{llm_count} agents have llm_enabled — cost may be high")
    else:
        warnings.append("No agents block")

    execution = cfg.get("execution") if isinstance(cfg.get("execution"), dict) else {}
    lev = execution.get("leverage")
    try:
        if lev is not None and float(lev) > 5:
            warnings.append(f"Leverage {lev} is aggressive")
    except (TypeError, ValueError):
        pass

    thr = cfg.get("decision_threshold") if isinstance(cfg.get("decision_threshold"), dict) else {}
    buy = thr.get("buy") if isinstance(thr.get("buy"), dict) else {}
    sell = thr.get("sell") if isinstance(thr.get("sell"), dict) else {}
    try:
        if buy.get("min_composite") is not None and sell.get("max_composite") is not None:
            if float(buy["min_composite"]) <= float(sell["max_composite"]):
                warnings.append("buy.min_composite should be greater than sell.max_composite")
    except (TypeError, ValueError):
        pass

    return warnings


class ConfigDesignerAgent:
    """Outside-graph specialist: chat → deploy JSON draft."""

    name = "config_designer"
    role = "strategy_config_specialist"

    def seed_style(self, style: str) -> ConfigDesignerResult:
        key = (style or "").strip().lower().replace("-", "_")
        aliases = {
            "measurement": "ohlcv_measurement",
            "ohlcv": "ohlcv_measurement",
            "ohlcv_only": "ohlcv_measurement",
            "buffett": "conservative",
            "safe": "conservative",
        }
        key = aliases.get(key, key)
        cfg = STYLE_SEEDS.get(key)
        if cfg is None:
            available = ", ".join(sorted(STYLE_SEEDS))
            return ConfigDesignerResult(
                reply=f"Unknown style '{style}'. Available: {available}",
                warnings=[f"unknown_style:{style}"],
            )
        draft = copy.deepcopy(cfg)
        warnings = validate_deploy_draft(draft)
        return ConfigDesignerResult(
            reply=f"Seeded deploy config for style `{key}`. Review and adjust weights/thresholds as needed.",
            draft_config=draft,
            warnings=warnings,
            style=key,
        )

    def review(self, cfg: dict[str, Any]) -> ConfigDesignerResult:
        warnings = validate_deploy_draft(cfg)
        if warnings:
            body = "Review complete. Issues to consider:\n" + "\n".join(f"- {w}" for w in warnings)
        else:
            body = "Review complete. No structural issues found in the draft."
        return ConfigDesignerResult(reply=body, draft_config=cfg, warnings=warnings)

    def chat(
        self,
        message: str,
        *,
        history: list[ConfigDesignerTurn] | None = None,
        current_config: dict[str, Any] | None = None,
    ) -> ConfigDesignerResult:
        """LLM-backed design turn. Falls back to seed/review heuristics without a key."""
        msg = (message or "").strip()
        if not msg:
            return ConfigDesignerResult(
                reply="Send a design request or paste a deploy JSON to review."
            )
        lower = msg.lower()
        if lower.startswith("style:") or lower.startswith("preset:"):
            style = msg.split(":", 1)[1].strip()
            return self.seed_style(style)
        if lower in STYLE_SEEDS or lower in ("measurement", "ohlcv", "buffett", "safe"):
            return self.seed_style(lower)

        pasted = _extract_json_block(msg)
        if pasted is not None and any(k in lower for k in ("review", "check", "validate")):
            return self.review(pasted)

        # LLM path
        try:
            from llm.agent_llm_client import check_api_key
            from llm.openai_client import run_tool_calling_chat
        except Exception as e:
            return ConfigDesignerResult(
                reply=(
                    f"LLM client unavailable ({e}). "
                    "Use `style: macro_tilt` / `style: conservative` / `style: ohlcv_measurement` "
                    "or paste JSON with the word 'review'."
                ),
                warnings=["llm_unavailable"],
            )

        key_err = check_api_key()
        if key_err:
            if pasted is not None:
                return self.review(pasted)
            return ConfigDesignerResult(
                reply=(
                    f"No LLM API key configured ({key_err}). "
                    "Try: `style: macro_tilt`, `style: conservative`, or paste JSON + 'review'."
                ),
                draft_config=STYLE_SEEDS.get("macro_tilt"),
                warnings=["no_api_key"],
                style="macro_tilt",
            )

        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if current_config:
            messages.append(
                {
                    "role": "system",
                    "content": "Current deploy config:\n```json\n"
                    + json.dumps(current_config, indent=2)
                    + "\n```",
                }
            )
        for turn in history or []:
            if turn.role in ("user", "assistant") and turn.content:
                messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": msg})

        try:
            result = run_tool_calling_chat(
                messages=messages,
                tools=[],
                temperature=0.2,
                max_tokens=1200,
            )
            reply = ""
            if isinstance(result, dict):
                reply = str(result.get("content") or result.get("text") or "")
            else:
                reply = str(result)
        except Exception as e:
            logger.exception("config designer LLM failed")
            return ConfigDesignerResult(
                reply=f"LLM error: {e}", warnings=[f"llm_error:{type(e).__name__}"]
            )

        draft = _extract_json_block(reply) or pasted
        warnings = validate_deploy_draft(draft) if draft else []
        return ConfigDesignerResult(
            reply=reply or "(empty model response)", draft_config=draft, warnings=warnings
        )


__all__ = [
    "ConfigDesignerAgent",
    "ConfigDesignerResult",
    "ConfigDesignerTurn",
    "STYLE_SEEDS",
    "validate_deploy_draft",
]
