"""dashboard/pages/market_overview.py — Live price, BB signals, volume."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from database.db_manager import DBManager
from broker.binance_client import BinanceClient
from utils.config_loader import load_config
from utils.fear_greed import fetch_fear_greed, get_fg_label, get_fg_color


@st.cache_data(ttl=60)
def get_ohlcv(symbol, timeframe, limit):
    cfg = load_config()
    db  = DBManager(cfg["data"]["db_path"])
    df  = db.get_ohlcv(symbol, timeframe, limit=limit)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    bb_period = cfg["strategy"]["bb_period"]
    bb_std    = cfg["strategy"]["bb_std_dev"]
    df["bb_mid"] = df["close"].rolling(bb_period).mean()
    df["bb_std"] = df["close"].rolling(bb_period).std()
    df["bb_top"] = df["bb_mid"] + bb_std * df["bb_std"]
    df["bb_bot"] = df["bb_mid"] - bb_std * df["bb_std"]
    df["bb_width"] = (df["bb_top"] - df["bb_bot"]) / df["bb_mid"]
    df["atr"] = df["high"].combine(df["close"].shift(), max) - df["low"].combine(df["close"].shift(), min)
    df["atr"] = df["atr"].rolling(14).mean()
    df["vol_ma"] = df["volume"].rolling(20).mean()
    return df

@st.cache_data(ttl=30)
def get_live_price(symbol):
    cfg    = load_config()
    client = BinanceClient(testnet=cfg["exchange"]["testnet"])
    ticker = client.get_ticker(symbol)
    return float(ticker["price"]) if ticker else None

@st.cache_data(ttl=300)
def get_fg():
    return fetch_fear_greed(limit=30)


def render():
    st.title("📊 Market Overview")
    cfg = load_config()

    # ── Controls ──────────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns([2, 2, 2])
    symbol    = c1.selectbox("Symbol",    cfg["trading"]["symbols"])
    timeframe = c2.selectbox("Timeframe", ["4h", "1h", "1d", "15m"])
    candles   = c3.slider("Candles", 50, 500, 200, 50)

    df = get_ohlcv(symbol, timeframe, candles)
    if df.empty:
        st.warning(f"No data for {symbol}/{timeframe}. Download data first.")
        return

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # ── Live price KPIs ───────────────────────────────────────────────────────
    live_price = get_live_price(symbol) or last["close"]
    pct_change = (last["close"] - prev["close"]) / prev["close"] * 100
    bb_pos     = (last["close"] - last["bb_bot"]) / (last["bb_top"] - last["bb_bot"]) * 100
    fg_series  = get_fg()
    fg_val     = float(fg_series.iloc[-1]) if not fg_series.empty else 50

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Price",        f"${live_price:,.2f}", f"{pct_change:+.2f}%")
    k2.metric("BB Upper",     f"${last['bb_top']:,.2f}")
    k3.metric("BB Lower",     f"${last['bb_bot']:,.2f}")
    k4.metric("BB Position",  f"{bb_pos:.1f}%",    help="0%=at lower band, 100%=at upper band")
    k5.metric("Fear & Greed", f"{fg_val:.0f}",     get_fg_label(fg_val))

    # ── Signal badge ──────────────────────────────────────────────────────────
    if last["close"] > last["bb_top"]:
        st.markdown("🟢 **SIGNAL: LONG BREAKOUT** — Price above upper BB", unsafe_allow_html=True)
    elif last["close"] < last["bb_bot"]:
        st.markdown("🔴 **SIGNAL: SHORT BREAKOUT** — Price below lower BB", unsafe_allow_html=True)
    elif bb_pos > 80:
        st.markdown("⚠️ **WATCH: Approaching upper band**", unsafe_allow_html=True)
    elif bb_pos < 20:
        st.markdown("⚠️ **WATCH: Approaching lower band**", unsafe_allow_html=True)
    else:
        st.markdown("⬜ **NEUTRAL** — Price within bands", unsafe_allow_html=True)

    st.divider()

    # ── Main chart: Candles + BB + Volume ─────────────────────────────────────
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.60, 0.20, 0.20],
        vertical_spacing=0.02,
        subplot_titles=("Price + Bollinger Bands", "Volume", "BB Width (Squeeze)"),
    )

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=df["dt"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name=symbol,
        increasing_line_color="#3fb950", decreasing_line_color="#f85149",
        increasing_fillcolor="#3fb950", decreasing_fillcolor="#f85149",
    ), row=1, col=1)

    # BB bands with fill
    fig.add_trace(go.Scatter(x=df["dt"], y=df["bb_top"], name="BB Upper",
        line=dict(color="rgba(88,166,255,0.9)", width=1.5, dash="dot"), showlegend=True), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["dt"], y=df["bb_mid"], name="BB Mid",
        line=dict(color="rgba(88,166,255,0.5)", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["dt"], y=df["bb_bot"], name="BB Lower",
        line=dict(color="rgba(88,166,255,0.9)", width=1.5, dash="dot"),
        fill="tonexty", fillcolor="rgba(88,166,255,0.05)"), row=1, col=1)

    # Volume bars coloured by candle direction
    vol_colors = ["#3fb950" if c >= o else "#f85149" for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(x=df["dt"], y=df["volume"], name="Volume",
        marker_color=vol_colors, opacity=0.7), row=2, col=1)
    fig.add_trace(go.Scatter(x=df["dt"], y=df["vol_ma"], name="Vol MA",
        line=dict(color="#e3b341", width=1.5)), row=2, col=1)

    # BB Width (squeeze detection)
    fig.add_trace(go.Scatter(x=df["dt"], y=df["bb_width"], name="BB Width",
        line=dict(color="#bc8cff", width=1.5), fill="tozeroy",
        fillcolor="rgba(188,140,255,0.1)"), row=3, col=1)
    squeeze_line = cfg["strategy"].get("squeeze_threshold", 0.005)
    fig.add_hline(y=squeeze_line, line_dash="dot", line_color="#f85149",
                  annotation_text="Squeeze", row=3, col=1)

    fig.update_layout(
        template="plotly_dark", height=680,
        margin=dict(l=0, r=0, t=30, b=0),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.03, x=0, font=dict(size=11)),
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
    )
    fig.update_xaxes(gridcolor="#21262d", zeroline=False)
    fig.update_yaxes(gridcolor="#21262d", zeroline=False)
    st.plotly_chart(fig, use_container_width=True)

    # ── ATR regime ────────────────────────────────────────────────────────────
    st.subheader("Volatility Regime (ATR)")
    fig2 = go.Figure()
    atr_ma = df["atr"].rolling(20).mean()
    fig2.add_trace(go.Scatter(x=df["dt"], y=df["atr"], name="ATR",
        line=dict(color="#58a6ff", width=1.5)))
    fig2.add_trace(go.Scatter(x=df["dt"], y=atr_ma, name="ATR MA",
        line=dict(color="#e3b341", width=1.5, dash="dot")))
    fig2.update_layout(template="plotly_dark", height=200,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117")
    fig2.update_xaxes(gridcolor="#21262d")
    fig2.update_yaxes(gridcolor="#21262d")
    st.plotly_chart(fig2, use_container_width=True)
