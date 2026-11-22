import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.db_manager import DBManager
from utils.config_loader import load_config
import pandas as pd

cfg = load_config()
db  = DBManager(cfg["data"]["db_path"])

trades = db.get_trades(limit=500)
equity = db.get_equity_curve(limit=500)

closed = trades[trades["status"] == "CLOSED"] if not trades.empty else pd.DataFrame()
open_t = trades[trades["status"] == "OPEN"]   if not trades.empty else pd.DataFrame()

print("=" * 40)
print("   PAPER TRADING SUMMARY")
print("=" * 40)
print(f"Total trades   : {len(trades)}")
print(f"Open trades    : {len(open_t)}")
print(f"Closed trades  : {len(closed)}")

if not closed.empty:
    wins = (closed["pnl"] > 0).sum()
    print(f"Win rate       : {wins/len(closed)*100:.1f}%")
    print(f"Total PnL      : ${closed['pnl'].sum():.2f}")
    print(f"Avg PnL        : ${closed['pnl'].mean():.2f}")
    print(f"Best trade     : ${closed['pnl'].max():.2f}")
    print(f"Worst trade    : ${closed['pnl'].min():.2f}")
    print()
    print("--- Recent Trades ---")
    cols = ["symbol","side","entry_price","exit_price","pnl","exit_reason","entry_time"]
    cols = [c for c in cols if c in closed.columns]
    print(closed[cols].tail(10).to_string(index=False))

if not equity.empty:
    print()
    print("--- Portfolio ---")
    print(f"Current value  : ${equity.iloc[-1]['portfolio_value']:.2f}")
    print(f"Peak value     : ${equity['portfolio_value'].max():.2f}")
    print(f"Max drawdown   : {equity['drawdown_pct'].max()*100:.2f}%")
