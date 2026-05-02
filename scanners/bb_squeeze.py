#!/usr/bin/env python3
"""BB Squeeze + N-day Range consolidation detector"""
import psycopg2, yaml, logging, time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BB] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DB  = "host=192.168.0.11 port=5432 dbname=quantforce user=heng password=newpassword123"
CFG = yaml.safe_load(open("/home/heng/quantforce-apex/config/params/params_latest.yaml"))

def get_conn(): return psycopg2.connect(DB)

def compute_bb_squeeze(symbol, currency="USD"):
    """Returns (is_squeeze, bb_width_pct, range_pct, days_in_squeeze) or None"""
    try:
        #conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT ts, open, high, low, close, volume
            FROM market_events
            WHERE symbol=%s AND ts >= NOW() - INTERVAL '30 days'
            ORDER BY ts ASC
        """, (symbol,))
        rows = cur.fetchall()
        conn.close()
        if len(rows) < 20:
            return None

        df = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"])
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        df = df.sort_values("ts").tail(25)

        # Bollinger Bands (20-day, 2 std)
        df["ma20"]   = df["close"].rolling(20).mean()
        df["std20"]  = df["close"].rolling(20).std()
        df["bb_upper"] = df["ma20"] + 2 * df["std20"]
        df["bb_lower"] = df["ma20"] - 2 * df["std20"]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["ma20"]
        df = df.dropna()

        if len(df) < 5:
            return None

        # Strategy params
        strat   = CFG.get("strategy_B" if currency=="CAD" else "strategy_A", {})
        sq_ratio = strat.get("bb_squeeze_ratio", 0.80)
        rng_pct  = strat.get("price_range_pct", 0.08)
        sq_days  = strat.get("bb_squeeze_days", 5)

        # Current BB width vs 20-day average
        avg_bb_width   = df["bb_width"].mean()
        curr_bb_width  = df["bb_width"].iloc[-1]
        bb_squeeze_ok  = curr_bb_width < avg_bb_width * sq_ratio

        # N-day price range
        last10 = df.tail(10)
        price_range = (last10["high"].max() - last10["low"].min()) / last10["close"].mean()
        range_ok = price_range < rng_pct

        # Days in squeeze
        days_in_squeeze = 0
        for w in reversed(df["bb_width"].values):
            if w < avg_bb_width * sq_ratio:
                days_in_squeeze += 1
            else:
                break

        is_squeeze = bb_squeeze_ok and range_ok and days_in_squeeze >= sq_days

        return {
            "symbol":          symbol,
            "is_squeeze":      is_squeeze,
            "bb_width_pct":    round(curr_bb_width * 100, 2),
            "avg_bb_width_pct":round(avg_bb_width * 100, 2),
            "range_pct":       round(price_range * 100, 2),
            "days_in_squeeze": days_in_squeeze,
            "bb_ok":           bb_squeeze_ok,
            "range_ok":        range_ok,
        }
    except Exception as e:
        log.error(f"{symbol}: {e}")
        return None

def scan_all():
    #conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT DISTINCT symbol,
               CASE WHEN symbol LIKE '%.TO' THEN 'CAD' ELSE 'USD' END as currency
        FROM signals_raw
        WHERE status='pending'
          AND created_at > NOW() - INTERVAL '2 hours'
        ORDER BY symbol
    """)
    symbols = cur.fetchall()
    conn.close()

    results = []
    for symbol, currency in symbols:
        r = compute_bb_squeeze(symbol, currency)
        if r:
            status = "SQUEEZE" if r["is_squeeze"] else "no squeeze"
            log.info(f"{symbol:<12} {status:<12} BB:{r['bb_width_pct']}% "
                     f"Range:{r['range_pct']}% Days:{r['days_in_squeeze']}")
            if r["is_squeeze"]:
                results.append(r)

    log.info(f"Scan complete: {len(symbols)} symbols, {len(results)} in squeeze")
    return results

if __name__ == "__main__":
    log.info("BB Squeeze engine starting...")
    while True:
        try:
            scan_all()
        except Exception as e:
            log.error(f"Scan error: {e}")
        time.sleep(300)  # 5 minutes
