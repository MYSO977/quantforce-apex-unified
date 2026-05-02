import numpy as np, logging
logging.basicConfig(level=logging.INFO)

class PositionSizer:
    """动态仓位管理：波动率目标 + Kelly 上限 + 硬性风控"""
    def __init__(self, account_balance=100000, max_risk_pct=0.02, kelly_fraction=0.25):
        self.balance = account_balance
        self.max_risk = account_balance * max_risk_pct  # 单笔最大亏损额
        self.kelly_frac = kelly_fraction
    
    def calculate_size(self, entry_price, stop_loss, volatility, win_rate=0.55, payoff_ratio=1.5):
        """返回: 数量, 建议仓位占比, 风控状态"""
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0: risk_per_share = entry_price * 0.01
        
        # 1. 基于硬性风控的数量
        qty_risk = int(self.max_risk / risk_per_share)
        
        # 2. Kelly 公式上限
        kelly = (win_rate * payoff_ratio - (1 - win_rate)) / payoff_ratio
        kelly = max(0, min(kelly * self.kelly_frac, 0.15))  # 限制在 15% 以内
        qty_kelly = int((self.balance * kelly) / entry_price)
        
        # 3. 波动率调整（高波动降仓）
        vol_adj = max(0.5, min(1.0, 0.02 / max(volatility, 0.005)))
        qty_final = int(min(qty_risk, qty_kelly) * vol_adj)
        qty_final = max(1, qty_final)  # 至少 1 股
        
        position_value = qty_final * entry_price
        risk_pct = (qty_final * risk_per_share) / self.balance
        
        return {
            "quantity": qty_final,
            "position_value": position_value,
            "risk_pct": risk_pct,
            "status": "✅ 风控通过" if risk_pct <= 0.02 else "⚠️ 接近风控上限"
        }