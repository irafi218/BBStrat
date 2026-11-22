"""dashboard/pages/backtest_analytics.py — Deep backtest analytics."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from database.db_manager import DBManager
from utils.config_loader import load_config


DARK = "#0d1117"
GRID = "#21262d"


@st.cache_data(ttl=60)
def load_data():
    cfg    = load_config()
    db     = DBManager(cfg["data"]["db_path"])
    trades = db.get_trades(limit=2000)
    bt_res = db.get_backtest_results(limit=20)
    return trades, bt_res


def render():
    st.title("🔬 Backtest Analytics")
    trades, bt_res = load_data()

    if bt_res.empty:
        st.info("No backtest results yet. Run: `python scripts/run_backtest.py`")
        return

    # ── Latest run selector ───────────────────────────────────────────────────
    bt_res["label"] = bt_res["run_time"].str[:16] + "  " + bt_res["symbol"] + "/" + bt_res["timeframe"]
    selected = st.selectbox("Select backtest run", bt_res["label"].tolist())
    row = bt_res[bt_res["label"] == selected].iloc[0]

    # ── KPI row ───────────────────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    ret_delta = f"{row['total_return']*100:+.2f}%"
    k1.metric("Total Return",  f"{row['total_return']*100:.2f}%", ret_delta)
    k2.metric("Sharpe Ratio",  f"{row['sharpe_ratio']:.2f}"  if row['sharpe_ratio'] else "—")
    k3.metric("Max Drawdown",  f"{(row['max_drawdown'] or 0)*100:.2f}%")
    k4.metric("Win Rate",      f"{(row['win_rate'] or 0)*100:.1f}%")
    k5.metric("Total Trades",  int(row["total_trades"] or 0))
    k6.metric("Final Value",   f"${row['final_value']:,.2f}")

    st.divider()

    closed = trades[trades["status"] == "CLOSED"].copy() if not trades.empty else pd.DataFrame()
    if closed.empty:
        st.info("No closed trades in database. Trade log is populated during live trading.")
        _show_backtest_charts(bt_res)
        return

    closed["entry_time"] = pd.to_datetime(closed["entry_time"])
    closed["exit_time"]  = pd.to_datetime(closed["exit_time"])
    closed = closed.sort_values("entry_time")
    closed["cumulative_pnl"]    = closed["pnl"].cumsum()
    closed["cumulative_return"] = closed["cumulative_pnl"] / float(row["initial_cash"])
    peak = closed["cumulative_pnl"].cummax()
    closed["drawdown"] = (closed["cumulative_pnl"] - peak) / (float(row["initial_cash"]) + peak.clip(lower=0))

    tabs = st.tabs(["📈 Equity & Drawdown", "📅 Monthly Returns", "🎯 Trade Analysis", "📊 Run History"])

    # ── Tab 1: Equity + Drawdown ──────────────────────────────────────────────
    with tabs[0]:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.65, 0.35], vertical_spacing=0.03,
            subplot_titles=("Cumulative PnL", "Drawdown %"))

        fig.add_trace(go.Scatter(x=closed["exit_time"], y=closed["cumulative_pnl"],
            mode="lines", name="Cum PnL", line=dict(color="#3fb950", width=2),
            fill="tozeroy", fillcolor="rgba(63,185,80,0.08)"), row=1, col=1)

        wins  = closed[closed["pnl"] > 0]
        losses = closed[closed["pnl"] <= 0]
        fig.add_trace(go.Scatter(x=wins["exit_time"],   y=wins["cumulative_pnl"],
            mode="markers", name="Win", marker=dict(color="#3fb950", size=6, symbol="circle")), row=1, col=1)
        fig.add_trace(go.Scatter(x=losses["exit_time"], y=losses["cumulative_pnl"],
            mode="markers", name="Loss", marker=dict(color="#f85149", size=6, symbol="circle")), row=1, col=1)

        fig.add_trace(go.Scatter(x=closed["exit_time"], y=closed["drawdown"] * 100,
            mode="lines", name="Drawdown", line=dict(color="#f85149", width=1.5),
            fill="tozeroy", fillcolor="rgba(248,81,73,0.15)"), row=2, col=1)

        fig.update_layout(template="plotly_dark", height=500,
            margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor=DARK, plot_bgcolor=DARK)
        fig.update_xaxes(gridcolor=GRID)
        fig.update_yaxes(gridcolor=GRID)
        st.plotly_chart(fig, use_container_width=True)

        # Win/loss streak
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("PnL Distribution")
            fig2 = px.histogram(closed, x="pnl", nbins=30,
                color_discrete_sequence=["#58a6ff"], template="plotly_dark")
            fig2.add_vline(x=0, line_color="#f85149", line_dash="dash")
            fig2.update_layout(height=280, margin=dict(l=0,r=0,t=0,b=0),
                paper_bgcolor=DARK, plot_bgcolor=DARK)
            st.plotly_chart(fig2, use_container_width=True)
        with col_b:
            st.subheader("Consecutive Win/Loss Streaks")
            streaks = []
            current, count = None, 0
            for _, r in closed.iterrows():
                outcome = "W" if r["pnl"] > 0 else "L"
                if outcome == current:
                    count += 1
                else:
                    if current:
                        streaks.append({"type": current, "length": count})
                    current, count = outcome, 1
            if current:
                streaks.append({"type": current, "length": count})
            sdf = pd.DataFrame(streaks)
            if not sdf.empty:
                fig3 = px.bar(sdf, y="length",
                    color="type", color_discrete_map={"W": "#3fb950", "L": "#f85149"},
                    template="plotly_dark")
                fig3.update_layout(height=280, margin=dict(l=0,r=0,t=0,b=0),
                    paper_bgcolor=DARK, plot_bgcolor=DARK, showlegend=False)
                st.plotly_chart(fig3, use_container_width=True)

    # ── Tab 2: Monthly Returns Heatmap ────────────────────────────────────────
    with tabs[1]:
        st.subheader("Monthly Returns Heatmap")
        closed["month"] = closed["exit_time"].dt.to_period("M")
        monthly = closed.groupby("month")["pnl"].sum().reset_index()
        monthly["year"]  = monthly["month"].dt.year
        monthly["month_num"] = monthly["month"].dt.month
        if not monthly.empty:
            pivot = monthly.pivot(index="year", columns="month_num", values="pnl").fillna(0)
            month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            pivot.columns = [month_names[m-1] for m in pivot.columns]
            fig4 = px.imshow(pivot, color_continuous_scale=["#f85149","#161b22","#3fb950"],
                color_continuous_midpoint=0, text_auto=".0f", template="plotly_dark",
                title="Monthly PnL ($)")
            fig4.update_layout(height=300, margin=dict(l=0,r=0,t=40,b=0),
                paper_bgcolor=DARK, coloraxis_colorbar=dict(title="PnL $"))
            st.plotly_chart(fig4, use_container_width=True)

        # Day-of-week performance
        st.subheader("Performance by Day of Week")
        closed["dow"] = closed["entry_time"].dt.day_name()
        dow_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        dow_perf  = closed.groupby("dow").agg(
            avg_pnl=("pnl","mean"), total_trades=("pnl","count"), win_rate=("pnl", lambda x: (x>0).mean()*100)
        ).reindex([d for d in dow_order if d in closed["dow"].unique()]).reset_index()
        fig5 = make_subplots(specs=[[{"secondary_y": True}]])
        fig5.add_trace(go.Bar(x=dow_perf["dow"], y=dow_perf["avg_pnl"], name="Avg PnL",
            marker_color=["#3fb950" if v >= 0 else "#f85149" for v in dow_perf["avg_pnl"]]))
        fig5.add_trace(go.Scatter(x=dow_perf["dow"], y=dow_perf["win_rate"], name="Win Rate %",
            line=dict(color="#e3b341", width=2), mode="lines+markers"), secondary_y=True)
        fig5.update_layout(template="plotly_dark", height=280,
            margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor=DARK, plot_bgcolor=DARK)
        st.plotly_chart(fig5, use_container_width=True)

    # ── Tab 3: Trade Analysis ─────────────────────────────────────────────────
    with tabs[2]:
        col_a, col_b, col_c = st.columns(3)
        avg_win  = closed[closed["pnl"] > 0]["pnl"].mean() if len(closed[closed["pnl"] > 0]) else 0
        avg_loss = closed[closed["pnl"] <= 0]["pnl"].mean() if len(closed[closed["pnl"] <= 0]) else 0
        rr_actual = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        col_a.metric("Avg Win",       f"${avg_win:.2f}")
        col_b.metric("Avg Loss",      f"${avg_loss:.2f}")
        col_c.metric("Actual R:R",    f"{rr_actual:.2f}")

        st.subheader("PnL per Trade")
        fig6 = go.Figure()
        fig6.add_trace(go.Bar(
            x=list(range(len(closed))), y=closed["pnl"],
            marker_color=["#3fb950" if p > 0 else "#f85149" for p in closed["pnl"]],
            name="Trade PnL",
        ))
        fig6.update_layout(template="plotly_dark", height=280,
            margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor=DARK, plot_bgcolor=DARK,
            xaxis_title="Trade #", yaxis_title="PnL ($)")
        fig6.update_xaxes(gridcolor=GRID)
        fig6.update_yaxes(gridcolor=GRID)
        st.plotly_chart(fig6, use_container_width=True)

        # Long vs Short breakdown
        st.subheader("Long vs Short Performance")
        side_perf = closed.groupby("side").agg(
            total_pnl=("pnl","sum"), avg_pnl=("pnl","mean"),
            trades=("pnl","count"), win_rate=("pnl", lambda x: (x>0).mean()*100)
        ).reset_index()
        st.dataframe(side_perf.style.format({
            "total_pnl": "${:.2f}", "avg_pnl": "${:.2f}",
            "win_rate": "{:.1f}%"
        }), use_container_width=True)

    # ── Tab 4: Run History ────────────────────────────────────────────────────
    with tabs[3]:
        _show_backtest_charts(bt_res)


def _show_backtest_charts(bt_res):
    st.subheader("All Backtest Runs")
    bt_plot = bt_res.sort_values("run_time")
    colors  = ["#3fb950" if r >= 0 else "#f85149" for r in bt_plot["total_return"]]
    fig = go.Figure(go.Bar(
        x=bt_plot["run_time"].str[:16],
        y=bt_plot["total_return"] * 100,
        marker_color=colors,
        text=[f"{v*100:.1f}%" for v in bt_plot["total_return"]],
        textposition="outside",
    ))
    fig.update_layout(template="plotly_dark", height=320,
        margin=dict(l=0,r=0,t=0,b=0), yaxis_title="Return %",
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117")
    st.plotly_chart(fig, use_container_width=True)

    show_cols = ["run_time","symbol","timeframe","start_date","end_date",
                 "total_return","sharpe_ratio","max_drawdown","win_rate","total_trades"]
    st.dataframe(bt_res[[c for c in show_cols if c in bt_res.columns]], use_container_width=True)
