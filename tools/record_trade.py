#!/usr/bin/env python3
"""手动记录交易结果到 signal_outcomes"""
import psycopg2, sys, json
from datetime import datetime

DB = "host=192.168.0.11 port=5432 dbname=quantforce user=heng password=newpassword123"

def record_entry():
    print("\n=== 开仓记录 ===")
    symbol   = input("股票代码 (如 BOOM / MDA.TO): ").upper().strip()
    strategy = input("策略 (A=IB美股 / B=BMO加股): ").upper().strip()
    strategy = "strategy_A" if strategy == "A" else "strategy_B"
    currency = "USD" if strategy == "strategy_A" else "CAD"
    price    = float(input(f"开仓价格 ({currency}): "))
    shares   = int(input("股数: "))
    rvol     = float(input("RVOL: "))
    bb_days  = int(input("BB Squeeze天数: "))
    groq     = float(input("Groq评分 (0=无): ") or "0")

    conn = psycopg2.connect(DB)
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO signal_outcomes
          (symbol, strategy, entry_price, entry_time, shares,
           position_value, currency, rvol, bb_squeeze_days, groq_score,
           params_snapshot)
        VALUES (%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (symbol, strategy, price, shares, round(price*shares,2),
          currency, rvol, bb_days, groq if groq > 0 else None,
          json.dumps({"date": str(datetime.now().date())})))
    row_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    print(f"\n✅ 开仓记录成功 ID={row_id}  {symbol} x{shares} @ {currency}{price}")

def record_exit():
    print("\n=== 平仓记录 ===")
    row_id    = int(input("交易ID: "))
    exit_price = float(input("平仓价格: "))
    notes     = input("备注 (可空): ").strip()

    conn = psycopg2.connect(DB)
    cur  = conn.cursor()
    cur.execute("SELECT entry_price, shares, currency FROM signal_outcomes WHERE id=%s", (row_id,))
    row = cur.fetchone()
    if not row:
        print("❌ ID不存在"); return
    entry_price, shares, currency = row
    pnl_pct    = (exit_price - entry_price) / entry_price
    pnl_amount = (exit_price - entry_price) * shares
    cur.execute("""
        UPDATE signal_outcomes SET
          exit_price=%s, exit_time=NOW(),
          pnl_pct=%s, pnl_amount=%s, notes=%s
        WHERE id=%s
    """, (exit_price, round(pnl_pct,4), round(pnl_amount,2), notes or None, row_id))
    conn.commit()
    conn.close()
    sign = "✅ 盈利" if pnl_pct > 0 else "❌ 亏损"
    print(f"\n{sign}  PnL: {currency}{pnl_amount:+.2f}  ({pnl_pct*100:+.2f}%)")

def show_open():
    conn = psycopg2.connect(DB)
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, symbol, strategy, entry_price, shares, currency, entry_time::date
        FROM signal_outcomes WHERE exit_time IS NULL ORDER BY entry_time DESC
    """)
    rows = cur.fetchall()
    conn.close()
    if not rows:
        print("\n无持仓"); return
    print(f"\n{'ID':>4}  {'代码':<10} {'策略':<12} {'开仓价':>8} {'股数':>6} {'货币':<5} {'日期'}")
    print("-"*60)
    for r in rows:
        print(f"{r[0]:>4}  {r[1]:<10} {r[2]:<12} {r[3]:>8.4f} {r[4]:>6} {r[5]:<5} {r[6]}")

def show_stats():
    conn = psycopg2.connect(DB)
    cur  = conn.cursor()
    cur.execute("""
        SELECT strategy, currency,
               count(*) as trades,
               sum(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END) as wins,
               round(avg(pnl_pct)*100,2) as avg_pnl,
               round(sum(pnl_amount),2) as total_pnl
        FROM signal_outcomes WHERE exit_time IS NOT NULL
        GROUP BY strategy, currency ORDER BY strategy
    """)
    rows = cur.fetchall()
    conn.close()
    if not rows:
        print("\n暂无已平仓记录"); return
    print(f"\n{'策略':<14} {'货币':<5} {'笔数':>5} {'赢':>4} {'胜率':>6} {'均盈':>8} {'总盈亏':>10}")
    print("-"*60)
    for r in rows:
        wr = f"{r[3]/r[2]*100:.0f}%" if r[2] > 0 else "—"
        print(f"{r[0]:<14} {r[1]:<5} {r[2]:>5} {r[3]:>4} {wr:>6} {r[4]:>7}% {r[5]:>10}")

if __name__ == "__main__":
    print("\nQuantForce 交易记录工具")
    print("1) 开仓  2) 平仓  3) 查持仓  4) 统计")
    choice = input("选择: ").strip()
    if choice == "1": record_entry()
    elif choice == "2": record_exit()
    elif choice == "3": show_open()
    elif choice == "4": show_stats()
