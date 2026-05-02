"""
QuantForce Apex — IB Executor v2 (runs on .11 Dell)
Receives signals via ZMQ PULL, submits bracket orders to IB Gateway.
clientId=20 | port=4002 | paper account DUP375010

Threading model:
  - Main thread: ZMQ PULL loop (non-blocking)
  - IB thread:   ib_insync event loop (ib.run())
"""
import os, sys, time, logging, threading
import zmq
from ib_insync import IB, Stock, Option, Forex, Future, Crypto, Order, util

sys.path.insert(0, os.path.expanduser("~/quantforce-apex"))
from core.interfaces import Signal, Fill, Side, ProductType, Order as QFOrder
from core.db          import write_execution, db_cursor
from core.notifier    import notify_fill

util.logToConsole(logging.WARNING)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("IBExecutor")

# ── Config ────────────────────────────────────────────────────────
IB_HOST    = os.getenv("IB_HOST",       "127.0.0.1")
IB_PORT    = int(os.getenv("IB_PORT",   "4002"))
CLIENT_ID  = int(os.getenv("IB_CLIENT_ID", "20"))
ZMQ_PORT   = int(os.getenv("QF_ZMQ_PORT",  "5558"))
IDEMPOTENT_TTL = 300

# ── IB contract factory ───────────────────────────────────────────
def make_contract(d: dict):
    sym = d["symbol"]
    p   = d.get("product", "stock")
    if p in ("stock", "etf"):  return Stock(sym, "SMART", "USD")
    elif p == "option":
        m = d.get("meta", {})
        return Option(sym, m.get("expiry",""), m.get("strike",0), m.get("right","C"), "SMART")
    elif p == "forex":   return Forex(sym)
    elif p == "futures": return Future(sym, exchange="CME")
    elif p == "crypto":  return Crypto(sym, "PAXOS", "USD")
    else:                return Stock(sym, "SMART", "USD")


class IBExecutor:
    def __init__(self):
        self.ib         = IB()
        self._seen_ids  = {}
        self._lock      = threading.Lock()
        self._ib_ready  = threading.Event()

    # ── IB thread ────────────────────────────────────────────────
    def _ib_thread(self):
        """Runs ib_insync event loop in its own asyncio loop (required for threads)."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.ib = IB()   # fresh IB instance bound to this loop
        while True:
            try:
                self.ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=15)
                logger.info(f"IB connected: {IB_HOST}:{IB_PORT} clientId={CLIENT_ID}")
                logger.info(f"Account: {self.ib.managedAccounts()}")
                self._ib_ready.set()
                self.ib.run()   # blocks inside this thread's event loop
            except Exception as e:
                logger.error(f"IB thread error: {e}")
                self._ib_ready.clear()
            logger.warning("IB disconnected — reconnecting in 15s")
            time.sleep(15)

    def start(self):
        t = threading.Thread(target=self._ib_thread, daemon=True)
        t.start()
        logger.info("IB thread started — waiting for connection...")

    # ── Idempotency ───────────────────────────────────────────────
    def _is_duplicate(self, signal_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._seen_ids = {k: v for k, v in self._seen_ids.items()
                              if now - v < IDEMPOTENT_TTL}
            if signal_id in self._seen_ids:
                return True
            self._seen_ids[signal_id] = now
        return False

    # ── Execute ───────────────────────────────────────────────────
    def execute(self, d: dict) -> bool:
        signal_id = d.get("signal_id", "")
        symbol    = d["symbol"]
        side      = d["side"]
        qty       = float(d.get("size", 0))
        entry     = float(d.get("entry_price", 0))
        sl        = float(d.get("stop_loss",   0))
        tp        = float(d.get("take_profit", 0))
        shadow    = d.get("shadow", False)

        if self._is_duplicate(signal_id):
            logger.warning(f"Duplicate signal_id {signal_id} — skipping")
            return False

        if qty < 1:
            qty = max(1, int(float(d.get("size", 0)) / entry)) if entry > 0 else 1

        logger.info(f"Signal: {symbol} {side} qty={qty:.0f} "
                    f"@{entry:.2f} SL={sl:.2f} TP={tp:.2f} shadow={shadow}")

        if shadow:
            logger.info(f"[SHADOW] {symbol} {side} {qty:.0f} shares — no order")
            return True

        # Wait up to 10s for IB to be ready
        if not self._ib_ready.wait(timeout=10):
            logger.error(f"IB not ready — cannot execute {symbol}")
            return False

        try:
            contract = make_contract(d)
            self.ib.qualifyContracts(contract)

            action  = "BUY" if side == "BUY" else "SELL"
            qty_int = int(qty)

            bracket = self.ib.bracketOrder(
                action, qty_int,
                limitPrice      = round(entry * 1.005, 2),
                takeProfitPrice = round(tp, 2),
                stopLossPrice   = round(sl, 2),
            )

            trades = []
            for order in bracket:
                trade = self.ib.placeOrder(contract, order)
                trades.append(trade)
                time.sleep(0.1)

            logger.info(f"Bracket placed: {symbol} {action} qty={qty_int} "
                        f"TP={tp:.2f} SL={sl:.2f}")

            # Wait for parent fill (up to 30s)
            parent = trades[0]
            for _ in range(30):
                time.sleep(1)
                if parent.orderStatus.status in ("Filled", "Cancelled"):
                    break

            fill_price = parent.orderStatus.avgFillPrice or entry
            filled_qty = parent.orderStatus.filled or qty_int

            fill = Fill(
                order_id    = str(parent.order.orderId),
                symbol      = symbol,
                side        = Side.BUY if action == "BUY" else Side.SELL,
                qty         = filled_qty,
                fill_price  = fill_price,
                commission  = max(1.0, filled_qty * 0.005),
                product     = ProductType(d.get("product", "stock")),
                signal_id   = signal_id,
                strategy_id = d.get("strategy_id", ""),
            )
            write_execution(fill)
            notify_fill(fill)
            logger.info(f"✅ Filled: {symbol} {action} {filled_qty}@{fill_price:.2f}")
            return True

        except Exception as e:
            logger.error(f"Execute error {symbol}: {e}", exc_info=True)
            return False


def run():
    executor = IBExecutor()
    executor.start()

    ctx    = zmq.Context()
    socket = ctx.socket(zmq.PULL)
    socket.setsockopt(zmq.RCVTIMEO, 2000)   # 2s timeout — tight loop
    socket.bind(f"tcp://*:{ZMQ_PORT}")
    logger.info(f"ZMQ PULL bound on port {ZMQ_PORT}")
    logger.info("IB Executor ready — waiting for signals...")

    while True:
        try:
            msg = socket.recv_json()
            logger.info(f"Received: {msg.get('symbol')} {msg.get('side')} "
                        f"strategy={msg.get('strategy_id')}")
            executor.execute(msg)
        except zmq.Again:
            continue   # timeout — loop back, IB thread stays alive
        except KeyboardInterrupt:
            logger.info("Stopped")
            break
        except Exception as e:
            logger.error(f"Loop error: {e}", exc_info=True)
            time.sleep(2)

    socket.close()
    ctx.term()


if __name__ == "__main__":
    run()
