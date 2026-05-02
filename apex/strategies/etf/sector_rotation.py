"""
[ETF-01] SectorRotation — SPDR 11-sector relative strength
Weekly rebalance | Low friction | Suitable for 30-40% of account
"""
from typing import Optional, List, Dict
import logging
from datetime import datetime

from core.interfaces import Signal, Bar, Side, ProductType
from strategies.base import BaseStrategy
from strategies.registry import strategy_registry

logger = logging.getLogger("ETF-01")

SPDR_SECTORS = {
    "XLK":  "Technology",
    "XLV":  "Healthcare",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLB":  "Materials",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "XLC":  "Communication",
}


@strategy_registry.register("sector_rotation")
class SectorRotationStrategy(BaseStrategy):
    PRODUCT = ProductType.ETF
    PARAMS  = {
        "lookback_weeks":  4,     # Weeks for relative strength calc
        "top_n":           2,     # Hold top N sectors
        "rebalance_day":   0,     # 0=Monday
        "min_rs_score":    0.0,   # Minimum RS score to enter (0 = any positive)
        "atr_stop":        2.0,   # Wider stop for ETF (slower moves)
        "rr_target":       1.5,
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._last_rebalance: Optional[str] = None
        self._holdings: List[str] = []

    def on_bar(self, symbol: str, bars: List[Bar], ctx: dict) -> Optional[Signal]:
        # SectorRotation is portfolio-level — called with symbol="PORTFOLIO"
        # ctx must contain {"sector_bars": {"XLK": [Bar,...], ...}}
        if symbol != "PORTFOLIO":
            return None

        today = datetime.utcnow().strftime("%Y-%m-%d")
        now   = datetime.utcnow()

        # Only rebalance on configured day (default Monday)
        if now.weekday() != self.params["rebalance_day"]:
            return None
        if self._last_rebalance == today:
            return None   # Already rebalanced today

        sector_bars: Dict[str, List[Bar]] = ctx.get("sector_bars", {})
        if not sector_bars:
            return None

        # ── Calculate relative strength scores ────────────────────
        lookback = self.params["lookback_weeks"] * 5   # Trading days
        scores   = {}
        for ticker, s_bars in sector_bars.items():
            if len(s_bars) < lookback + 1:
                continue
            start_price = s_bars[-lookback].close
            end_price   = s_bars[-1].close
            if start_price > 0:
                scores[ticker] = (end_price - start_price) / start_price * 100

        if not scores:
            return None

        # ── Rank and select top N ─────────────────────────────────
        ranked  = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_etfs = [t for t, s in ranked[:self.params["top_n"]]
                    if s >= self.params["min_rs_score"]]

        # ── Generate BUY signals for new entries ──────────────────
        signals = []
        for ticker in top_etfs:
            if ticker in self._holdings:
                continue   # Already holding
            etf_bars = sector_bars.get(ticker, [])
            if not etf_bars:
                continue
            latest = etf_bars[-1]
            atr    = latest.atr if latest.atr > 0 else latest.close * 0.015
            stop   = latest.close - atr * self.params["atr_stop"]
            tp     = latest.close + atr * self.params["atr_stop"] * self.params["rr_target"]
            rs     = scores.get(ticker, 0)

            sig = Signal(
                symbol      = ticker,
                side        = Side.BUY,
                strategy_id = "sector_rotation",
                product     = ProductType.ETF,
                confidence  = min(0.85, 0.65 + rs / 100),
                entry_price = latest.close,
                stop_loss   = round(stop, 2),
                take_profit = round(tp,   2),
                shadow      = getattr(self, "_shadow", False),
                meta        = {
                    "rs_score": round(rs, 2),
                    "rank":     [t for t, _ in ranked].index(ticker) + 1,
                    "sector":   SPDR_SECTORS.get(ticker, ""),
                }
            )
            signals.append(sig)
            logger.info(f"[ETF-01] {ticker} ({SPDR_SECTORS.get(ticker)}) RS={rs:.1f}% rank={sig.meta['rank']}")

        self._last_rebalance = today
        self._holdings       = top_etfs

        # Return first signal; signal_fusion will call multiple times for portfolio strategies
        return signals[0] if signals else None

    def get_exit_signals(self, ctx: dict) -> List[Signal]:
        """Return SELL signals for holdings that dropped out of top N."""
        sector_bars = ctx.get("sector_bars", {})
        exits = []
        for ticker in list(self._holdings):
            etf_bars = sector_bars.get(ticker, [])
            if not etf_bars:
                continue
            latest = etf_bars[-1]
            # Check if stop hit
            current_pos = ctx.get("positions", {}).get(ticker)
            if current_pos and latest.close <= current_pos.get("stop_loss", 0):
                exits.append(Signal(
                    symbol=ticker, side=Side.SELL,
                    strategy_id="sector_rotation", product=ProductType.ETF,
                    entry_price=latest.close, confidence=0.9,
                    shadow=getattr(self, "_shadow", False),
                ))
                self._holdings.remove(ticker)
        return exits
