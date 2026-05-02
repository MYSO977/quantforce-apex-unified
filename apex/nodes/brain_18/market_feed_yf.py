"""
QuantForce Apex — Market Feed (yfinance version)
yfinance 1min bars -> PG market_events
Runs on .18 (has internet access)
"""
import os, time, logging
import yfinance as yf
import sys
sys.path.insert(0, os.path.expanduser("~/quantforce-apex"))
from core.db import db_cursor

logging.basicConfig(level=logging.INFO, force=True,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("MarketFeedYF")

RVOL_MIN  = 1.5
POLL_SEC  = 60

def load_symbols():
    try:
        with db_cursor(commit=False) as cur:
            cur.execute("""
                SELECT symbol FROM universe_whitelist
                WHERE active=true AND avg_volume_30d >= 500000
                ORDER BY avg_volume_30d DESC LIMIT 150
            """)
            return [r["symbol"] for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"DB load error: {e}")
        return ["SPY","QQQ","IWM","XLK","XLV","XLF","XLE","XLI","XLY","XLP"]

def write_bar(symbol, row, rvol):
    try:
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO market_events
                    (symbol, ts, open, high, low, close, volume, rvol, vwap, atr, atr_move)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                symbol,
                row.name.to_pydatetime(),
                float(row["Open"]), float(row["High"]),
                float(row["Low"]),  float(row["Close"]),
                float(row["Volume"]),
                round(rvol, 3),
                float(row["Close"]),
                float(row["High"] - row["Low"]),
                0.0
            ))
        logger.debug(f"{symbol} written c={row['Close']:.2f} rvol={rvol:.2f}")
    except Exception as e:
        logger.error(f"write_bar {symbol}: {e}")

def run():
    syms = load_symbols()
    logger.info(f"Market feed YF started: {len(syms)} symbols")
    vol_history = {}

    while True:
        batch = [syms[i:i+50] for i in range(0, len(syms), 50)]
        for group in batch:
            try:
                tickers = " ".join(group)
                df = yf.download(tickers, period="2d", interval="1m",
                                 group_by="ticker", progress=False, auto_adjust=True)
                for sym in group:
                    try:
                        if len(group) == 1:
                            data = df
                        else:
                            if sym not in df.columns.get_level_values(0):
                                continue
                            data = df[sym]
                        data = data.dropna()
                        if data.empty:
                            continue
                        # compute RVOL
                        vols = data["Volume"].tolist()
                        if sym not in vol_history:
                            vol_history[sym] = vols
                        else:
                            vol_history[sym] = (vol_history[sym] + vols)[-60:]
                        avg_vol = sum(vol_history[sym][:-1]) / max(len(vol_history[sym])-1, 1)
                        last = data.iloc[-2]  # last complete bar
                        rvol = float(last["Volume"]) / avg_vol if avg_vol > 0 else 1.0
                        write_bar(sym, last, rvol)
                    except Exception as e:
                        logger.debug(f"{sym} error: {e}")
            except Exception as e:
                logger.error(f"batch download error: {e}")
        logger.info(f"Poll complete: {len(syms)} symbols")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    run()
