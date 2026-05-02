"""
[STK-03] OpeningRangeBreakout — 09:30-09:45 range breakout
Pure price/volume — no news required
Hold: same day to 2 days | Works best on high-RVOL days
"""
from typing import Optional, List
import logging
from datetime import datetime, timezone
import pytz

from core.interfaces import Signal, Bar, Side, ProductType
from strategies.base import BaseStrategy
from strategies.registry import strategy_registry

logger = logging.getLogger("STK-03")
ET = pytz.timezone("America/New_York")


@strategy_registry.register("opening_range_breakout")
class OpeningRangeBreakoutStrategy(BaseStrategy):
    PRODUCT = ProductType.STOCK
    PARAMS  = {
        "orb_minutes":      15,    # Opening range window (minutes)
        "rvol_min":         1.5,   # RVOL at breakout
        "volume_confirm":   1.5,   # Breakout bar volume vs ORB avg
        "max_range_pct":    4.0,   # ORB width / price <= 4% (avoid chaotic opens)
        "atr_stop":         1.0,   # Stop = ORB midpoint or 1×ATR below
        "rr_target":        2.0,
        "min_price":        5.0,
        "max_price":        60.0,
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._orb: dict = {}   # symbol -> {high, low, avg_vol, set_at}

    def on_bar(self, symbol: str, bars: List[Bar], ctx: dict) -> Optional[Signal]:
        if len(bars) < 4:
            return None

        latest = bars[-1]
        price  = latest.close

        if not (self.params["min_price"] <= price <= self.params["max_price"]):
            return None

        now_et = datetime.now(ET)
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        orb_end     = now_et.replace(hour=9, minute=30 + self.params["orb_minutes"], second=0, microsecond=0)

        # ── Build/update ORB during opening window ────────────────
        if now_et <= orb_end:
            orb_bars = [b for b in bars if b.ts >= market_open.timestamp()]
            if orb_bars:
                self._orb[symbol] = {
                    "high":    max(b.high for b in orb_bars),
                    "low":     min(b.low  for b in orb_bars),
                    "avg_vol": sum(b.volume for b in orb_bars) / len(orb_bars),
                }
            return None   # Don't trade during ORB formation

        orb = self._orb.get(symbol)
        if not orb:
            return None

        # ── ORB range sanity check ────────────────────────────────
        orb_range_pct = (orb["high"] - orb["low"]) / orb["low"] * 100
        if orb_range_pct > self.params["max_range_pct"]:
            return None   # Too wide/chaotic

        # ── Breakout condition ────────────────────────────────────
        if latest.close <= orb["high"]:
            return None   # No breakout yet

        # ── Volume confirmation ───────────────────────────────────
        if orb["avg_vol"] > 0 and latest.volume < orb["avg_vol"] * self.params["volume_confirm"]:
            return None

        # ── RVOL ─────────────────────────────────────────────────
        if latest.rvol < self.params["rvol_min"]:
            return None

        # ── Stops: below ORB midpoint or ATR ──────────────────────
        atr     = latest.atr if latest.atr > 0 else price * 0.02
        orb_mid = (orb["high"] + orb["low"]) / 2
        stop    = max(orb_mid, price - atr * self.params["atr_stop"])
        tp      = price + (price - stop) * self.params["rr_target"]

        sig = Signal(
            symbol      = symbol,
            side        = Side.BUY,
            strategy_id = "opening_range_breakout",
            product     = ProductType.STOCK,
            confidence  = 0.68,
            entry_price = price,
            stop_loss   = round(stop, 2),
            take_profit = round(tp,   2),
            rvol        = latest.rvol,
            atr         = atr,
            shadow      = getattr(self, "_shadow", False),
            meta        = {
                "orb_high":      round(orb["high"], 2),
                "orb_low":       round(orb["low"],  2),
                "orb_range_pct": round(orb_range_pct, 2),
            }
        )

        logger.info(
            f"[STK-03] {symbol} ORB breakout @{price:.2f} "
            f"range={orb_range_pct:.1f}% RVOL={latest.rvol:.1f}"
        )
        # Clear ORB after signal to prevent duplicate
        self._orb.pop(symbol, None)
        return sig
