import pandas as pd, numpy as np
from datasets import Dataset
def _gen(n=30):
    if n<=20: n=30
    np.random.seed(42); out=[]
    for s in ["TSLA","NVDA","AAPL"]:
        p=np.random.uniform(100,900,n); v=np.random.uniform(1e6,1e8,n)
        df=pd.DataFrame({"c":p,"v":v})
        df["m5"]=df.c.rolling(5).mean(); df["m20"]=df.c.rolling(20).mean(); df["vr"]=df.v/df.v.rolling(20).mean()
        for i in range(20,n):
            r=df.iloc[i]
            if pd.isna(r.m20): continue
            act="BUY" if r.c>r.m20 and r.vr>1.2 else "SELL" if r.c<r.m20 and r.vr>1.2 else "HOLD"
            out.append({"text":f"Asset:{s}|Price:{r.c:.2f}|MA5/20:{r.m5:.2f}/{r.m20:.2f}|VolRatio:{r.vr:.1f}x|Node:rt|Action:{act}"})
    return out
def load_real_market_data(node_id="g", samples_count=30, **kw): return Dataset.from_list(_gen(samples_count))
def fetch_realtime_prompt(sym, **kw):
    for r in _gen(30):
        if sym.upper() in r["text"]: return r["text"].split("|Action:")[0]+"|Action:"
    return None