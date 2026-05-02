import logging,os,yaml,asyncio
from datetime import datetime
try: from telegram import Bot; _HAS_TG=True
except ImportError: _HAS_TG=False
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
class Notifier:
    def __init__(s,cp="config/telegram.yaml"):
        s.enabled=False;s.bot=None;s.chat_id=None
        if not _HAS_TG or not os.path.exists(cp): return
        try:
            with open(cp)as f:cfg=yaml.safe_load(f)
            if not cfg.get("enabled"): return
            s.bot=Bot(token=cfg["bot_token"]);s.chat_id=cfg["chat_id"];s.config=cfg.get("notify",{})
            s.bot.get_me();s.enabled=True;logging.info("✅ Telegram 通知已启用")
        except Exception as e:logging.error(f"❌ 初始化失败:{e}")
    def _send(s,t): 
        if s.enabled and s.bot:
            try:asyncio.run(s.bot.send_message(chat_id=s.chat_id,text=t,parse_mode="Markdown"))
            except:pass
    def send_trade_signal(s,sym,act,pr,vr,dr=True):
        if not s.config.get("trade_signal"):return
        ic="🟢"if act=="BUY"else"🔴"if act=="SELL"else"⚪";md="🧪模拟"if dr else"💰实盘"
        s._send(f"{ic}*信号*\n`{sym}` `{act}` {md}\n${pr:.2f} {vr:.1f}x")
_notifier=None
def get_notifier():
    global _notifier
    if _notifier is None:_notifier=Notifier()
    return _notifier