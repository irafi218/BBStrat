"""dashboard/pages/overview.py — Main KPI dashboard page with BTC candlestick chart."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from database.db_manager import DBManager
from utils.config_loader import load_config


def render_overview():
    st.title("📊 Portfolio Overview")

    cfg = load_config()
    db  = DBManager(cfg["data"]["db_path"])

    trades = db.get_trades(limit=1000)
    equity = db.get_equity_curve(limit=2000)
    closed = trades[trades["status"] == "CLOSED"] if not trades.empty else pd.DataFrame()

    # ── KPI row ───────────────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    total_pnl = closed["pnl"].sum() if not closed.empty else 0
    n_trades  = len(closed)
    win_rate  = (closed["pnl"] > 0).mean() * 100 if n_trades else 0
    open_count = (trades["status"] == "OPEN").sum() if not trades.empty else 0
    avg_pnl   = closed["pnl_pct"].mean() * 100 if not closed.empty else 0

    col1.metric("Total PnL",    f"${total_pnl:,.2f}")
    col2.metric("Closed Trades", n_trades)
    col3.metric("Win Rate",     f"{win_rate:.1f}%")
    col4.metric("Open Trades",  open_count)
    col5.metric("Avg PnL %",    f"{avg_pnl:.2f}%")

    st.divider()

    # ── Symbol + timeframe selector ───────────────────────────────────────────
    c1, c2, c3 = st.columns([2, 2, 2])
    symbol    = c1.selectbox("Symbol",    cfg["trading"]["symbols"], key="chart_symbol")
    timeframe = c2.selectbox("Timeframe", ["4h", "1h", "1d", "15m"],  key="chart_tf")
    candles   = c3.number_input("Candles", min_value=50, max_value=1000, value=300, step=50)

    # ── Load OHLCV ────────────────────────────────────────────────────────────
    df = db.get_ohlcv(symbol, timeframe, limit=int(candles))

    if df.empty:
        st.warning(f"No data for {symbol}/{timeframe}. Run: `python scripts/download_data.py --symbol {symbol} --timeframe {timeframe} --start 2023-01-01`")
        return

    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_period = cfg["strategy"]["bb_period"]
    bb_std    = cfg["strategy"]["bb_std_dev"]
    df["bb_mid"] = df["close"].rolling(bb_period).mean()
    df["bb_std"] = df["close"].rolling(bb_period).std()
    df["bb_top"] = df["bb_mid"] + bb_std * df["bb_std"]
    df["bb_bot"] = df["bb_mid"] - bb_std * df["bb_std"]

    # ── Build chart ───────────────────────────────────────────────────────────
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.03,
    )

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=df["dt"],
        open=df["open"], high=df["high"],
        low=df["low"],   close=df["close"],
        name=symbol,
        increasing_line_color="#3fb950",
        decreasing_line_color="#f85149",
    ), row=1, col=1)

    # Bollinger Bands
    fig.add_trace(go.Scatter(
        x=df["dt"], y=df["bb_top"],
        name="BB Upper", line=dict(color="rgba(88,166,255,0.8)", width=1, dash="dot"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df["dt"], y=df["bb_mid"],
        name="BB Mid", line=dict(color="rgba(88,166,255,0.4)", width=1),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df["dt"], y=df["bb_bot"],
        name="BB Lower", line=dict(color="rgba(88,166,255,0.8)", width=1, dash="dot"),
        fill="tonexty", fillcolor="rgba(88,166,255,0.04)",
    ), row=1, col=1)

    # Trade markers from DB
    if not closed.empty:
        sym_trades = closed[closed["symbol"] == symbol].copy()
        if not sym_trades.empty:
            sym_trades["entry_dt"] = pd.to_datetime(sym_trades["entry_time"])
            sym_trades["exit_dt"]  = pd.to_datetime(sym_trades["exit_time"])

            wins  = sym_trades[sym_trades["pnl"] > 0]
            losses = sym_trades[sym_trades["pnl"] <= 0]

            if not wins.empty:
                fig.add_trace(go.Scatter(
                    x=wins["entry_dt"], y=wins["entry_price"],
                    mode="markers", name="Win Entry",
                    marker=dict(symbol="triangle-up", size=10, color="#3fb950"),
                ), row=1, col=1)
            if not losses.empty:
                fig.add_trace(go.Scatter(
                    x=losses["entry_dt"], y=losses["entry_price"],
                    mode="markers", name="Loss Entry",
                    marker=dict(symbol="triangle-up", size=10, color="#f85149"),
                ), row=1, col=1)

    # Volume bars
    colors = ["#3fb950" if c >= o else "#f85149"
              for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df["dt"], y=df["volume"],
        name="Volume", marker_color=colors, opacity=0.6,
    ), row=2, col=1)

    fig.update_layout(
        template="plotly_dark",
        height=620,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.02, x=0),
        xaxis2_title=None,
    )
    fig.update_yaxes(title_text="Price (USDT)", row=1, col=1)
    fig.update_yaxes(title_text="Volume",       row=2, col=1)

    st.plotly_chart(fig, use_container_width=True)

    # ── Equity curve ─────────────────────────────────────────────────────────
    if not equity.empty:
        st.subheader("Equity Curve")
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=equity["snapshot_time"], y=equity["portfolio_value"],
            mode="lines", name="Portfolio",
            line=dict(color="#58a6ff", width=2),
            fill="tozeroy", fillcolor="rgba(88,166,255,0.08)",
        ))
        fig2.update_layout(
            template="plotly_dark", height=250,
            margin=dict(l=0, r=0, t=0, b=0),
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ── PnL distribution ─────────────────────────────────────────────────────
    if not closed.empty:
        import plotly.express as px
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("PnL Distribution")
            fig3 = px.histogram(closed, x="pnl", nbins=30,
                                color_discrete_sequence=["#58a6ff"], template="plotly_dark")
            fig3.update_layout(height=250, margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig3, use_container_width=True)
        with col_b:
            st.subheader("Trades by Symbol")
            by_sym = closed.groupby("symbol")["pnl"].sum().reset_index()
            fig4 = px.bar(by_sym, x="symbol", y="pnl",
                          color="pnl", color_continuous_scale=["#f85149", "#3fb950"],
                          template="plotly_dark")
            fig4.update_layout(height=250, margin=dict(l=0, r=0, t=0, b=0), showlegend=False)
            st.plotly_chart(fig4, use_container_width=True)
