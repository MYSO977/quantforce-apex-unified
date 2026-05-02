"""
QuantForce Apex — Signal Fusion (Notify + Auto-Execute Mode)
Generates signals, sends Telegram alerts AND pushes APPROVED signals
via ZMQ to ib_executor on .11 (port 5558).
shadow=True strategies: Telegram only. shadow=False: Telegram + ZMQ → IB.
"""
import os, sys, time, logging, yaml, json
sys.path.insert(0, os.path.expanduser("~/quantforce-apex"))
import zmq

from core.interfaces import Signal, SignalStatus
from core.risk_gate  import RiskGate
from core.account    import AccountManager
from core.db         import db_cursor, write_signal, get_account_state
from core.notifier   import _send
from universe.universe_manager import universe
from strategies.registry import strategy_registry

import strategies.stock.momentum_swing
import strategies.stock.news_breakout
import strategies.stock.opening_range
import strategies.etf.sector_rotation

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S")
logger = logging.getLogger("SignalFusion")

CFG_PATH    = os.path.expanduser("~/quantforce-apex/config")

# ── ZMQ push to ib_executor on .11 ───────────────────────────────
EXECUTOR_HOST = os.getenv("QF_EXECUTOR_HOST", "192.168.0.11")
EXECUTOR_PORT = int(os.getenv("QF_ZMQ_PORT",  "5558"))

_zmq_ctx    = None
_zmq_socket = None

def _get_zmq_socket():
    global _zmq_ctx, _zmq_socket
    if _zmq_socket is None:
        _zmq_ctx    = zmq.Context()
        _zmq_socket = _zmq_ctx.socket(zmq.PUSH)
        _zmq_socket.setsockopt(zmq.LINGER,    0)
        _zmq_socket.setsockopt(zmq.SNDHWM,    10)
        _zmq_socket.connect(f"tcp://{EXECUTOR_HOST}:{EXECUTOR_PORT}")
        logger.info(f"ZMQ PUSH connected → {EXECUTOR_HOST}:{EXECUTOR_PORT}")
    return _zmq_socket


def push_to_executor(sig) -> bool:
    """Send APPROVED, non-shadow signal to ib_executor via ZMQ."""
    payload = {
        "signal_id":   sig.signal_id,
        "symbol":      sig.symbol,
        "side":        sig.side.value,          # "BUY" / "SELL"
        "product":     sig.product.value,        # "stock" / "etf" / …
        "entry_price": sig.entry_price,
        "stop_loss":   sig.stop_loss,
        "take_profit": sig.take_profit,
        "size":        sig.size,                 # notional $
        "confidence":  sig.confidence,
        "strategy_id": sig.strategy_id,
        "shadow":      False,
    }
    try:
        sock = _get_zmq_socket()
        sock.send_json(payload, zmq.NOBLOCK)
        logger.info(f"[ZMQ→.11] {sig.symbol} {sig.side.value} "
                    f"size=${sig.size:.0f} conf={sig.confidence:.0%}")
        return True
    except zmq.Again:
        logger.warning(f"[ZMQ] Send buffer full — signal dropped: {sig.symbol}")
        return False
    except Exception as e:
        logger.error(f"[ZMQ] Push failed: {e}")
        return False


def notify_manual_order(sig: Signal):
    """Send Telegram alert with manual order instructions."""
    rr   = f"{sig.risk_reward:.1f}" if sig.risk_reward > 0 else "—"
    mode = "🔵 SHADOW" if sig.shadow else "📡 **ACTION REQUIRED**"
    text = (
        f"{mode}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 <b>{sig.symbol}</b>  {sig.side.value}\n"
        f"策略: {sig.strategy_id}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 入场: <b>${sig.entry_price:.2f}</b>\n"
        f"🛑 止损: ${sig.stop_loss:.2f}\n"
        f"🎯 止盈: ${sig.take_profit:.2f}\n"
        f"📊 R/R: {rr}  |  置信: {sig.confidence:.0%}\n"
        f"📰 新闻: {sig.news_score:.1f}/10\n"
        f"📦 RVOL: {sig.rvol:.1f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 建议仓位: ${sig.size:.0f}\n"
        f"🔑 ID: {sig.signal_id}"
    )
    _send(text)


def load_recent_bars(symbols):
    bars_by_symbol = {}
    if not symbols:
        return bars_by_symbol
    try:
        with db_cursor(commit=False) as cur:
            cur.execute("""
                SELECT symbol,
                       extract(epoch from ts) as ts,
                       COALESCE(open,  price, 0) as open,
                       COALESCE(high,  price, 0) as high,
                       COALESCE(low,   price, 0) as low,
                       COALESCE(close, price, 0) as close,
                       COALESCE(volume, 0)        as volume,
                       COALESCE(rvol, 1.0)        as rvol,
                       COALESCE(vwap, price, 0)   as vwap,
                       COALESCE(atr,  0)          as atr
                FROM market_events
                WHERE symbol = ANY(%s)
                  AND ts >= NOW() - INTERVAL '5 days'
                ORDER BY symbol, ts ASC
            """, (symbols,))
            from core.interfaces import Bar
            for row in cur.fetchall():
                sym = row["symbol"]
                b = Bar(
                    symbol=sym, ts=float(row["ts"]),
                    open=float(row["open"]),   high=float(row["high"]),
                    low=float(row["low"]),     close=float(row["close"]),
                    volume=float(row["volume"]),
                    rvol=float(row["rvol"]),
                    vwap=float(row["vwap"]),
                    atr=float(row["atr"]),
                )
                bars_by_symbol.setdefault(sym, []).append(b)
    except Exception as e:
        logger.error(f"load_bars error: {e}")
    return bars_by_symbol


def load_news_scores():
    scores = {}
    try:
        with db_cursor(commit=False) as cur:
            cur.execute("""
                SELECT symbol,
                       MAX(COALESCE(news_score, score, confidence*10)) as score
                FROM signals_raw
                WHERE created_at >= NOW() - INTERVAL '2 hours'
                GROUP BY symbol
            """)
            for row in cur.fetchall():
                scores[row["symbol"]] = float(row["score"] or 0)
    except Exception as e:
        logger.error(f"load_news_scores error: {e}")
    return scores


def get_account():
    """Get account state from PG, return AccountState object."""
    from core.interfaces import AccountState
    row = get_account_state()
    if row:
        return AccountState(
            total_balance     = float(row["total_balance"]      or 1200),
            settled_balance   = float(row["settled_balance"]    or 1200),
            pending_settlement= float(row["pending_settlement"] or 0),
            unrealized_pnl    = float(row["unrealized_pnl"]     or 0),
            realized_pnl_today= float(row["realized_pnl_today"] or 0),
        )
    return AccountState(total_balance=1200, settled_balance=1200)


def run():
    risk_cfg  = yaml.safe_load(open(f"{CFG_PATH}/risk_config.yaml"))  or {}
    strat_cfg = yaml.safe_load(open(f"{CFG_PATH}/strategies.yaml"))   or {}

    risk_gate  = RiskGate(risk_cfg.get("risk", {}))
    strategies = strategy_registry.load(strat_cfg.get("strategies", []))

    universe.refresh_daily()
    logger.info(f"Universe: {len(universe.get_candidates())} candidates")
    logger.info(f"Strategies: {[s.__class__.__name__ for s in strategies]}")
    logger.info("Signal fusion (notify-only mode) started")

    _send("🚀 <b>QuantForce Apex 启动</b>\n信号生成模式 — Telegram推送手动执行")

    SCAN_INTERVAL = 60

    while True:
        try:
            loop_start   = time.time()
            candidates   = universe.get_candidates()
            news_scores  = load_news_scores()
            bars_map     = load_recent_bars(candidates)
            account      = get_account()

            ctx = {
                "bars":        bars_map,
                "news_scores": news_scores,
                "positions":   account.open_positions,
                "account":     account,
            }

            fired = 0
            for strategy in strategies:
                from core.interfaces import ProductType
                if strategy.PRODUCT == ProductType.ETF:
                    sector_syms = ["XLK","XLV","XLF","XLE","XLI",
                                   "XLY","XLP","XLB","XLU","XLRE","XLC"]
                    ctx["sector_bars"] = {s: bars_map.get(s,[]) for s in sector_syms}
                    sig = strategy.on_bar("PORTFOLIO", [], ctx)
                    if sig:
                        _process(sig, risk_gate, account)
                        fired += 1
                    continue

                for symbol in candidates:
                    bars = bars_map.get(symbol, [])
                    if len(bars) < 5:
                        continue
                    sig = strategy.on_bar(symbol, bars, ctx)
                    if sig:
                        _process(sig, risk_gate, account)
                        fired += 1
                        continue
                    score = news_scores.get(symbol, 0)
                    if score >= 6.0:
                        sig = strategy.on_news(symbol, score, ctx)
                        if sig:
                            _process(sig, risk_gate, account)
                            fired += 1

            elapsed = time.time() - loop_start
            logger.info(f"Scan: {len(candidates)} candidates | "
                        f"{fired} signals | {elapsed:.1f}s")

            if int(time.time()) % 1800 < SCAN_INTERVAL:
                universe.refresh_daily()

            time.sleep(max(0, SCAN_INTERVAL - elapsed))

        except KeyboardInterrupt:
            logger.info("Stopped")
            break
        except Exception as e:
            logger.error(f"Loop error: {e}", exc_info=True)
            time.sleep(10)


def _process(sig, risk_gate, account):
    from core.interfaces import SignalStatus
    result = risk_gate.check_signal(sig, account)
    if not result.approved:
        sig.status = SignalStatus.REJECTED
        write_signal(sig)
        logger.info(f"[REJECT] {sig.symbol}: {result.reason}")
        return
    sig.size   = result.adjusted_qty * sig.entry_price
    sig.status = SignalStatus.APPROVED
    write_signal(sig)
    notify_manual_order(sig)
    if sig.shadow:
        logger.info(f"[SHADOW] {sig.symbol} {sig.side.value} "
                    f"@{sig.entry_price:.2f} → Telegram only")
    else:
        pushed = push_to_executor(sig)
        logger.info(f"[LIVE] {sig.symbol} {sig.side.value} "
                    f"@{sig.entry_price:.2f} → Telegram + ZMQ {'✅' if pushed else '❌'}")


if __name__ == "__main__":
    run()
