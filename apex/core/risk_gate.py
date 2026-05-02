"""
QuantForce Apex — Risk Gate v3.0 (Layer 0, DO NOT MODIFY)
$1,200 cash account aware. Inline synchronous execution only.
"""
import logging
from datetime import datetime, timezone
import pytz

from core.interfaces import Signal, Order, RiskResult, AccountState, ProductType

logger = logging.getLogger("RiskGate")

# ── Hard-coded safety constants (change via risk_config.yaml only) ─
DAILY_LOSS_LIMIT_PCT  = 0.02    # 2% daily loss circuit breaker
MAX_SINGLE_PCT        = 0.10    # Max 10% of account per position
MAX_TOTAL_EXPOSURE_PCT= 0.90    # Never exceed 90% total exposure
COOLDOWN_MINUTES      = 60      # Same symbol cooldown
MARKET_OPEN_BUFFER    = 5       # Minutes after open before entries (09:35)
MARKET_CLOSE_BUFFER   = 15      # Minutes before close to stop entries (15:45)
MIN_RR_RATIO          = 1.5     # Minimum risk/reward
MIN_ACCOUNT_BALANCE   = 100.0   # Hard floor: if equity < $100, halt all trading

ET = pytz.timezone("America/New_York")


class RiskGate:
    """
    Synchronous inline risk gate. Call check_signal() before every order.
    Never async, never microservice.
    """

    def __init__(self, cfg: dict = None):
        cfg = cfg or {}
        self.daily_loss_limit = cfg.get("daily_loss", DAILY_LOSS_LIMIT_PCT)
        self.max_single_pct   = cfg.get("max_single_pct", MAX_SINGLE_PCT)
        self.min_rr           = cfg.get("min_rr", MIN_RR_RATIO)
        self._signal_timestamps: dict = {}   # symbol -> last signal ts

    # ── Main entry point ──────────────────────────────────────────

    def check_signal(self, signal: Signal, account: AccountState) -> RiskResult:
        """Run all checks in order. First failure rejects."""

        checks = [
            self._check_account_floor,
            self._check_daily_loss,
            self._check_market_hours,
            self._check_cooldown,
            self._check_position_size,
            self._check_total_exposure,
            self._check_risk_reward,
            self._check_product_specific,
        ]

        for check in checks:
            result = check(signal, account)
            if not result.approved:
                logger.warning(f"[REJECT] {signal.symbol} {signal.strategy_id}: {result.reason}")
                return result

        # Calculate approved position size
        qty = self._calc_position_size(signal, account)
        logger.info(f"[APPROVE] {signal.symbol} {signal.strategy_id} qty={qty:.2f}")
        self._signal_timestamps[signal.symbol] = datetime.utcnow().timestamp()
        return RiskResult.ok(qty=qty)

    # ── Individual checks ─────────────────────────────────────────

    def _check_account_floor(self, signal, account) -> RiskResult:
        if account.total_balance < MIN_ACCOUNT_BALANCE:
            return RiskResult.reject(f"Account below floor ${MIN_ACCOUNT_BALANCE}: ${account.total_balance:.2f}")
        return RiskResult.ok()

    def _check_daily_loss(self, signal, account) -> RiskResult:
        if account.daily_loss_pct <= -self.daily_loss_limit:
            return RiskResult.reject(
                f"Daily loss limit hit: {account.daily_loss_pct*100:.1f}% (limit {self.daily_loss_limit*100:.0f}%)"
            )
        return RiskResult.ok()

    def _check_market_hours(self, signal, account) -> RiskResult:
        # Skip time check for crypto (24/7) and forex (weekday 24h)
        if signal.product in (ProductType.CRYPTO, ProductType.FOREX):
            return RiskResult.ok()

        now_et = datetime.now(ET)
        # Weekends: no equities/futures trading
        if now_et.weekday() >= 5:
            return RiskResult.reject("Weekend — markets closed")

        market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
        entry_open   = now_et.replace(hour=9,  minute=30+MARKET_OPEN_BUFFER,  second=0, microsecond=0)
        entry_close  = now_et.replace(hour=15, minute=60-MARKET_CLOSE_BUFFER, second=0, microsecond=0)

        if now_et < entry_open:
            return RiskResult.reject(f"Too early: wait until 09:{30+MARKET_OPEN_BUFFER:02d} ET (T-014)")
        if now_et > entry_close:
            return RiskResult.reject(f"Too late: no new entries after {entry_close.strftime('%H:%M')} ET")
        return RiskResult.ok()

    def _check_cooldown(self, signal, account) -> RiskResult:
        last_ts = self._signal_timestamps.get(signal.symbol)
        if last_ts is None:
            return RiskResult.ok()
        elapsed = (datetime.utcnow().timestamp() - last_ts) / 60
        if elapsed < COOLDOWN_MINUTES:
            return RiskResult.reject(
                f"Cooldown: {signal.symbol} last signal {elapsed:.0f}min ago (need {COOLDOWN_MINUTES}min)"
            )
        return RiskResult.ok()

    def _check_position_size(self, signal, account) -> RiskResult:
        if signal.entry_price <= 0:
            return RiskResult.reject("entry_price not set")
        max_notional = account.total_balance * self.max_single_pct
        if signal.size > 0 and signal.size > max_notional:
            return RiskResult.reject(
                f"Position size ${signal.size:.0f} exceeds max ${max_notional:.0f} (10% of account)"
            )
        return RiskResult.ok()

    def _check_total_exposure(self, signal, account) -> RiskResult:
        used = sum(account.open_positions.values())
        max_exposure = account.total_balance * MAX_TOTAL_EXPOSURE_PCT
        new_size = signal.size if signal.size > 0 else account.total_balance * 0.10
        if used + new_size > max_exposure:
            return RiskResult.reject(
                f"Total exposure ${used+new_size:.0f} would exceed ${max_exposure:.0f} (90% limit)"
            )
        return RiskResult.ok()

    def _check_risk_reward(self, signal, account) -> RiskResult:
        if signal.stop_loss <= 0 or signal.take_profit <= 0:
            return RiskResult.ok()   # Skip if not set (strategy may use ATR-based exits)
        rr = signal.risk_reward
        if rr < self.min_rr:
            return RiskResult.reject(f"R/R {rr:.2f} below minimum {self.min_rr}")
        return RiskResult.ok()

    def _check_product_specific(self, signal, account) -> RiskResult:
        """Product-specific hard rules."""
        if signal.product == ProductType.FUTURES:
            # Futures disabled until account >= $3,000
            if account.total_balance < 3000:
                if not signal.shadow:
                    return RiskResult.reject(
                        f"Futures require $3,000 account (current ${account.total_balance:.0f}). Set shadow=True to paper trade."
                    )
        if signal.product == ProductType.OPTION:
            if account.total_balance < 2000:
                if not signal.shadow:
                    return RiskResult.reject(
                        f"Options require $2,000 account (current ${account.total_balance:.0f}). Set shadow=True."
                    )
        if signal.product == ProductType.CRYPTO:
            # Crypto max $200 notional per trade
            if signal.size > 200:
                return RiskResult.reject(f"Crypto max $200/trade (requested ${signal.size:.0f})")
        return RiskResult.ok()

    # ── Position sizing ───────────────────────────────────────────

    def _calc_position_size(self, signal: Signal, account: AccountState) -> float:
        """
        ATR-based position sizing.
        Risk per trade = 2.5% of account ($30 at $1,200).
        qty = risk_amount / stop_distance
        """
        risk_amount   = account.total_balance * 0.025
        stop_distance = signal.stop_distance

        if stop_distance <= 0 or signal.entry_price <= 0:
            # Fallback: 5% of available cash
            fallback_notional = min(account.available_cash * 0.05, account.total_balance * 0.05)
            return max(1.0, fallback_notional / signal.entry_price) if signal.entry_price > 0 else 1.0

        import math
        qty = math.floor(risk_amount / stop_distance)

        # Hard cap: notional must not exceed 70% of available cash
        max_notional = account.available_cash * 0.70
        max_qty_by_cash = math.floor(max_notional / signal.entry_price)
        qty = min(qty, max_qty_by_cash)

        # Hard cap: 10% of total account
        max_qty_by_account = math.floor(account.total_balance * self.max_single_pct / signal.entry_price)
        qty = min(qty, max_qty_by_account)

        return max(1.0, float(qty))
