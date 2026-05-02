import pandas as pd, numpy as np, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class StrategyEngine:
    """多因子信号引擎：趋势 + 动量 + 波动率 + 成交量确认"""
    def __init__(self, params=None):
        defaults = {
            "ma_fast": 5, "ma_slow": 20, "roc_period": 10,
            "vol_threshold": 1.2, "atr_mult_sl": 2.0, "atr_mult_tp": 3.0,
            "min_confidence": 0.6
        }
        self.p = {**defaults, **(params or {})}
        # 🔹 关键修复：确保 rolling 窗口参数为整数（pandas 要求）
        for k in ["ma_fast", "ma_slow", "roc_period"]:
            if k in self.p:
                self.p[k] = int(self.p[k])  # 合并而非覆盖
    def compute_features(self, df):
        """向量化计算特征（回测用）"""
        df = df.copy()
        df["MA_Fast"] = df["close"].rolling(self.p["ma_fast"]).mean()
        df["MA_Slow"] = df["close"].rolling(self.p["ma_slow"]).mean()
        df["ROC"] = df["close"].pct_change(self.p["roc_period"])
        df["VolRatio"] = df["volume"] / df["volume"].rolling(20).mean()
        df["ATR"] = (df["high"]-df["low"]).rolling(14).mean()
        df["Trend"] = (df["MA_Fast"] > df["MA_Slow"]).astype(int)
        df["Momentum"] = (df["ROC"] > 0).astype(int)
        df["VolConfirm"] = (df["VolRatio"] > self.p["vol_threshold"]).astype(int)
        return df.dropna()
    
    def get_signal(self, row):
        """实时推理接口（兼容 inference.py 调用）"""
        score = 0
        reasons = []
        if row.get("Trend", 0): score += 0.4; reasons.append("趋势向上")
        if row.get("Momentum", 0): score += 0.3; reasons.append("动量为正")
        if row.get("VolConfirm", 0): score += 0.3; reasons.append("放量确认")
        
        if score >= self.p["min_confidence"]:
            action = "BUY"
        elif score <= 0.2:
            action = "SELL"
        else:
            action = "HOLD"
        
        # 计算止损止盈（基于 ATR）
        atr = row.get("ATR", row["close"]*0.02)
        sl = row["close"] - self.p["atr_mult_sl"]*atr if action=="BUY" else row["close"] + self.p["atr_mult_sl"]*atr
        tp = row["close"] + self.p["atr_mult_tp"]*atr if action=="BUY" else row["close"] - self.p["atr_mult_tp"]*atr
        
        return {
            "action": action,
            "confidence": min(score, 1.0),
            "stop_loss": sl,
            "take_profit": tp,
            "reason": " | ".join(reasons) if reasons else "无明确信号"
        }