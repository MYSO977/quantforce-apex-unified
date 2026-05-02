"""
QuantForce Apex — Telegram Notifier (Layer 0)
"""
import os, logging, requests
from core.interfaces import Signal, Fill

logger = logging.getLogger("Notifier")

def _get_cfg():
    try:
        import yaml
        path = os.path.expanduser("~/quantforce-apex/config/telegram.yaml")
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}

def _send(text: str):
    cfg   = _get_cfg()
    token = cfg.get("token") or os.getenv("TG_TOKEN", "7019506529:AAHGd21YXchNiqaJrMYAH7qNE3O7TmNRRB8")
    chat  = cfg.get("chat_id") or os.getenv("TG_CHAT_ID", "6318635327")
    if not token or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")

def notify_signal(sig: Signal):
    mode  = "🔵 SHADOW" if sig.shadow else "📡 SIGNAL"
    rr    = f"{sig.risk_reward:.1f}" if sig.risk_reward > 0 else "—"
    text  = (
        f"{mode} <b>{sig.symbol}</b> {sig.side.value}\n"
        f"Strategy: {sig.strategy_id}\n"
        f"Entry: ${sig.entry_price:.2f}  SL: ${sig.stop_loss:.2f}  TP: ${sig.take_profit:.2f}\n"
        f"R/R: {rr}  Conf: {sig.confidence:.0%}  RVOL: {sig.rvol:.1f}\n"
        f"News: {sig.news_score:.1f}/10  ID: {sig.signal_id}"
    )
    _send(text)

def notify_fill(fill: Fill):
    text = (
        f"✅ <b>FILLED</b> {fill.symbol} {fill.side.value}\n"
        f"Qty: {fill.qty:.0f}  Price: ${fill.fill_price:.2f}\n"
        f"Commission: ${fill.commission:.2f}  Net: ${fill.net_value:.2f}\n"
        f"Strategy: {fill.strategy_id}  ID: {fill.order_id}"
    )
    _send(text)

def notify_risk_reject(symbol: str, reason: str, strategy: str):
    _send(f"🛡️ <b>RISK REJECT</b> {symbol}\n{strategy}: {reason}")

def notify_daily_report(stats: dict):
    pnl   = stats.get("realized_pnl", 0)
    emoji = "📈" if pnl >= 0 else "📉"
    text  = (
        f"{emoji} <b>Daily Report</b>\n"
        f"P&L: ${pnl:+.2f}  Trades: {stats.get('trades',0)}\n"
        f"Win rate: {stats.get('win_rate',0):.0%}  "
        f"Balance: ${stats.get('balance',0):.2f}\n"
        f"Signals: {stats.get('signals',0)}  Rejected: {stats.get('rejected',0)}"
    )
    _send(text)
