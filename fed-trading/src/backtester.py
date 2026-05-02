import pandas as pd, numpy as np, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class Backtester:
    def __init__(self, commission=0.0005, slippage=0.001, initial_capital=100000):
        self.commission, self.slippage, self.capital = commission, slippage, initial_capital
        self.position, self.entry_price = 0, 0
        self.current_sl, self.current_tp = 0, np.inf
        self.holding_bars = 0
        self.trades, self.equity_curve = [], [initial_capital]

    def run(self, df, strategy_fn):
        cash = self.capital
        for i, row in df.iterrows():
            signal = strategy_fn(row)
            price = row["close"]
            exec_price = price * (1 + self.slippage) if signal["action"]=="BUY" else price * (1 - self.slippage)
            
            # 持仓管理：固定 SL/TP + 最短持仓 3 根K线
            if self.position > 0:
                self.holding_bars += 1
                if self.holding_bars >= 3 and (row["low"] <= self.current_sl or row["high"] >= self.current_tp):
                    exit_price = row["close"] * (1 - self.slippage)
                    pnl = (exit_price - self.entry_price) * self.position - (exit_price * self.position * self.commission * 2)
                    cash += pnl
                    self.trades.append({"exit": i, "pnl": pnl, "type": "SL/TP"})
                    self.position, self.holding_bars = 0, 0
                    self.current_sl, self.current_tp = 0, np.inf
            
            # 开仓信号
            if signal["action"] == "BUY" and self.position == 0 and self.holding_bars == 0 and cash > exec_price:
                qty = int(cash * 0.2 / exec_price)  # 降至 20% 仓位
                cost = qty * exec_price * (1 + self.commission)
                cash -= cost
                self.position, self.entry_price = qty, exec_price
                self.current_sl = signal.get("stop_loss", self.entry_price * 0.98)
                self.current_tp = signal.get("take_profit", self.entry_price * 1.06)
                self.holding_bars = 0
                self.trades.append({"entry": i, "price": exec_price, "qty": qty, "type": "OPEN"})
            
            self.equity_curve.append(cash + (self.position * price))
        return self._calc_metrics()

    def _calc_metrics(self):
        eq = pd.Series(self.equity_curve)
        returns = eq.pct_change().dropna()
        total_return = (eq.iloc[-1] / eq.iloc[0]) - 1
        sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0
        drawdown = (eq / eq.cummax() - 1).min()
        wins = [t["pnl"] for t in self.trades if t.get("pnl",0)>0]
        win_rate = len(wins) / max(1, len([t for t in self.trades if "pnl" in t]))
        return {"total_return": f"{total_return:.2%}", "sharpe": round(sharpe,2), "max_drawdown": f"{drawdown:.2%}", "win_rate": f"{win_rate:.1%}", "final_equity": round(eq.iloc[-1],2), "trade_count": len([t for t in self.trades if "pnl" in t])}