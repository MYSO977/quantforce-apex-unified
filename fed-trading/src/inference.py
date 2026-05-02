import logging
logging.basicConfig(level=logging.INFO)
def predict(prompt):
    if not prompt: return {"action":"HOLD","confidence":0.0}
    try:
        parts=dict(p.split(":") for p in prompt.split("|") if ":" in p)
        price=float(parts.get("Price","0")); ma20=float(parts.get("MA5/20","0/0").split("/")[1])
        vr=float(parts.get("VolRatio","1.0").replace("x",""))
        action="BUY" if price>ma20 and vr>1.2 else "SELL" if price<ma20 and vr>1.2 else "HOLD"
        return {"action":action,"confidence":0.85 if action!="HOLD" else 0.5}
    except: return {"action":"HOLD","confidence":0.0}