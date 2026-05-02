"""
QuantForce Apex — Core Interfaces (Layer 0, DO NOT MODIFY)
All products share these base types.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


# ── Enums ─────────────────────────────────────────────────────────

class Side(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"

class ProductType(str, Enum):
    STOCK   = "stock"
    ETF     = "etf"
    OPTION  = "option"
    FOREX   = "forex"
    FUTURES = "futures"
    CRYPTO  = "crypto"

class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT  = "LMT"
    STOP   = "STP"

class SignalStatus(str, Enum):
    RAW      = "raw"
    APPROVED = "approved"
    REJECTED = "rejected"
    FILLED   = "filled"
    EXPIRED  = "expired"

class TimeFrame(str, Enum):
    M1  = "1m"
    M5  = "5m"
    M15 = "15m"
    H1  = "1h"
    D1  = "1d"


# ── Market Data ───────────────────────────────────────────────────

@dataclass
class Bar:
    symbol:    str
    ts:        float          # Unix timestamp
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float
    rvol:      float  = 1.0   # Relative volume vs 20-day avg
    vwap:      float  = 0.0
    atr:       float  = 0.0
    timeframe: str    = "5m"
    product:   str    = ProductType.STOCK

    @property
    def typical_price(self):
        return (self.high + self.low + self.close) / 3


@dataclass
class Quote:
    symbol:   str
    bid:      float
    ask:      float
    last:     float
    volume:   int
    ts:       float = field(default_factory=lambda: datetime.utcnow().timestamp())

    @property
    def spread_pct(self):
        return (self.ask - self.bid) / self.ask * 100 if self.ask > 0 else 999


# ── Signal ────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol:      str
    side:        Side
    strategy_id: str
    product:     ProductType  = ProductType.STOCK
    confidence:  float        = 0.7       # 0.0–1.0
    entry_price: float        = 0.0
    stop_loss:   float        = 0.0
    take_profit: float        = 0.0
    size:        float        = 0.0       # Suggested position size ($)
    signal_id:   str          = ""
    news_score:  float        = 0.0       # Groq score 0–10
    rvol:        float        = 0.0
    atr:         float        = 0.0
    status:      SignalStatus = SignalStatus.RAW
    shadow:      bool         = False     # Shadow mode: log only, no order
    created_at:  float        = field(default_factory=lambda: datetime.utcnow().timestamp())
    updated_at:  float        = field(default_factory=lambda: datetime.utcnow().timestamp())
    meta:        Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.signal_id:
            import uuid
            self.signal_id = str(uuid.uuid4())[:8]

    @property
    def risk_reward(self):
        if self.entry_price <= 0 or self.stop_loss <= 0:
            return 0.0
        risk   = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit - self.entry_price)
        return reward / risk if risk > 0 else 0.0

    @property
    def stop_distance(self):
        return abs(self.entry_price - self.stop_loss)


# ── Order ─────────────────────────────────────────────────────────

@dataclass
class Order:
    symbol:      str
    side:        Side
    qty:         float
    order_type:  OrderType    = OrderType.MARKET
    limit_price: float        = 0.0
    stop_price:  float        = 0.0
    take_profit: float        = 0.0
    stop_loss:   float        = 0.0
    product:     ProductType  = ProductType.STOCK
    signal_id:   str          = ""
    order_id:    str          = ""
    strategy_id: str          = ""
    created_at:  float        = field(default_factory=lambda: datetime.utcnow().timestamp())
    meta:        Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.order_id:
            import uuid
            self.order_id = str(uuid.uuid4())[:8]

    @property
    def notional(self):
        price = self.limit_price if self.limit_price > 0 else self.stop_price
        return self.qty * price


# ── Fill ──────────────────────────────────────────────────────────

@dataclass
class Fill:
    order_id:   str
    symbol:     str
    side:       Side
    qty:        float
    fill_price: float
    commission: float = 0.0
    product:    ProductType = ProductType.STOCK
    signal_id:  str         = ""
    strategy_id:str         = ""
    filled_at:  float       = field(default_factory=lambda: datetime.utcnow().timestamp())
    meta:       Dict[str, Any] = field(default_factory=dict)

    @property
    def gross_value(self):
        return self.qty * self.fill_price

    @property
    def net_value(self):
        return self.gross_value - self.commission


# ── Risk Result ───────────────────────────────────────────────────

@dataclass
class RiskResult:
    approved:   bool
    reason:     str  = ""
    adjusted_qty: float = 0.0   # Risk gate may reduce qty

    @classmethod
    def ok(cls, qty: float = 0.0):
        return cls(approved=True, reason="approved", adjusted_qty=qty)

    @classmethod
    def reject(cls, reason: str):
        return cls(approved=False, reason=reason, adjusted_qty=0.0)


# ── Account State ─────────────────────────────────────────────────

@dataclass
class AccountState:
    total_balance:     float = 1200.0   # Total equity
    settled_balance:   float = 1200.0   # T+1 settled cash (usable)
    pending_settlement:float = 0.0      # Sold but not yet settled
    unrealized_pnl:    float = 0.0
    realized_pnl_today:float = 0.0
    open_positions:    Dict[str, float] = field(default_factory=dict)  # symbol -> notional
    updated_at:        float = field(default_factory=lambda: datetime.utcnow().timestamp())

    @property
    def available_cash(self):
        """Cash available for new trades (uses settled balance)."""
        used = sum(self.open_positions.values())
        return max(0.0, self.settled_balance - used)

    @property
    def daily_loss_pct(self):
        return self.realized_pnl_today / self.total_balance if self.total_balance > 0 else 0.0
