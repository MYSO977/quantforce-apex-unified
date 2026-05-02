import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ib_connector import IBTrader; from inference import predict
def run_cycle(dry_run=True):
    t = IBTrader(port=4002, paper=True)
    if not t.connect(): return
    for sym in ["TSLA","NVDA"]:
        p = t.fetch_market_prompt(sym); 
        if not p: continue
        result = predict(p); print(f"📤 [{sym}] {result['action']} (conf:{result['confidence']:.2f})")
        t.parse_and_place_order(sym, p, dry_run=dry_run)
    t.disconnect()
if __name__=="__main__": run_cycle(dry_run=True)