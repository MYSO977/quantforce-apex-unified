"""
QuantForce Apex — PostgreSQL Connection Factory (Layer 0)
Field mapping compatible with v1 schema + new apex fields.
"""
import os, logging, json
import psycopg2, psycopg2.extras
from contextlib import contextmanager

logger = logging.getLogger("DB")

DB_CONFIG = {
    "host":     os.getenv("QF_PG_HOST",     "192.168.0.11"),
    "port":     int(os.getenv("QF_PG_PORT", "5432")),
    "dbname":   os.getenv("QF_PG_DB",       "quantforce"),
    "user":     os.getenv("QF_PG_USER",     "heng"),
    "password": os.getenv("QF_PG_PASS",     "newpassword123"),
    "connect_timeout": 5,
}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

@contextmanager
def db_cursor(commit: bool = True):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"DB error: {e}")
        raise
    finally:
        conn.close()

def write_signal(signal) -> bool:
    """Write Signal to signals_raw — compatible with v1+apex schema."""
    sql = """
        INSERT INTO signals_raw
            (signal_id, symbol, side, strategy_id, product, confidence,
             entry_price, stop_loss, take_profit, size, news_score, rvol,
             atr, status, shadow, created_at, updated_at, meta,
             direction, score, signal_type, source)
        VALUES
            (%(signal_id)s, %(symbol)s, %(side)s, %(strategy_id)s,
             %(product)s, %(confidence)s, %(entry_price)s, %(stop_loss)s,
             %(take_profit)s, %(size)s, %(news_score)s, %(rvol)s, %(atr)s,
             %(status)s, %(shadow)s,
             to_timestamp(%(created_at)s), to_timestamp(%(updated_at)s),
             %(meta)s::jsonb,
             %(direction)s, %(score)s, 'tech', 'apex')
        ON CONFLICT (signal_id) DO UPDATE SET
            status=EXCLUDED.status,
            updated_at=EXCLUDED.updated_at
    """
    try:
        with db_cursor() as cur:
            cur.execute(sql, {
                "signal_id":   signal.signal_id,
                "symbol":      signal.symbol,
                "side":        signal.side.value,
                "strategy_id": signal.strategy_id,
                "product":     signal.product.value,
                "confidence":  signal.confidence,
                "entry_price": signal.entry_price,
                "stop_loss":   signal.stop_loss,
                "take_profit": signal.take_profit,
                "size":        signal.size,
                "news_score":  signal.news_score,
                "rvol":        signal.rvol,
                "atr":         signal.atr,
                "status":      signal.status.value,
                "shadow":      signal.shadow,
                "created_at":  signal.created_at,
                "updated_at":  signal.updated_at,
                "meta":        json.dumps(signal.meta),
                "direction":   signal.side.value.lower(),
                "score":       float(signal.news_score or 0),
            })
        return True
    except Exception as e:
        logger.error(f"write_signal error: {e}")
        return False

def write_execution(fill) -> bool:
    """Write Fill to executions — compatible with v1+apex schema."""
    sql = """
        INSERT INTO executions
            (order_id, signal_id, symbol, side, action, qty, fill_price,
             price, commission, product, strategy_id, filled_at, ts, meta)
        VALUES
            (%(order_id)s, %(signal_id)s, %(symbol)s, %(side)s, %(side)s,
             %(qty)s, %(fill_price)s, %(fill_price)s, %(commission)s,
             %(product)s, %(strategy_id)s,
             to_timestamp(%(filled_at)s), to_timestamp(%(filled_at)s),
             %(meta)s)
        ON CONFLICT DO NOTHING
    """
    try:
        with db_cursor() as cur:
            cur.execute(sql, {
                "order_id":   fill.order_id,
                "signal_id":  fill.signal_id,
                "symbol":     fill.symbol,
                "side":       fill.side.value,
                "qty":        fill.qty,
                "fill_price": fill.fill_price,
                "commission": fill.commission,
                "product":    fill.product.value,
                "strategy_id":fill.strategy_id,
                "filled_at":  fill.filled_at,
                "meta":       json.dumps(fill.meta),
            })
        return True
    except Exception as e:
        logger.error(f"write_execution error: {e}")
        return False

def get_account_state():
    """Read account state — maps v1 fields to apex AccountState."""
    try:
        with db_cursor(commit=False) as cur:
            cur.execute("""
                SELECT
                    COALESCE(total_balance, net_liquidation, 1200) AS total_balance,
                    COALESCE(settled_balance, cash, 1200)          AS settled_balance,
                    COALESCE(pending_settlement, 0)                AS pending_settlement,
                    COALESCE(unrealized_pnl, 0)                    AS unrealized_pnl,
                    COALESCE(realized_pnl_today, day_pnl, 0)       AS realized_pnl_today,
                    COALESCE(open_positions, positions, '{}')      AS open_positions
                FROM account_state
                ORDER BY updated_at DESC LIMIT 1
            """)
            return cur.fetchone()
    except Exception as e:
        logger.error(f"get_account_state error: {e}")
        return None
