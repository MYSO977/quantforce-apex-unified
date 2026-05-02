import logging
from ib_insync import IB, Stock, MarketOrder
logging.basicConfig(level=logging.INFO)
class IBTrader:
    def __init__(s,host="127.0.0.1",port=4002,client_id=100,paper=True):
        s.ib,s.connected=IB(),False; s.host,s.port,s.client_id,s.paper=host,port,client_id,paper
    def connect(s):
        try: s.ib.connect(s.host,s.port,clientId=s.client_id,readonly=False,timeout=10); s.connected=True; logging.info(f"✅ 连接 IB {'模拟'if s.paper else'实盘'}"); return True
        except Exception as e: logging.error(f"❌ 连接失败:{e}"); return False
    def disconnect(s):
        if s.connected: s.ib.disconnect(); s.connected=False
    def fetch_market_prompt(s,symbol,**k): return f"Asset:{symbol}|Price:245.30|MA5/20:242.10/238.50|VolRatio:1.5x|Node:ib|Action:"
    def parse_and_place_order(s,symbol,output,dry_run=True):
        import re; m=re.search(r"\b(BUY|SELL|HOLD)\b",output,re.I)
        if not m or m.group(1)=="HOLD": return None
        action,qty=m.group(1).upper(),10; logging.info(f"📤 [{symbol}] {action} x{qty} | DryRun:{dry_run}")
        return {"status":"DRY_RUN"if dry_run else"PENDING","symbol":symbol,"action":action}