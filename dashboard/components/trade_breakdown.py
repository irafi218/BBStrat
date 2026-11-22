"""dashboard/pages/trade_breakdown.py — Per-trade breakdown with score/factors."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from database.db_manager import DBManager
from utils.config_loader import load_config

DARK = "#0d1117"
GRID = "#21262d"


@st.cache_data(ttl=30)
def load_trades(symbol, status, limit):
    cfg = load_config()
    db  = DBManager(cfg["data"]["db_path"])
    return db.get_trades(
        symbol=None if symbol == "All" else symbol,
        status=None if status == "All" else status,
        limit=limit,
    )


def render():
    st.title("📋 Trade Breakdown")
    cfg = load_config()

    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    symbol  = c1.selectbox("Symbol",  ["All"] + cfg["trading"]["symbols"])
    status  = c2.selectbox("Status",  ["All", "OPEN", "CLOSED", "CANCELLED"])
    side    = c3.selectbox("Side",    ["All", "BUY", "SELL"])
    limit   = c4.number_input("Max rows", 50, 1000, 200, 50)

    df = load_trades(symbol, status, int(limit))

    if df.empty:
        st.info("No trades found. Trades are logged during live trading or when callbacks are wired into the backtest.")
        return

    if side != "All":
        df = df[df["side"] == side]

    closed = df[df["status"] == "CLOSED"]

    # ── Summary metrics ───────────────────────────────────────────────────────
    m1, m2, m3, m4, m5 = st.columns(5)
    total_pnl = closed["pnl"].sum() if not closed.empty else 0
    win_rate  = (closed["pnl"] > 0).mean() * 100 if not closed.empty else 0
    avg_pnl   = closed["pnl"].mean() if not closed.empty else 0
    best      = closed["pnl"].max() if not closed.empty else 0
    worst     = closed["pnl"].min() if not closed.empty else 0

    m1.metric("Total PnL",    f"${total_pnl:,.2f}")
    m2.metric("Win Rate",     f"{win_rate:.1f}%")
    m3.metric("Avg PnL",      f"${avg_pnl:.2f}")
    m4.metric("Best Trade",   f"${best:.2f}")
    m5.metric("Worst Trade",  f"${worst:.2f}")

    st.divider()

    tabs = st.tabs(["📊 PnL Waterfall", "🎯 Factor Analysis", "📋 Trade Table"])

    # ── Tab 1: PnL Waterfall ──────────────────────────────────────────────────
    with tabs[0]:
        if not closed.empty:
            closed_s = closed.sort_values("entry_time").copy()
            closed_s["trade_num"] = range(1, len(closed_s) + 1)

            # Waterfall chart
            measure = ["relative"] * len(closed_s) + ["total"]
            x_vals  = [f"#{i}" for i in closed_s["trade_num"]] + ["Total"]
            y_vals  = list(closed_s["pnl"]) + [closed_s["pnl"].sum()]
            colors  = ["#3fb950" if v >= 0 else "#f85149" for v in closed_s["pnl"]] + ["#58a6ff"]

            fig = go.Figure(go.Waterfall(
                x=x_vals, y=y_vals,
                measure=measure,
                connector=dict(line=dict(color="#21262d", width=1)),
                increasing=dict(marker=dict(color="#3fb950")),
                decreasing=dict(marker=dict(color="#f85149")),
                totals=dict(marker=dict(color="#58a6ff")),
                text=[f"${v:.0f}" for v in y_vals],
                textposition="outside",
            ))
            fig.update_layout(template="plotly_dark", height=380,
                margin=dict(l=0,r=0,t=0,b=0),
                paper_bgcolor=DARK, plot_bgcolor=DARK,
                xaxis_title="Trade", yaxis_title="PnL ($)",
                showlegend=False)
            fig.update_xaxes(gridcolor=GRID)
            fig.update_yaxes(gridcolor=GRID)
            st.plotly_chart(fig, use_container_width=True)

            # Cumulative PnL line
            closed_s["cum_pnl"] = closed_s["pnl"].cumsum()
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=closed_s["trade_num"], y=closed_s["cum_pnl"],
                mode="lines+markers", name="Cumulative PnL",
                line=dict(color="#58a6ff", width=2),
                fill="tozeroy", fillcolor="rgba(88,166,255,0.08)",
                marker=dict(
                    color=["#3fb950" if p > 0 else "#f85149" for p in closed_s["pnl"]],
                    size=7,
                ),
            ))
            fig2.add_hline(y=0, line_color="#21262d", line_dash="dash")
            fig2.update_layout(template="plotly_dark", height=250,
                margin=dict(l=0,r=0,t=0,b=0),
                paper_bgcolor=DARK, plot_bgcolor=DARK,
                xaxis_title="Trade #", yaxis_title="Cumulative PnL ($)")
            fig2.update_xaxes(gridcolor=GRID)
            fig2.update_yaxes(gridcolor=GRID)
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No closed trades to display.")

    # ── Tab 2: Factor Analysis ────────────────────────────────────────────────
    with tabs[1]:
        st.subheader("Entry Factor Performance")
        st.info("Factor data is recorded during live trading when the confluence strategy logs each signal. Run the bot live to populate this view.")

        if not closed.empty and "exit_reason" in closed.columns:
            st.subheader("Exit Reason Breakdown")
            reason_counts = closed["exit_reason"].value_counts().reset_index()
            reason_counts.columns = ["reason", "count"]
            fig3 = px.pie(reason_counts, values="count", names="reason",
                color_discrete_sequence=["#3fb950","#f85149","#58a6ff","#e3b341"],
                template="plotly_dark", hole=0.4)
            fig3.update_layout(height=300, margin=dict(l=0,r=0,t=0,b=0),
                paper_bgcolor=DARK)
            st.plotly_chart(fig3, use_container_width=True)

        if not closed.empty:
            st.subheader("PnL by Symbol")
            by_sym = closed.groupby("symbol")["pnl"].agg(["sum","mean","count"]).reset_index()
            by_sym.columns = ["symbol","total_pnl","avg_pnl","trades"]
            fig4 = px.bar(by_sym, x="symbol", y="total_pnl",
                color="total_pnl", color_continuous_scale=["#f85149","#161b22","#3fb950"],
                color_continuous_midpoint=0, template="plotly_dark", text="trades")
            fig4.update_layout(height=280, margin=dict(l=0,r=0,t=0,b=0),
                paper_bgcolor=DARK, plot_bgcolor=DARK)
            st.plotly_chart(fig4, use_container_width=True)

    # ── Tab 3: Trade Table ────────────────────────────────────────────────────
    with tabs[2]:
        display_cols = ["trade_id","symbol","side","entry_price","exit_price",
                        "quantity","entry_time","exit_time","status",
                        "pnl","pnl_pct","stop_loss","take_profit","exit_reason"]
        display_cols = [c for c in display_cols if c in df.columns]
        df_show = df[display_cols].copy()
        if "pnl_pct" in df_show:
            df_show["pnl_pct"] = (df_show["pnl_pct"] * 100).round(2).astype(str) + "%"

        def highlight_pnl(val):
            try:
                v = float(str(val).replace("%","").replace("$",""))
                if v > 0:  return "color: #3fb950"
                if v < 0:  return "color: #f85149"
            except (ValueError, TypeError):
                pass
            return ""

        st.dataframe(
            df_show.style.applymap(highlight_pnl, subset=["pnl"]),
            use_container_width=True, height=500,
        )
        csv = df_show.to_csv(index=False).encode()
        st.download_button("⬇️ Export CSV", csv, "trades.csv", "text/csv")
