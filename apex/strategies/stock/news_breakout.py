"""
[STK-02] NewsBreakout — News Catalyst + Gap Confirmation
Groq score >= 7.5 + opening gap >= 3% + 15min pullback entry
Hold: 1–3 days | Best for: biotech/small-cap catalysts
"""
from typing import Optional, List
import logging

from core.interfaces import Signal, Bar, Side, ProductType
from strategies.base import BaseStrategy
from strategies.registry import strategy_registry

logger = logging.getLogger("STK-02")


@strategy_registry.register("news_breakout")
class NewsBreakoutStrategy(BaseStrategy):
    PRODUCT = ProductType.STOCK
    PARAMS  = {
        "news_min":       7.5,   # Groq score threshold
        "gap_min_pct":    3.0,   # Minimum gap % at open
        "rvol_min":       1.5,   # RVOL (relaxed — news drives it)
        "atr_stop":       1.2,   # Tighter stop on news plays
        "rr_target":      2.5,   # Higher RR target
        "min_price":      2.0,   # Allow lower-priced biotech
        "max_price":      80.0,
        "biotech_halve":  True,  # Halve position for biotech (high vol)
    }

    # Catalyst type weights added to base confidence
    CATALYST_BOOST = {
        "fda":        0.20,
        "clinical":   0.18,
        "earnings":   0.15,
        "merger":     0.15,
        "acquisition":0.15,
        "contract":   0.10,
        "upgrade":    0.08,
    }

    def on_bar(self, symbol: str, bars: List[Bar], ctx: dict) -> Optional[Signal]:
        # NewsBreakout requires news score — skip pure bar calls
        news_score = ctx.get("news_scores", {}).get(symbol, 0.0)
        if news_score < self.params["news_min"]:
            return None
        return self._evaluate(symbol, bars, ctx, news_score)

    def on_news(self, symbol: str, score: float, ctx: dict) -> Optional[Signal]:
        if score < self.params["news_min"]:
            return None
        bars = ctx.get("bars", {}).get(symbol, [])
        if len(bars) < 3:
            return None
        return self._evaluate(symbol, bars, ctx, score)

    def _evaluate(self, symbol, bars, ctx, news_score) -> Optional[Signal]:
        if len(bars) < 3:
            return None

        latest = bars[-1]
        prev   = bars[-2]
        price  = latest.close

        # ── Price range ───────────────────────────────────────────
        if not (self.params["min_price"] <= price <= self.params["max_price"]):
            return None

        # ── Gap check (today open vs yesterday close) ─────────────
        gap_pct = (latest.open - prev.close) / prev.close * 100 if prev.close > 0 else 0
        if gap_pct < self.params["gap_min_pct"]:
            # No gap yet — may be intraday; check price vs yesterday close
            intraday_move = (price - prev.close) / prev.close * 100
            if intraday_move < self.params["gap_min_pct"]:
                return None

        # ── RVOL ─────────────────────────────────────────────────
        if latest.rvol < self.params["rvol_min"]:
            return None

        # ── ATR-based stops ───────────────────────────────────────
        atr  = latest.atr if latest.atr > 0 else price * 0.025
        # Stop = 15-minute low (approximate as open - 1×ATR)
        stop = latest.low - atr * 0.2   # Tight: just below session low
        stop = max(stop, price - atr * self.params["atr_stop"])
        tp   = price + (price - stop) * self.params["rr_target"]

        # ── Confidence from catalyst type ─────────────────────────
        confidence  = 0.70
        catalyst    = ctx.get("catalyst_type", {}).get(symbol, "")
        for key, boost in self.CATALYST_BOOST.items():
            if key in catalyst.lower():
                confidence = min(0.95, confidence + boost)
                break

        # ── Biotech position halving ──────────────────────────────
        is_biotech   = ctx.get("sector", {}).get(symbol, "") in ("biotech", "pharma", "healthcare")
        size_modifier= 0.5 if (is_biotech and self.params["biotech_halve"]) else 1.0

        sig = Signal(
            symbol      = symbol,
            side        = Side.BUY,
            strategy_id = "news_breakout",
            product     = ProductType.STOCK,
            confidence  = confidence,
            entry_price = price,
            stop_loss   = round(stop, 2),
            take_profit = round(tp,   2),
            news_score  = news_score,
            rvol        = latest.rvol,
            atr         = atr,
            shadow      = getattr(self, "_shadow", False),
            meta        = {
                "gap_pct":      round(gap_pct, 2),
                "catalyst":     catalyst,
                "size_modifier":size_modifier,
            }
        )

        logger.info(
            f"[STK-02] {symbol} BUY @{price:.2f} gap={gap_pct:.1f}% "
            f"news={news_score:.1f} catalyst={catalyst} conf={confidence:.2f}"
        )
        return sig
