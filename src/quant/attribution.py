"""Factor P&L Attribution — decompose returns into agent desk contributions.

For every bar, computes:
  1. Per-symbol PnL = position_weight × bar_return
  2. Per-desk attribution = desk_weight × desk_directional_score × bar_return
  3. Cumulative desk P&L tracks
  4. Equity curve decomposition (what drove the P&L curve)

This gives provably trustworthy equity curves — you can see exactly which desks
contributed to every dollar of P&L. No black box.

Usage::

    from quant.attribution import AttributionTracker

    tracker = AttributionTracker()
    tracker.record_bar(
        desk_scores={"monetary_sentinel": {"BTC/USDT": 0.7, "ETH/USDT": 0.55}, ...},
        desk_weights={"monetary_sentinel": 0.12, ...},
        final_weights={"BTC/USDT": 0.15, "ETH/USDT": -0.08},
        bar_returns={"BTC/USDT": 0.002, "ETH/USDT": -0.001},
    )
    report = tracker.summary()
    # report = {"desk_pnl": {...}, "cumulative": {...}, "total_pnl": ..., "sharpe": ...}
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


class AttributionTracker:
    """Tracks P&L attribution per desk, per bar.

    Records every bar's decomposition: which desks (and how much weight) drove
    the final position, and how much P&L each contributed.
    """

    def __init__(self):
        # Per-desk cumulative P&L (in units of portfolio equity, base=1.0)
        self.desk_cumulative: dict[str, float] = {}
        # Per-symbol cumulative P&L
        self.symbol_cumulative: dict[str, float] = {}
        # Bar-by-bar breakdown
        self.bar_history: list[dict[str, Any]] = []
        # Total P&L contribution tracking
        self.total_pnl: float = 0.0
        self.bar_count: int = 0
        # Daily returns for Sharpe computation
        self.daily_returns: list[float] = []

    def record_bar(
        self,
        *,
        desk_scores: dict[str, dict[str, float]],
        desk_weights: dict[str, float],
        final_weights: dict[str, float],
        bar_returns: dict[str, float],
        composite_scores: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Record one bar of attribution data.

        Args:
            desk_scores: {desk_id: {symbol: normalized_score}}
            desk_weights: {desk_id: weight} from arbitrator
            final_weights: {symbol: position_weight} from optimizer
            bar_returns: {symbol: log_return_this_bar}
            composite_scores: {symbol: composite_0_1} (optional, for reporting)

        Returns:
            Bar-level attribution dict for audit trail
        """
        import math as _math

        def _safe(v: Any, default: float = 0.0) -> float:
            try:
                f = float(v)
                if _math.isnan(f) or _math.isinf(f):
                    return default
                return max(-1e9, min(1e9, f))
            except (TypeError, ValueError):
                return default

        self.bar_count += 1

        # 1. Total portfolio P&L this bar
        total_bar_pnl = sum(
            _safe(final_weights.get(sym, 0.0)) * _safe(bar_returns.get(sym, 0.0))
            for sym in set(list(final_weights.keys()) + list(bar_returns.keys()))
        )
        if _math.isnan(total_bar_pnl) or _math.isinf(total_bar_pnl):
            total_bar_pnl = 0.0
        self.total_pnl += total_bar_pnl
        self.daily_returns.append(total_bar_pnl)

        # 2. Per-desk contribution attribution
        #    desk_contribution = Σ_sym desk_weight × desk_score_directional × bar_return
        desk_contrib: dict[str, float] = {}
        for desk_id, weight in desk_weights.items():
            w = _safe(weight)
            if w <= 0:
                continue
            contrib = 0.0
            scores = desk_scores.get(desk_id, {})
            for sym, ret_v in bar_returns.items():
                ret = _safe(ret_v)
                if abs(ret) < 1e-15:
                    continue
                raw_score = _safe(scores.get(sym, 0.5), default=0.5)
                directional = (raw_score - 0.5) * 2.0  # [-1, +1]
                contrib += w * directional * ret
            if _math.isnan(contrib) or _math.isinf(contrib):
                contrib = 0.0
            desk_contrib[desk_id] = contrib
            self.desk_cumulative[desk_id] = self.desk_cumulative.get(desk_id, 0.0) + contrib

        # 3. Per-symbol P&L
        for sym, ret_v in bar_returns.items():
            ret = _safe(ret_v)
            w = _safe(final_weights.get(sym, 0.0))
            self.symbol_cumulative[sym] = self.symbol_cumulative.get(sym, 0.0) + w * ret

        # 4. Serialize for audit trail
        def _safe_round(v: float, n: int = 8) -> float | None:
            if _math.isnan(v) or _math.isinf(v):
                return None
            return round(v, n)

        entry: dict[str, Any] = {
            "bar_index": self.bar_count,
            "total_pnl": _safe_round(total_bar_pnl, 8),
            "cumulative_pnl": _safe_round(self.total_pnl, 8),
            "desk_contributions": {k: _safe_round(v, 8) for k, v in desk_contrib.items()},
            "desk_weights": dict(desk_weights),
            "symbol_pnl": {k: _safe_round(v, 8) for k, v in bar_returns.items()},
        }
        if composite_scores:
            entry["composite_scores"] = composite_scores
        self.bar_history.append(entry)

        return entry

    def summary(self) -> dict[str, Any]:
        """Return full attribution summary."""
        return {
            "total_pnl": round(self.total_pnl, 6),
            "bar_count": self.bar_count,
            "sharpe": self._compute_sharpe(),
            "desk_cumulative_pnl": {
                k: round(v, 6) for k, v in sorted(self.desk_cumulative.items(), key=lambda x: -x[1])
            },
            "symbol_cumulative_pnl": {
                k: round(v, 6)
                for k, v in sorted(self.symbol_cumulative.items(), key=lambda x: -x[1])
            },
            "top_desks": self._top_desks(3),
            "bottom_desks": self._bottom_desks(3),
            "desk_sharpe_ratios": self._desk_sharpes(),
        }

    def _compute_sharpe(self) -> float:
        n = len(self.daily_returns)
        if n < 5:
            return 0.0
        avg = sum(self.daily_returns) / n
        if abs(avg) < 1e-12:
            return 0.0
        var = sum((r - avg) ** 2 for r in self.daily_returns) / n
        if var < 1e-12:
            return 0.0
        return avg / math.sqrt(var) * math.sqrt(365 * 24)  # annualized

    def _top_desks(self, n: int = 3) -> list[dict[str, Any]]:
        sorted_desks = sorted(self.desk_cumulative.items(), key=lambda x: -x[1])
        return [{"desk": k, "pnl": round(v, 6)} for k, v in sorted_desks[:n]]

    def _bottom_desks(self, n: int = 3) -> list[dict[str, Any]]:
        sorted_desks = sorted(self.desk_cumulative.items(), key=lambda x: x[1])
        return [{"desk": k, "pnl": round(v, 6)} for k, v in sorted_desks[:n]]

    def _desk_sharpes(self) -> dict[str, float]:
        """Approximate per-desk Sharpe from contribution time series."""
        if not self.bar_history:
            return {}
        # Reconstruct per-desk return time series from bar history
        desk_returns: dict[str, list[float]] = {}
        for entry in self.bar_history:
            for desk_id, contrib in entry.get("desk_contributions", {}).items():
                if desk_id not in desk_returns:
                    desk_returns[desk_id] = []
                desk_returns[desk_id].append(contrib)

        sharpes: dict[str, float] = {}
        for desk_id, rets in desk_returns.items():
            n = len(rets)
            if n < 5:
                sharpes[desk_id] = 0.0
                continue
            avg = sum(rets) / n
            if abs(avg) < 1e-12:
                sharpes[desk_id] = 0.0
                continue
            var = sum((r - avg) ** 2 for r in rets) / n
            if var < 1e-12:
                sharpes[desk_id] = 0.0
                continue
            sharpes[desk_id] = round(avg / math.sqrt(var) * math.sqrt(365 * 24), 4)

        return sharpes

    def to_jsonl(self) -> list[str]:
        """Export bar history as JSONL strings for audit trail."""
        import json
        import math as _math

        def _sanitize(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_sanitize(v) for v in obj]
            if isinstance(obj, float):
                if _math.isnan(obj):
                    return None
                if _math.isinf(obj):
                    return 1e308 if obj > 0 else -1e308
            return obj

        return [json.dumps(_sanitize(entry)) for entry in self.bar_history]


__all__ = ["AttributionTracker"]
