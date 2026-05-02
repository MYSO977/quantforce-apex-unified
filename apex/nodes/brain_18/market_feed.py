#!/usr/bin/env python3
"""
Market Feed v1.0  (.11 Dell)
- ib_insync 订阅 universe_whitelist 的股票行情
- 计算 RVOL / VWAP偏离 / ATR
- 异动（RVOL≥2.0）写入 market_events 表
- 每30秒轮询一次
"""
import os, time, logging, psycopg2, psycopg2.extras
from datetime import datetime, timezone
from ib_insync import IB, Stock, util

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MARKET_FEED] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/heng/market_feed.log"),
    ],
)
log = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────────────
IB_HOST      = os.getenv("IB_HOST", "192.168.0.18")
IB_PORT      = int(os.getenv("IB_PORT", "4002"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "3"))   # 不同于 executor 的 clientId=2
DB_DSN       = "host=192.168.0.18 port=5432 dbname=quantforce user=heng password=quantforce123"

POLL_INTERVAL  = 30       # 秒
RVOL_THRESHOLD = 2.0      # 触发 market_event 的最低 RVOL
MAX_TICKERS    = 100      # 每次订阅上限（IB 并发限制）
ATR_BARS       = 14       # ATR 计算周期


# ── DB ────────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DB_DSN)


def load_universe(conn) -> list[str]:
    """从 universe_whitelist 取前 MAX_TICKERS 只（按 DV rank）"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol FROM universe_whitelist
            ORDER BY dollar_volume_rank ASC
            LIMIT %s
        """, (MAX_TICKERS,))
        return [r[0] for r in cur.fetchall()]


def write_event(conn, symbol, price, volume, rvol, vwap_dev, atr, event_type):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO market_events
                (ts, symbol, rvol, vwap_dev, atr, event_type, price, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            datetime.now(timezone.utc),
            symbol,
            float(rvol)      if rvol      is not None else None,
            float(vwap_dev)  if vwap_dev  is not None else None,
            float(atr)       if atr       is not None else None,
            event_type,
            float(price)     if price     is not None else None,
            float(volume)    if volume    is not None else None,
        ))
    conn.commit()


# ── 行情计算 ──────────────────────────────────────────────────────────────────
def calc_rvol(bars) -> float | None:
    """当日成交量 / 过去14日同时段均量"""
    if not bars or len(bars) < 2:
        return None
    try:
        today_vol  = bars[-1].volume
        hist_vols  = [b.volume for b in bars[-(ATR_BARS+1):-1] if b.volume > 0]
        if not hist_vols:
            return None
        avg_vol = sum(hist_vols) / len(hist_vols)
        return round(today_vol / avg_vol, 2) if avg_vol > 0 else None
    except:
        return None


def calc_atr(bars) -> float | None:
    if not bars or len(bars) < ATR_BARS + 1:
        return None
    try:
        trs = []
        for i in range(1, ATR_BARS + 1):
            b  = bars[-i]
            bp = bars[-(i+1)]
            tr = max(b.high - b.low,
                     abs(b.high - bp.close),
                     abs(b.low  - bp.close))
            trs.append(tr)
        return round(sum(trs) / len(trs), 4)
    except:
        return None


def calc_vwap_dev(ticker_data, price) -> float | None:
    """价格相对 VWAP 的偏离百分比"""
    try:
        vwap = getattr(ticker_data, "vwap", None)
        if vwap and vwap > 0 and price:
            return round((price - vwap) / vwap * 100, 2)
    except:
        pass
    return None


# ── 主循环 ────────────────────────────────────────────────────────────────────
def run():
    log.info(f"连接 IB Gateway {IB_HOST}:{IB_PORT} clientId={IB_CLIENT_ID}")
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    log.info("IB 已连接")

    conn = get_conn()
    tickers_sub = {}   # symbol -> Ticker 对象

    def refresh_subscriptions():
        """重新加载 universe 并更新订阅"""
        symbols = load_universe(conn)
        if not symbols:
            log.warning("universe_whitelist 为空，跳过订阅")
            return
        current = set(tickers_sub.keys())
        new_set = set(symbols)

        # 取消不在新池的订阅
        for sym in current - new_set:
            contract = Stock(sym, "SMART", "USD")
            ib.cancelMktData(contract)
            del tickers_sub[sym]
            log.info(f"取消订阅: {sym}")

        # 新增订阅
        for sym in new_set - current:
            contract = Stock(sym, "SMART", "USD")
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract, "233", False, False)  # 233=RT Volume
            tickers_sub[sym] = ticker
            log.info(f"订阅: {sym}")

        log.info(f"当前订阅: {len(tickers_sub)} 只")

    # 首次加载
    refresh_subscriptions()
    last_refresh = time.time()

    log.info(f"开始行情监控，RVOL阈值={RVOL_THRESHOLD}，轮询间隔={POLL_INTERVAL}s")

    while True:
        try:
            if not ib.isConnected():
                log.warning("IB 断连，重连中...")
                ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
                refresh_subscriptions()

            ib.sleep(POLL_INTERVAL)

            # 每10分钟刷新一次 universe
            if time.time() - last_refresh > 600:
                refresh_subscriptions()
                last_refresh = time.time()

            # 遍历行情
            events = 0
            for sym, tkr in list(tickers_sub.items()):
                try:
                    price  = tkr.last or tkr.close
                    volume = tkr.volume
                    if not price or not volume:
                        continue

                    # 拉日线 bars 计算指标
                    contract = Stock(sym, "SMART", "USD")
                    bars = ib.reqHistoricalData(
                        contract,
                        endDateTime="",
                        durationStr=f"{ATR_BARS + 5} D",
                        barSizeSetting="1 day",
                        whatToShow="TRADES",
                        useRTH=True,
                        keepUpToDate=False,
                    )

                    rvol     = calc_rvol(bars)
                    atr      = calc_atr(bars)
                    vwap_dev = calc_vwap_dev(tkr, price)

                    if rvol is not None and rvol >= RVOL_THRESHOLD:
                        event_type = "rvol_spike"
                        write_event(conn, sym, price, volume, rvol, vwap_dev, atr, event_type)
                        log.info(
                            f"异动 {sym} price={price} rvol={rvol} "
                            f"vwap_dev={vwap_dev}% atr={atr}"
                        )
                        events += 1

                except Exception as e:
                    log.error(f"{sym} 处理异常: {e}")
                    continue

            log.info(f"本轮扫描完成，异动事件={events}")

        except KeyboardInterrupt:
            log.info("收到中断，退出")
            break
        except Exception as e:
            log.error(f"主循环异常: {e}")
            time.sleep(5)

    ib.disconnect()
    conn.close()


if __name__ == "__main__":
    run()
