"""dashboard/pages/backtest_page.py — Run backtests directly from the dashboard."""

import sys
import json
import subprocess
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import streamlit as st

from database.db_manager import DBManager
from utils.config_loader import load_config

DARK = "#0d1117"
GRID = "#21262d"


@st.cache_data(ttl=10)
def load_results():
    cfg = load_config()
    db  = DBManager(cfg["data"]["db_path"])
    return db.get_backtest_results(limit=50)


def render_backtest_page():
    st.title("🔬 Backtest Runner")
    cfg = load_config()

    tabs = st.tabs(["▶ Run Backtest", "📊 Results History", "⚖️ Compare Runs"])

    # ── Tab 1: Run ────────────────────────────────────────────────────────────
    with tabs[0]:
        st.subheader("Configure & Run")

        col1, col2 = st.columns(2)
        with col1:
            symbol    = st.selectbox("Symbol",    cfg["trading"]["symbols"])
            timeframe = st.selectbox("Timeframe", ["4h", "1h", "1d", "15m"])
            strategy  = st.selectbox("Strategy",  ["confluence", "bb"],
                            format_func=lambda x: "Confluence (Multi-factor)" if x == "confluence" else "BB Breakout (Original)")
        with col2:
            start = st.date_input("Start Date", value=date(2023, 1, 1))
            end   = st.date_input("End Date",   value=date(2024, 12, 31))
            initial_cash = st.number_input("Initial Cash ($)", value=10000, step=1000)

        # Advanced params expander
        with st.expander("⚙️ Advanced Parameters"):
            ac1, ac2, ac3 = st.columns(3)
            bb_period      = ac1.number_input("BB Period",         value=cfg["strategy"]["bb_period"],         step=1)
            bb_std         = ac1.number_input("BB Std Dev",        value=cfg["strategy"]["bb_std_dev"],        step=0.1)
            atr_period     = ac2.number_input("ATR Period",        value=cfg["strategy"]["atr_period"],        step=1)
            sl_mult        = ac2.number_input("Stop Loss ATR Mult",value=cfg["risk"]["stop_loss_atr_mult"],    step=0.1)
            tp_rr          = ac3.number_input("Take Profit R:R",   value=cfg["risk"]["take_profit_rr"],        step=0.1)
            min_score      = ac3.number_input("Min Score (confluence)", value=cfg["strategy"].get("min_score", 2), step=1, min_value=1, max_value=3)
            vol_confirm    = ac1.checkbox("Volume Confirmation", value=cfg["strategy"]["volume_confirm"])
            trailing_stop  = ac2.checkbox("Trailing Stop",       value=cfg["risk"]["trailing_stop"])

        # Download data option
        auto_download = st.checkbox("Auto-download missing data from Binance", value=True)

        run_btn = st.button("▶ Run Backtest", type="primary", use_container_width=True)

        if run_btn:
            if start >= end:
                st.error("Start date must be before end date.")
            else:
                # Build command
                cmd = [
                    sys.executable,
                    str(ROOT / "scripts" / "run_backtest.py"),
                    "--symbol",    symbol,
                    "--timeframe", timeframe,
                    "--start",     str(start),
                    "--end",       str(end),
                    "--strategy",  strategy,
                ]

                # Write temp config overrides to a patch approach via env or inline
                # We patch config temporarily by writing override env vars
                import os
                env = os.environ.copy()

                log_placeholder = st.empty()
                result_placeholder = st.empty()

                with st.spinner(f"Running {strategy} backtest on {symbol}/{timeframe}..."):
                    process = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, cwd=str(ROOT), env=env,
                        bufsize=1, universal_newlines=True,
                    )

                    log_lines = []
                    log_box   = st.empty()

                    for line in process.stdout:
                        line = line.rstrip()
                        if line:
                            log_lines.append(line)
                            # Show last 20 lines in a scrollable box
                            log_box.code("\n".join(log_lines[-20:]), language="bash")

                    process.wait()

                if process.returncode == 0:
                    st.success("✅ Backtest complete!")
                    st.cache_data.clear()

                    # Parse results from last lines
                    result_lines = [l for l in log_lines if "──" in l or any(
                        k in l for k in ["return","sharpe","drawdown","trades","win_rate","final_value"]
                    )]

                    # Load fresh from DB
                    db  = DBManager(cfg["data"]["db_path"])
                    res = db.get_backtest_results(limit=1)
                    if not res.empty:
                        r = res.iloc[0]
                        st.divider()
                        st.subheader("Results")
                        m1, m2, m3, m4, m5 = st.columns(5)
                        ret = r["total_return"] * 100
                        m1.metric("Total Return",  f"{ret:.2f}%",    delta=f"{ret:+.2f}%",
                                  delta_color="normal" if ret >= 0 else "inverse")
                        m2.metric("Sharpe Ratio",  f"{r['sharpe_ratio']:.2f}" if r['sharpe_ratio'] else "—")
                        m3.metric("Max Drawdown",  f"{(r['max_drawdown'] or 0)*100:.2f}%")
                        m4.metric("Win Rate",      f"{(r['win_rate'] or 0)*100:.1f}%")
                        m5.metric("Total Trades",  int(r["total_trades"] or 0))

                        # Quick equity proxy chart from trade log
                        _show_quick_chart(r)
                else:
                    st.error("❌ Backtest failed. See log above.")

    # ── Tab 2: Results History ─────────────────────────────────────────────────
    with tabs[1]:
        st.subheader("All Backtest Runs")
        df = load_results()

        if df.empty:
            st.info("No backtest results yet.")
        else:
            # Summary cards for best run
            best = df.loc[df["total_return"].idxmax()]
            b1, b2, b3 = st.columns(3)
            b1.metric("Best Return",  f"{best['total_return']*100:.2f}%")
            b2.metric("Best Sharpe",  f"{df['sharpe_ratio'].max():.2f}")
            b3.metric("Lowest DD",    f"{df['max_drawdown'].min()*100:.2f}%")

            st.divider()

            # Returns bar chart
            df_plot  = df.sort_values("run_time").tail(20)
            colors   = ["#3fb950" if r >= 0 else "#f85149" for r in df_plot["total_return"]]
            fig = go.Figure(go.Bar(
                x=df_plot["run_time"].str[:16],
                y=df_plot["total_return"] * 100,
                marker_color=colors,
                text=[f"{v*100:.1f}%" for v in df_plot["total_return"]],
                textposition="outside",
                customdata=df_plot[["symbol","timeframe","total_trades"]].values,
                hovertemplate="<b>%{x}</b><br>Return: %{y:.1f}%<br>Symbol: %{customdata[0]}/%{customdata[1]}<br>Trades: %{customdata[2]}<extra></extra>",
            ))
            fig.update_layout(template="plotly_dark", height=320,
                margin=dict(l=0,r=0,t=0,b=60),
                yaxis_title="Return %",
                xaxis_tickangle=-30,
                paper_bgcolor=DARK, plot_bgcolor=DARK)
            fig.update_xaxes(gridcolor=GRID)
            fig.update_yaxes(gridcolor=GRID)
            st.plotly_chart(fig, use_container_width=True)

            # Full table
            show_cols = ["run_time","symbol","timeframe","start_date","end_date",
                         "initial_cash","final_value","total_return",
                         "sharpe_ratio","max_drawdown","win_rate","total_trades"]
            show_cols = [c for c in show_cols if c in df.columns]
            df_show = df[show_cols].copy()
            df_show["total_return"] = (df_show["total_return"] * 100).round(2).astype(str) + "%"
            df_show["win_rate"]     = (df_show["win_rate"] * 100).round(1).astype(str) + "%"
            df_show["max_drawdown"] = (df_show["max_drawdown"] * 100).round(2).astype(str) + "%"
            st.dataframe(df_show, use_container_width=True, height=400)

            csv = df[show_cols].to_csv(index=False).encode()
            st.download_button("⬇️ Export Results CSV", csv, "backtest_results.csv", "text/csv")

    # ── Tab 3: Compare Runs ────────────────────────────────────────────────────
    with tabs[2]:
        st.subheader("Compare Two Runs")
        df = load_results()
        if df.empty or len(df) < 2:
            st.info("Run at least 2 backtests to compare.")
        else:
            df["label"] = df["run_time"].str[:16] + "  " + df["symbol"] + "/" + df["timeframe"]
            labels = df["label"].tolist()

            cc1, cc2 = st.columns(2)
            sel_a = cc1.selectbox("Run A", labels, index=0)
            sel_b = cc2.selectbox("Run B", labels, index=min(1, len(labels)-1))

            ra = df[df["label"] == sel_a].iloc[0]
            rb = df[df["label"] == sel_b].iloc[0]

            metrics = [
                ("Total Return %",  ra["total_return"]*100,  rb["total_return"]*100,  True),
                ("Sharpe Ratio",    ra["sharpe_ratio"] or 0, rb["sharpe_ratio"] or 0,  True),
                ("Max Drawdown %",  (ra["max_drawdown"] or 0)*100, (rb["max_drawdown"] or 0)*100, False),
                ("Win Rate %",      (ra["win_rate"] or 0)*100, (rb["win_rate"] or 0)*100, True),
                ("Total Trades",    ra["total_trades"] or 0, rb["total_trades"] or 0,   None),
                ("Final Value $",   ra["final_value"],        rb["final_value"],          True),
            ]

            col_a, col_b = st.columns(2)
            col_a.markdown(f"### 🅐 `{sel_a}`")
            col_b.markdown(f"### 🅑 `{sel_b}`")

            for label, va, vb, higher_is_better in metrics:
                ca, cb = st.columns(2)
                if higher_is_better is True:
                    a_better = va > vb
                elif higher_is_better is False:
                    a_better = va < vb
                else:
                    a_better = None

                a_icon = "🟢" if a_better is True  else ("🔴" if a_better is False else "⚪")
                b_icon = "🟢" if a_better is False else ("🔴" if a_better is True  else "⚪")

                ca.metric(f"{a_icon} {label}", f"{va:.2f}")
                cb.metric(f"{b_icon} {label}", f"{vb:.2f}", delta=f"{vb-va:+.2f}")

            st.divider()

            # Radar chart
            cats   = ["Return", "Sharpe", "Win Rate", "Low DD", "Trades"]
            norm_a = [
                min(ra["total_return"]*100 / 100, 1),
                min((ra["sharpe_ratio"] or 0) / 5, 1),
                (ra["win_rate"] or 0),
                1 - min((ra["max_drawdown"] or 0), 1),
                min((ra["total_trades"] or 0) / 100, 1),
            ]
            norm_b = [
                min(rb["total_return"]*100 / 100, 1),
                min((rb["sharpe_ratio"] or 0) / 5, 1),
                (rb["win_rate"] or 0),
                1 - min((rb["max_drawdown"] or 0), 1),
                min((rb["total_trades"] or 0) / 100, 1),
            ]

            fig_r = go.Figure()
            fig_r.add_trace(go.Scatterpolar(r=norm_a + [norm_a[0]], theta=cats + [cats[0]],
                fill="toself", name="Run A", line=dict(color="#58a6ff"), fillcolor="rgba(88,166,255,0.15)"))
            fig_r.add_trace(go.Scatterpolar(r=norm_b + [norm_b[0]], theta=cats + [cats[0]],
                fill="toself", name="Run B", line=dict(color="#3fb950"), fillcolor="rgba(63,185,80,0.15)"))
            fig_r.update_layout(
                polar=dict(
                    bgcolor="#161b22",
                    radialaxis=dict(visible=True, range=[0,1], gridcolor=GRID, linecolor=GRID),
                    angularaxis=dict(gridcolor=GRID, linecolor=GRID),
                ),
                template="plotly_dark", height=400,
                margin=dict(l=40,r=40,t=40,b=40),
                paper_bgcolor=DARK,
                showlegend=True,
            )
            st.plotly_chart(fig_r, use_container_width=True)


def _show_quick_chart(r):
    """Show a simple final value vs initial cash bar after a run."""
    fig = go.Figure(go.Bar(
        x=["Initial Cash", "Final Value"],
        y=[r["initial_cash"], r["final_value"]],
        marker_color=["#8b949e", "#3fb950" if r["final_value"] >= r["initial_cash"] else "#f85149"],
        text=[f"${r['initial_cash']:,.0f}", f"${r['final_value']:,.0f}"],
        textposition="outside",
    ))
    fig.update_layout(template="plotly_dark", height=280,
        margin=dict(l=0,r=0,t=0,b=0),
        paper_bgcolor=DARK, plot_bgcolor=DARK,
        yaxis_title="Value ($)",
        showlegend=False)
    fig.update_xaxes(gridcolor=GRID)
    fig.update_yaxes(gridcolor=GRID)
    st.plotly_chart(fig, use_container_width=True)
