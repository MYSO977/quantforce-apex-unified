"""
QuantForce Apex — Account Manager (Layer 0)
$1,200 cash account with T+1 settlement awareness.
"""
import logging
from datetime import datetime, timedelta
from core.interfaces import AccountState, Fill, Side

logger = logging.getLogger("Account")

INITIAL_BALANCE = 1200.0


class AccountManager:
    """
    Tracks settled vs unsettled cash.
    Cash account: sold proceeds are NOT available until T+1.
    """

    def __init__(self, initial_balance: float = INITIAL_BALANCE):
        self._state = AccountState(
            total_balance=initial_balance,
            settled_balance=initial_balance,
        )
        self._pending: list = []   # [{amount, settle_date}]

    def get_state(self) -> AccountState:
        self._process_settlements()
        return self._state

    def on_fill(self, fill: Fill):
        """Update account on order fill."""
        notional = fill.qty * fill.fill_price
        net      = notional - fill.commission

        if fill.side == Side.BUY:
            # Deduct from settled cash immediately
            self._state.settled_balance -= (notional + fill.commission)
            self._state.open_positions[fill.symbol] = \
                self._state.open_positions.get(fill.symbol, 0) + notional
            logger.info(f"BUY {fill.symbol}: -${notional:.2f} settled cash")

        elif fill.side == Side.SELL:
            # Remove from positions, add to pending settlement (T+1)
            self._state.open_positions.pop(fill.symbol, None)
            settle_date = (datetime.utcnow() + timedelta(days=1)).date()
            self._pending.append({"amount": net, "settle_date": settle_date})
            self._state.pending_settlement += net
            # Update realized PnL (simplified)
            self._state.realized_pnl_today += (net - notional * 0.5)
            logger.info(f"SELL {fill.symbol}: +${net:.2f} pending T+1 settlement on {settle_date}")

    def _process_settlements(self):
        """Move settled amounts from pending to settled_balance."""
        today = datetime.utcnow().date()
        still_pending = []
        for item in self._pending:
            if item["settle_date"] <= today:
                self._state.settled_balance += item["amount"]
                self._state.pending_settlement -= item["amount"]
                logger.info(f"T+1 settled: +${item['amount']:.2f}")
            else:
                still_pending.append(item)
        self._pending = still_pending

    def reset_daily_pnl(self):
        """Call at start of each trading day."""
        self._state.realized_pnl_today = 0.0
