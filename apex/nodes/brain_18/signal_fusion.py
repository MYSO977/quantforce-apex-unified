"""
QuantForce Apex — Signal Fusion (runs on .18 Brain)
Multi-product G1-G4 funnel: Universe -> Strategy -> Cooldown -> RiskGate -> ZMQ
"""
import os, sys, time, logging, json, yaml
import zmq

sys.path.insert(0, os.path.expanduser("~/quantforce-apex"))

from core.interfaces import Signal, Bar, AccountState, SignalStatus, ProductType
from core.risk_gate   import RiskGate
from core.account     import AccountManager
from core.db          import db_cursor, write_signal
from core.notifier    import notify_signal
from universe.universe_manager import universe
from strategies.registry import strategy_registry

# Auto-import all strategy modules to trigger @register decorators
import strategies.stock.momentum_swing
import strategies.stock.news_breakout
import strategies.stock.opening_range
import strategies.etf.sector_rotation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("SignalFusion")

# ── Config ────────────────────────────────────────────────────────
CFG_PATH  = os.path.expanduser("~/quantforce-apex/config")
ZMQ_PORT  = int(os.getenv("QF_ZMQ_PORT", "5558"))
ZMQ_HOST  = os.getenv("QF_EXEC_HOST", "192.168.0.11")


def load_config():
    risk_cfg  = yaml.safe_load(open(f"{CFG_PATH}/risk_config.yaml"))   or {}
    strat_cfg = yaml.safe_load(open(f"{CFG_PATH}/strategies.yaml"))    or {}
    return risk_cfg, strat_cfg


def load_recent_bars(symbols: list, limit: int = 30) -> dict:
    """Load recent 5-min bars from market_events PG table."""
    bars_by_symbol = {}
    if not symbols:
        return bars_by_symbol
    try:
        with db_cursor(commit=False) as cur:
            cur.execute("""
                SELECT symbol, extract(epoch from ts) as ts,
                       COALESCE(open,price) as open, COALESCE(high,price) as high, COALESCE(low,price) as low, COALESCE(close,price) as close, volume, rvol, COALESCE(vwap,price) as vwap, atr
                FROM market_events
                WHERE symbol = ANY(%s)
                  AND ts >= NOW() - INTERVAL '4 hours'
                ORDER BY symbol, ts ASC
            """, (symbols,))
            for row in cur.fetchall():
                sym = row["symbol"]
                b   = Bar(
                    symbol=sym, ts=float(row["ts"]),
                    open=float(row["open"]),   high=float(row["high"]),
                    low=float(row["low"]),     close=float(row["close"]),
                    volume=float(row["volume"]),
                    rvol=float(row["rvol"] or 1.0),
                    vwap=float(row["vwap"] or 0.0),
                    atr=float(row["atr"]  or 0.0),
                )
                bars_by_symbol.setdefault(sym, []).append(b)
    except Exception as e:
        logger.error(f"load_recent_bars error: {e}")
    return bars_by_symbol


def load_news_scores() -> dict:
    """Load latest Groq scores from signals_raw (news signals, last 2h)."""
    scores = {}
    try:
        with db_cursor(commit=False) as cur:
            cur.execute("""
                SELECT symbol, MAX(confidence * 10) as score
                FROM signals_raw
                WHERE created_at >= NOW() - INTERVAL '2 hours'
                  AND strategy_id LIKE '%news%'
                GROUP BY symbol
            """)
            for row in cur.fetchall():
                scores[row["symbol"]] = float(row["score"] or 0.0)
    except Exception as e:
        logger.error(f"load_news_scores error: {e}")
    return scores


def signal_to_dict(sig: Signal) -> dict:
    return {
        "signal_id":   sig.signal_id,
        "symbol":      sig.symbol,
        "side":        sig.side.value,
        "strategy_id": sig.strategy_id,
        "product":     sig.product.value,
        "confidence":  sig.confidence,
        "entry_price": sig.entry_price,
        "stop_loss":   sig.stop_loss,
        "take_profit": sig.take_profit,
        "size":        sig.size,
        "news_score":  sig.news_score,
        "rvol":        sig.rvol,
        "atr":         sig.atr,
        "shadow":      sig.shadow,
        "meta":        sig.meta,
    }


def run():
    risk_cfg, strat_cfg = load_config()
    risk_gate   = RiskGate(risk_cfg.get("risk", {}))
    acct_mgr    = AccountManager(risk_cfg.get("account", {}).get("initial_balance", 1200.0))
    strategies  = strategy_registry.load(strat_cfg.get("strategies", []))

    # ── ZMQ PUSH socket ───────────────────────────────────────────
    ctx_zmq  = zmq.Context()
    socket   = ctx_zmq.socket(zmq.PUSH)
    # Clear any lingering connections first
    socket.setsockopt(zmq.LINGER, 0)
    socket.connect(f"tcp://{ZMQ_HOST}:{ZMQ_PORT}")
    logger.info(f"ZMQ PUSH connected to {ZMQ_HOST}:{ZMQ_PORT}")

    # ── Universe warm-up ──────────────────────────────────────────
    universe.refresh_daily()
    logger.info(f"Universe ready: {len(universe.get_candidates())} candidates")

    logger.info(f"Strategies loaded: {[s.__class__.__name__ for s in strategies]}")
    logger.info("Signal fusion loop starting...")

    SCAN_INTERVAL = 60   # seconds between full scans

    while True:
        try:
            loop_start = time.time()

            # Refresh universe candidates
            candidates  = universe.get_candidates()
            news_scores = load_news_scores()
            bars_map    = load_recent_bars(candidates)
            account     = acct_mgr.get_state()

            ctx = {
                "bars":        bars_map,
                "news_scores": news_scores,
                "positions":   account.open_positions,
                "account":     account,
            }

            signals_fired = 0

            for strategy in strategies:
                product = strategy.PRODUCT

                # ETF portfolio strategy — special handling
                if product == ProductType.ETF:
                    sector_bars = {sym: bars_map.get(sym, [])
                                   for sym in ["XLK","XLV","XLF","XLE","XLI",
                                               "XLY","XLP","XLB","XLU","XLRE","XLC"]}
                    ctx["sector_bars"] = sector_bars
                    sig = strategy.on_bar("PORTFOLIO", [], ctx)
                    if sig:
                        _process_signal(sig, risk_gate, account, socket)
                        signals_fired += 1
                    continue

                # Stock strategies — scan each candidate
                for symbol in candidates:
                    bars = bars_map.get(symbol, [])
                    if len(bars) < 5:
                        continue
                    # BB Squeeze filter
                    try:
                        import yfinance as yf, numpy as np
                        import yaml as _yaml
                        _cfg = _yaml.safe_load(open("/home/heng/quantforce-apex/config/params/params_latest.yaml"))
                        _cur = bars[-1].close if bars else 0
                        _currency = "CAD" if symbol.endswith(".TO") else "USD"
                        _strat_key = "strategy_B" if _currency == "CAD" else "strategy_A"
                        _strat = _cfg.get(_strat_key, {})
                        _price_min = _strat.get("price_min", 3.0)
                        _price_max = _strat.get("price_max", 20.0)
                        # Price range filter (USD only)
                        if _currency == "USD" and not (_price_min <= _cur <= _price_max):
                            continue
                        # BB Squeeze check
                        _tk = yf.Ticker(symbol)
                        _df = _tk.history(period="30d", interval="1d")
                        if len(_df) >= 20:
                            _df["ma20"] = _df["Close"].rolling(20).mean()
                            _df["std20"] = _df["Close"].rolling(20).std()
                            _df["bbw"] = (_df["ma20"] + 2*_df["std20"] - (_df["ma20"] - 2*_df["std20"])) / _df["ma20"]
                            _df = _df.dropna()
                            if len(_df) >= 5:
                                _avg_bbw = _df["bbw"].mean()
                                _cur_bbw = _df["bbw"].iloc[-1]
                                _sq_ratio = _strat.get("bb_squeeze_ratio", 0.80)
                                _rng_pct  = _strat.get("price_range_pct", 0.08)
                                _sq_days  = _strat.get("bb_squeeze_days", 5)
                                _last10   = _df.tail(10)
                                _rng      = (_last10["High"].max() - _last10["Low"].min()) / _last10["Close"].mean()
                                _days_sq  = sum(1 for w in reversed(_df["bbw"].values) if w < _avg_bbw * _sq_ratio)
                                _is_sq    = (_cur_bbw < _avg_bbw * _sq_ratio) and (_rng < _rng_pct) and (_days_sq >= _sq_days)
                                if not _is_sq:
                                    continue
                                logger.debug(f"[BB SQUEEZE] {symbol} passed BBW:{_cur_bbw*100:.1f}% Range:{_rng*100:.1f}% Days:{_days_sq}")
                    except Exception as _e:
                        logger.debug(f"[BB SQUEEZE] {symbol} check failed: {_e}")

                    # on_bar
                    sig = strategy.on_bar(symbol, bars, ctx)
                    if sig:
                        _process_signal(sig, risk_gate, account, socket)
                        signals_fired += 1
                        continue

                    # on_news (if news score available)
                    score = news_scores.get(symbol, 0.0)
                    if score >= 7.5:
                        sig = strategy.on_news(symbol, score, ctx)
                        if sig:
                            if score >= 7.5:
                                sig.confidence = min(1.0, sig.confidence * 1.3)
                                logger.info(f"[NEWS BOOST] {symbol} conf x1.3 -> {sig.confidence:.2f}")
                            _process_signal(sig, risk_gate, account, socket)
                            signals_fired += 1

            elapsed = time.time() - loop_start
            logger.info(
                f"Scan complete: {len(candidates)} candidates, "
                f"{signals_fired} signals, {elapsed:.1f}s"
            )

            # Refresh universe every 30 minutes
            if int(time.time()) % 1800 < SCAN_INTERVAL:
                universe.refresh_daily()

            sleep_time = max(0, SCAN_INTERVAL - elapsed)
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info("Signal fusion stopped by user")
            break
        except Exception as e:
            logger.error(f"Fusion loop error: {e}", exc_info=True)
            time.sleep(10)

    socket.close()
    ctx_zmq.term()


def _process_signal(sig: Signal, risk_gate: RiskGate,
                    account: AccountState, socket) -> bool:
    """G3 cooldown + G4 risk gate + write PG + ZMQ push."""

    # G4: Risk gate
    result = risk_gate.check_signal(sig, account)
    if not result.approved:
        sig.status = SignalStatus.REJECTED
        write_signal(sig)
        return False

    sig.size   = result.adjusted_qty * sig.entry_price if sig.entry_price > 0 else 0
    sig.status = SignalStatus.APPROVED

    # Write to PG
    write_signal(sig)

    # Shadow mode: log only, no ZMQ push
    if sig.shadow:
        logger.info(f"[SHADOW] {sig.symbol} {sig.strategy_id} — not pushing to executor")
        return True

    # Push to executor via ZMQ
    try:
        socket.send_json(signal_to_dict(sig), zmq.NOBLOCK)
        logger.info(
            f"[PUSH] {sig.symbol} {sig.side.value} "
            f"@{sig.entry_price:.2f} SL={sig.stop_loss:.2f} "
            f"strategy={sig.strategy_id} conf={sig.confidence:.2f}"
        )
        notify_signal(sig)
        return True
    except Exception as e:
        logger.error(f"ZMQ push failed: {e}")
        return False


if __name__ == "__main__":
    run()
