#!/usr/bin/env python3
"""
ib_executor_v2.py
QuantForce Labs — IB 下单执行服务 (executor .11)
架构：ZMQ PULL ← signal_fusion → 下单 IB Gateway:4002 → 更新 signals_final
"""

import json
import logging
import os
import threading
import time
from datetime import datetime

import psycopg2
import zmq
from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper

# ─────────────────────────────────────────
#  配置
# ─────────────────────────────────────────

ZMQ_PULL_ADDR  = "tcp://0.0.0.0:5558"          # 监听 signal_fusion 推送
IB_HOST        = "127.0.0.1"
IB_PORT        = 4002                           # IB Gateway paper trading
IB_CLIENT_ID   = 11

PG_DSN = os.getenv(
    "QUANT_PG_DSN",
    "host=192.168.0.18 port=5432 dbname=quantforce user=heng password=quantforce123"
)

# 每笔订单固定金额（美元），根据 confidence 动态调整
ORDER_BASE_USD = 1000.0
ORDER_MAX_USD  = 5000.0

# ─────────────────────────────────────────
#  日志
# ─────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EXECUTOR] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("/tmp/ib_executor_v2.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ib_executor")

# ─────────────────────────────────────────
#  IB Wrapper / Client
# ─────────────────────────────────────────

class IBWrapper(EWrapper):
    def __init__(self):
        super().__init__()
        self.next_order_id = None
        self._order_id_event = threading.Event()
        self.order_status_cb = None   # 外部注册回调

    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self._order_id_event.set()
        log.info(f"IB 连接就绪，nextOrderId={orderId}")

    def orderStatus(self, orderId, status, filled, remaining,
                    avgFillPrice, permId, parentId, lastFillPrice,
                    clientId, whyHeld, mktCapPrice):
        log.info(f"订单状态 id={orderId} status={status} filled={filled} avgPrice={avgFillPrice}")
        if self.order_status_cb:
            self.order_status_cb(orderId, status, filled, avgFillPrice)

    def execDetails(self, reqId, contract, execution):
        log.info(f"成交 {contract.symbol} qty={execution.shares} price={execution.price} execId={execution.execId}")

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # 忽略非错误信息码
        if errorCode in (2104, 2106, 2107, 2158, 2176):
            log.debug(f"IB info [{errorCode}] {errorString}")
        else:
            log.error(f"IB错误 reqId={reqId} code={errorCode}: {errorString}")

    def wait_for_order_id(self, timeout=10) -> bool:
        return self._order_id_event.wait(timeout=timeout)


class IBExecutor(EClient):
    def __init__(self, wrapper: IBWrapper):
        EClient.__init__(self, wrapper)
        self.wrapper = wrapper

    def connect_and_run(self):
        self.connect(IB_HOST, IB_PORT, IB_CLIENT_ID)
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        ok = self.wrapper.wait_for_order_id(timeout=15)
        if not ok:
            raise TimeoutError("IB Gateway 连接超时，未收到 nextValidId")
        return t

    def get_next_order_id(self) -> int:
        oid = self.wrapper.next_order_id
        self.wrapper.next_order_id += 1
        return oid

    def place_market_order(self, symbol: str, qty: int, direction: str) -> int:
        contract = Contract()
        contract.symbol   = symbol
        contract.secType  = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        order = Order()
        order.action        = direction.upper()   # BUY / SELL
        order.orderType     = "MKT"
        order.totalQuantity = qty
        order.tif           = "DAY"

        oid = self.get_next_order_id()
        self.placeOrder(oid, contract, order)
        log.info(f"📤 下单 {direction.upper()} {qty}股 {symbol} orderId={oid}")
        return oid

# ─────────────────────────────────────────
#  数据库
# ─────────────────────────────────────────

def update_signal_final(signal_id: int, status: str, fill_price: float = None):
    sql = """
        UPDATE signals_final
        SET updated_at = NOW(),
            features = features || %s::jsonb
        WHERE id = %s;
    """
    extra = {"executor_status": status}
    if fill_price:
        extra["fill_price"] = fill_price
    try:
        with psycopg2.connect(PG_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (json.dumps(extra), signal_id))
            conn.commit()
    except Exception as e:
        log.error(f"PG更新失败 signal_id={signal_id}: {e}")

# ─────────────────────────────────────────
#  信号处理
# ─────────────────────────────────────────

def calc_qty(price: float, confidence: float) -> int:
    """根据 confidence 动态计算股数。"""
    usd = ORDER_BASE_USD + (ORDER_MAX_USD - ORDER_BASE_USD) * confidence
    qty = max(1, int(usd / price))
    return qty


def handle_signal(msg: dict, ib: IBExecutor):
    """处理一条 signal_fusion 推送的信号。"""
    ticker     = msg.get("ticker", "")
    direction  = msg.get("direction", "buy")
    confidence = float(msg.get("confidence", 0.5))
    signal_id  = msg.get("signal_id")
    price      = float(msg.get("features", {}).get("price") or 10.0)

    log.info(f"收到信号 {ticker} dir={direction} conf={confidence:.2f} signal_id={signal_id}")

    if not ticker:
        log.warning("信号缺少 ticker，跳过")
        return

    qty = calc_qty(price, confidence)
    log.info(f"计划下单 {ticker} {qty}股 (conf={confidence:.2f} price≈{price})")

    try:
        oid = ib.place_market_order(ticker, qty, direction)
        # 注册回调，成交后更新 PG
        original_cb = ib.wrapper.order_status_cb

        def on_status(order_id, status, filled, avg_price):
            if order_id == oid:
                if status in ("Filled", "PartiallyFilled"):
                    update_signal_final(signal_id, status, avg_price)
                    log.info(f"✅ {ticker} 成交 {filled}股 @ {avg_price}")
            if original_cb:
                original_cb(order_id, status, filled, avg_price)

        ib.wrapper.order_status_cb = on_status

    except Exception as e:
        log.error(f"下单失败 {ticker}: {e}")
        if signal_id:
            update_signal_final(signal_id, "error")

# ─────────────────────────────────────────
#  主循环
# ─────────────────────────────────────────

def main():
    log.info("=== ib_executor_v2 启动 ===")

    # ZMQ PULL
    ctx    = zmq.Context()
    socket = ctx.socket(zmq.PULL)
    socket.bind(ZMQ_PULL_ADDR)
    socket.setsockopt(zmq.RCVTIMEO, 5000)   # 5秒超时，允许心跳循环
    log.info(f"ZMQ PULL 监听 {ZMQ_PULL_ADDR}")

    # IB 连接
    wrapper = IBWrapper()
    ib      = IBExecutor(wrapper)
    try:
        ib.connect_and_run()
    except Exception as e:
        log.error(f"IB连接失败: {e}")
        log.warning("⚠️  IB离线模式运行（信号只记录不下单）")
        ib = None

    log.info("等待信号...")

    while True:
        try:
            raw = socket.recv_json()
            log.info(f"📨 ZMQ收到: {raw.get('ticker')} conf={raw.get('confidence')}")

            if ib and ib.isConnected():
                handle_signal(raw, ib)
            else:
                log.warning(f"IB未连接，信号丢弃: {raw.get('ticker')}")
                if raw.get("signal_id"):
                    update_signal_final(raw["signal_id"], "ib_offline")

        except zmq.Again:
            # 超时，正常心跳
            if ib and not ib.isConnected():
                log.warning("IB连接断开，尝试重连...")
                try:
                    ib.connect_and_run()
                except Exception as e:
                    log.error(f"重连失败: {e}")
            continue

        except KeyboardInterrupt:
            log.info("退出")
            break

        except Exception as e:
            log.error(f"处理异常: {e}", exc_info=True)
            time.sleep(1)

    socket.close()
    ctx.term()
    if ib:
        ib.disconnect()


if __name__ == "__main__":
    main()
