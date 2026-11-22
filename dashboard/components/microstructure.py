"""dashboard/pages/microstructure.py — Volume, liquidity, market imbalance."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from database.db_manager import DBManager
from utils.config_loader import load_config

DARK = "#0d1117"
GRID = "#21262d"


@st.cache_data(ttl=60)
def load_ohlcv(symbol, timeframe, limit):
    cfg = load_config()
    db  = DBManager(cfg["data"]["db_path"])
    df  = db.get_ohlcv(symbol, timeframe, limit=limit)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

    # Volume delta (proxy for buy/sell imbalance)
    # Up-candle volume → buy pressure, down-candle → sell pressure
    df["is_up"]       = df["close"] >= df["open"]
    df["buy_vol"]     = df["volume"] * df["is_up"]
    df["sell_vol"]    = df["volume"] * (~df["is_up"])
    df["vol_delta"]   = df["buy_vol"] - df["sell_vol"]
    df["cum_delta"]   = df["vol_delta"].cumsum()

    # Volume MA and ratio
    df["vol_ma20"]    = df["volume"].rolling(20).mean()
    df["vol_ratio"]   = df["volume"] / df["vol_ma20"]

    # Volume profile bins (price levels)
    df["vol_ma5"]     = df["volume"].rolling(5).mean()

    # Liquidity proxy: high-low range (tighter = more liquid)
    df["hl_range"]    = (df["high"] - df["low"]) / df["close"] * 100
    df["hl_ma"]       = df["hl_range"].rolling(20).mean()

    # Market imbalance score (-1 to +1)
    df["imbalance"]   = df["vol_delta"] / df["volume"].replace(0, np.nan)
    df["imb_ma"]      = df["imbalance"].rolling(10).mean()

    return df


def render():
    st.title("🌊 Market Microstructure")
    cfg = load_config()

    c1, c2, c3 = st.columns([2, 2, 2])
    symbol    = c1.selectbox("Symbol",    cfg["trading"]["symbols"])
    timeframe = c2.selectbox("Timeframe", ["4h", "1h", "1d", "15m"])
    candles   = c3.slider("Candles", 50, 500, 200, 50)

    df = load_ohlcv(symbol, timeframe, candles)
    if df.empty:
        st.warning(f"No data for {symbol}/{timeframe}.")
        return

    last = df.iloc[-1]

    # ── KPIs ──────────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Vol Ratio",     f"{last['vol_ratio']:.2f}x",
              help="Current volume vs 20-bar average. >1.5 = spike")
    k2.metric("Cum Delta",     f"{last['cum_delta']:,.0f}",
              help="Cumulative buy minus sell volume")
    k3.metric("Imbalance",     f"{last['imbalance']:.2f}",
              help="+1 = pure buy candle, -1 = pure sell candle")
    k4.metric("HL Range %",    f"{last['hl_range']:.2f}%",
              help="High-low spread as % of price. Lower = tighter/more liquid")

    st.divider()

    # ── Chart 1: Price + Volume Delta ─────────────────────────────────────────
    st.subheader("Volume Delta & Cumulative Delta")
    fig1 = make_subplots(rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.40, 0.30, 0.30], vertical_spacing=0.03,
        subplot_titles=("Price", "Volume Delta (Buy - Sell)", "Cumulative Delta"))

    fig1.add_trace(go.Candlestick(
        x=df["dt"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name=symbol,
        increasing_line_color="#3fb950", decreasing_line_color="#f85149",
    ), row=1, col=1)

    delta_colors = ["#3fb950" if v >= 0 else "#f85149" for v in df["vol_delta"]]
    fig1.add_trace(go.Bar(x=df["dt"], y=df["vol_delta"], name="Vol Delta",
        marker_color=delta_colors, opacity=0.8), row=2, col=1)
    fig1.add_hline(y=0, line_color=GRID, row=2, col=1)

    cum_color = "#3fb950" if last["cum_delta"] >= 0 else "#f85149"
    fig1.add_trace(go.Scatter(x=df["dt"], y=df["cum_delta"], name="Cum Delta",
        line=dict(color=cum_color, width=2),
        fill="tozeroy", fillcolor=f"rgba({'63,185,80' if last['cum_delta'] >= 0 else '248,81,73'},0.1)"),
        row=3, col=1)
    fig1.add_hline(y=0, line_color=GRID, row=3, col=1)

    fig1.update_layout(template="plotly_dark", height=580,
        margin=dict(l=0,r=0,t=30,b=0),
        xaxis_rangeslider_visible=False,
        paper_bgcolor=DARK, plot_bgcolor=DARK)
    fig1.update_xaxes(gridcolor=GRID)
    fig1.update_yaxes(gridcolor=GRID)
    st.plotly_chart(fig1, use_container_width=True)

    # ── Chart 2: Volume Profile + Imbalance ───────────────────────────────────
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Volume Profile (Price Levels)")
        price_min = df["low"].min()
        price_max = df["high"].max()
        n_bins    = 30
        bins      = np.linspace(price_min, price_max, n_bins + 1)
        labels    = [(bins[i] + bins[i+1]) / 2 for i in range(n_bins)]
        buy_profile  = np.zeros(n_bins)
        sell_profile = np.zeros(n_bins)

        for _, row in df.iterrows():
            mid = (row["high"] + row["low"]) / 2
            idx = min(int((mid - price_min) / (price_max - price_min) * n_bins), n_bins - 1)
            buy_profile[idx]  += row["buy_vol"]
            sell_profile[idx] += row["sell_vol"]

        fig2 = go.Figure()
        fig2.add_trace(go.Bar(y=labels, x=buy_profile, orientation="h",
            name="Buy Vol", marker_color="#3fb950", opacity=0.7))
        fig2.add_trace(go.Bar(y=labels, x=-sell_profile, orientation="h",
            name="Sell Vol", marker_color="#f85149", opacity=0.7))
        fig2.add_hline(y=float(last["close"]), line_color="#e3b341",
            line_dash="dash", annotation_text="Price")
        fig2.update_layout(barmode="overlay", template="plotly_dark", height=400,
            margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor=DARK, plot_bgcolor=DARK,
            xaxis_title="Volume", yaxis_title="Price")
        fig2.update_xaxes(gridcolor=GRID)
        fig2.update_yaxes(gridcolor=GRID)
        st.plotly_chart(fig2, use_container_width=True)

    with col_b:
        st.subheader("Market Imbalance & Liquidity")
        fig3 = make_subplots(rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.5, 0.5], vertical_spacing=0.05,
            subplot_titles=("Imbalance (MA10)", "HL Range % (Liquidity)"))

        imb_colors = ["#3fb950" if v >= 0 else "#f85149" for v in df["imb_ma"].fillna(0)]
        fig3.add_trace(go.Bar(x=df["dt"], y=df["imb_ma"], name="Imbalance MA",
            marker_color=imb_colors, opacity=0.8), row=1, col=1)
        fig3.add_hline(y=0, line_color=GRID, row=1, col=1)

        fig3.add_trace(go.Scatter(x=df["dt"], y=df["hl_range"], name="HL Range",
            line=dict(color="#58a6ff", width=1.5),
            fill="tozeroy", fillcolor="rgba(88,166,255,0.08)"), row=2, col=1)
        fig3.add_trace(go.Scatter(x=df["dt"], y=df["hl_ma"], name="HL Range MA",
            line=dict(color="#e3b341", width=1.5, dash="dot")), row=2, col=1)

        fig3.update_layout(template="plotly_dark", height=400,
            margin=dict(l=0,r=0,t=30,b=0),
            paper_bgcolor=DARK, plot_bgcolor=DARK)
        fig3.update_xaxes(gridcolor=GRID)
        fig3.update_yaxes(gridcolor=GRID)
        st.plotly_chart(fig3, use_container_width=True)

    # ── Volume Ratio Heatmap ──────────────────────────────────────────────────
    st.subheader("Volume Spike History")
    fig4 = go.Figure()
    spike_color = ["#f85149" if r > 2 else "#e3b341" if r > 1.5 else "#3fb950" if r > 1 else "#8b949e"
                   for r in df["vol_ratio"].fillna(1)]
    fig4.add_trace(go.Bar(x=df["dt"], y=df["vol_ratio"], name="Vol Ratio",
        marker_color=spike_color))
    fig4.add_hline(y=1.5, line_color="#e3b341", line_dash="dot",
        annotation_text="1.5× (signal threshold)")
    fig4.add_hline(y=2.0, line_color="#f85149", line_dash="dot",
        annotation_text="2× (strong spike)")
    fig4.update_layout(template="plotly_dark", height=220,
        margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor=DARK, plot_bgcolor=DARK,
        yaxis_title="Volume Ratio vs MA20")
    fig4.update_xaxes(gridcolor=GRID)
    fig4.update_yaxes(gridcolor=GRID)
    st.plotly_chart(fig4, use_container_width=True)
