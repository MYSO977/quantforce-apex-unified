#!/usr/bin/env python3
"""BB Squeeze using yfinance daily data"""
import yfinance as yf, yaml, logging, time, psycopg2
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BB] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DB  = "host=192.168.0.11 port=5432 dbname=quantforce user=heng password=newpassword123"
CFG = yaml.safe_load(open("/home/heng/quantforce-apex/config/params/params_latest.yaml"))

def get_conn(): return psycopg2.connect(DB)

def compute_bb_squeeze(symbol, currency="USD"):
    try:
        tk = yf.Ticker(symbol)
        df = tk.history(period="60d", interval="1d")
        if len(df) < 22:
            return None
        df = df.tail(25).copy()
        df["ma20"]     = df["Close"].rolling(20).mean()
        df["std20"]    = df["Close"].rolling(20).std()
        df["bb_upper"] = df["ma20"] + 2 * df["std20"]
        df["bb_lower"] = df["ma20"] - 2 * df["std20"]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["ma20"]
        df = df.dropna()
        if len(df) < 5:
            return None

        strat    = CFG.get("strategy_B" if currency=="CAD" else "strategy_A", {})
        sq_ratio = strat.get("bb_squeeze_ratio", 0.80)
        rng_pct  = strat.get("price_range_pct", 0.08)
        sq_days  = strat.get("bb_squeeze_days", 5)

        avg_bb   = df["bb_width"].mean()
        curr_bb  = df["bb_width"].iloc[-1]
        bb_ok    = curr_bb < avg_bb * sq_ratio

        last10   = df.tail(10)
        rng      = (last10["High"].max() - last10["Low"].min()) / last10["Close"].mean()
        rng_ok   = rng < rng_pct

        days_sq  = 0
        for w in reversed(df["bb_width"].values):
            if w < avg_bb * sq_ratio: days_sq += 1
            else: break

        is_sq = bb_ok and rng_ok and days_sq >= sq_days

        return {
            "symbol": symbol, "is_squeeze": is_sq,
            "bb_width_pct": round(curr_bb*100,2),
            "avg_bb_pct":   round(avg_bb*100,2),
            "range_pct":    round(rng*100,2),
            "days_in_squeeze": days_sq,
            "close": round(float(df["Close"].iloc[-1]),2),
        }
    except Exception as e:
        log.error(f"{symbol}: {e}")
        return None

def scan_pending():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT DISTINCT symbol,
               CASE WHEN symbol LIKE '%%.TO' THEN 'CAD' ELSE 'USD' END
        FROM signals_raw
        WHERE status='pending' AND created_at > NOW() - INTERVAL '4 hours'
        ORDER BY symbol
    """)
    symbols = cur.fetchall()
    conn.close()

    squeeze_list = []
    for symbol, currency in symbols:
        r = compute_bb_squeeze(symbol, currency)
        if r:
            s = "✅ SQUEEZE" if r["is_squeeze"] else "   no     "
            log.info(f"{symbol:<12} {s}  BB:{r['bb_width_pct']:>5}%  "
                     f"Avg:{r['avg_bb_pct']:>5}%  Range:{r['range_pct']:>5}%  "
                     f"Days:{r['days_in_squeeze']}  Close:{r['close']}")
            if r["is_squeeze"]:
                squeeze_list.append(r)

    log.info(f"Done: {len(symbols)} scanned, {len(squeeze_list)} in squeeze")
    return squeeze_list

if __name__ == "__main__":
    log.info("BB Squeeze (yfinance daily) starting...")
    while True:
        try:
            scan_pending()
        except Exception as e:
            log.error(f"Error: {e}")
        time.sleep(300)
