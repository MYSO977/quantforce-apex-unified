"""
[STK-01] MomentumSwing — Russell 2000 Momentum + RVOL
Hold: 1–4 days | Risk: 2.5% account | RR >= 2:1
"""
from typing import Optional, List
import logging

from core.interfaces import Signal, Bar, Side, ProductType, SignalStatus
from strategies.base import BaseStrategy
from strategies.registry import strategy_registry

logger = logging.getLogger("STK-01")


@strategy_registry.register("momentum_swing")
class MomentumSwingStrategy(BaseStrategy):
    PRODUCT = ProductType.STOCK
    PARAMS  = {
        "rvol_min":   2.0,
        "atr_stop":   1.5,
        "rr_target":  2.0,
        "lookback":   20,
        "min_price":  3.0,
        "max_price":  80.0,
        "news_boost": 6.0,
    }

    def on_bar(self, symbol: str, bars: List[Bar], ctx: dict) -> Optional[Signal]:
        if len(bars) < self.params["lookback"] + 1:
            return None

        latest = bars[-1]
        price  = latest.close

        if not (self.params["min_price"] <= price <= self.params["max_price"]):
            return None

        if latest.rvol < self.params["rvol_min"]:
            return None

        recent_high = max(b.high for b in bars[-self.params["lookback"]-1:-1])
        if latest.close <= recent_high:
            return None

        atr   = latest.atr if latest.atr > 0 else price * 0.02
        stop  = price - atr * self.params["atr_stop"]
        tp    = price + atr * self.params["atr_stop"] * self.params["rr_target"]

        news_score = ctx.get("news_scores", {}).get(symbol, 0.0)
        confidence = 0.65
        if news_score >= self.params["news_boost"]:
            confidence = min(0.95, confidence + 0.15)
        if latest.rvol >= self.params["rvol_min"] * 1.5:
            confidence = min(0.95, confidence + 0.10)

        sig = Signal(
            symbol      = symbol,
            side        = Side.BUY,
            strategy_id = "momentum_swing",
            product     = ProductType.STOCK,
            confidence  = confidence,
            entry_price = price,
            stop_loss   = round(stop, 2),
            take_profit = round(tp, 2),
            news_score  = news_score,
            rvol        = latest.rvol,
            atr         = atr,
            shadow      = getattr(self, "_shadow", False),
        )
        logger.info(f"[STK-01] {symbol} BUY @{price:.2f} SL={stop:.2f} TP={tp:.2f} RVOL={latest.rvol:.1f}")
        return sig

    def on_news(self, symbol: str, score: float, ctx: dict) -> Optional[Signal]:
        if score < 8.5:
            return None
        bars = ctx.get("bars", {}).get(symbol, [])
        if not bars:
            return None
        latest = bars[-1]
        if latest.rvol < self.params["rvol_min"] * 0.8:
            return None
        ctx["news_scores"] = {symbol: score}
        return self.on_bar(symbol, bars, ctx)
