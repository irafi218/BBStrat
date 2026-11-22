"""dashboard/pages/trade_log.py — Filterable trade journal page."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from database.db_manager import DBManager
from utils.config_loader import load_config


def render_trade_log():
    st.title("📋 Trade Log")

    cfg = load_config()
    db  = DBManager(cfg["data"]["db_path"])

    # ── Filters ───────────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        symbols = ["All"] + cfg["trading"]["symbols"]
        symbol_filter = st.selectbox("Symbol", symbols)
    with col2:
        status_filter = st.selectbox("Status", ["All", "OPEN", "CLOSED", "CANCELLED"])
    with col3:
        limit = st.number_input("Max rows", min_value=10, max_value=1000, value=100, step=10)

    sym  = None if symbol_filter == "All" else symbol_filter
    stat = None if status_filter == "All" else status_filter
    df   = db.get_trades(symbol=sym, status=stat, limit=int(limit))

    if df.empty:
        st.info("No trades found matching the selected filters.")
        return

    # ── Summary row ───────────────────────────────────────────────────────────
    closed = df[df["status"] == "CLOSED"]
    if not closed.empty:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total PnL",   f"${closed['pnl'].sum():,.2f}")
        c2.metric("Trades shown", len(df))
        win_rate = (closed["pnl"] > 0).mean() * 100 if len(closed) else 0
        c3.metric("Win Rate",    f"{win_rate:.1f}%")
        c4.metric("Avg PnL %",  f"{closed['pnl_pct'].mean()*100:.2f}%")

    # ── Table ─────────────────────────────────────────────────────────────────
    display_cols = [
        "trade_id", "symbol", "side", "entry_price", "exit_price",
        "quantity", "entry_time", "exit_time", "status",
        "pnl", "pnl_pct", "exit_reason",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    df_display = df[display_cols].copy()
    if "pnl_pct" in df_display.columns:
        df_display["pnl_pct"] = (df_display["pnl_pct"] * 100).round(2).astype(str) + "%"

    def color_pnl(val):
        try:
            v = float(str(val).replace("%", ""))
            return "color: #3fb950" if v > 0 else ("color: #f85149" if v < 0 else "")
        except (ValueError, TypeError):
            return ""

    st.dataframe(
        df_display.style.applymap(color_pnl, subset=["pnl", "pnl_pct"]),
        use_container_width=True,
        height=500,
    )

    # ── Export ────────────────────────────────────────────────────────────────
    csv = df_display.to_csv(index=False).encode()
    st.download_button("⬇️ Export CSV", csv, "trade_log.csv", "text/csv")
