"""dashboard/pages/fear_greed_page.py — Fear & Greed gauge, history, BTC correlation."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from database.db_manager import DBManager
from utils.config_loader import load_config
from utils.fear_greed import fetch_fear_greed, get_fg_label, get_fg_color

DARK = "#0d1117"
GRID = "#21262d"


@st.cache_data(ttl=3600)
def load_fg():
    return fetch_fear_greed(limit=365)

@st.cache_data(ttl=60)
def load_price(symbol, timeframe):
    cfg = load_config()
    db  = DBManager(cfg["data"]["db_path"])
    df  = db.get_ohlcv(symbol, timeframe, limit=365)
    if df.empty:
        return df
    df["dt"]   = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["date"] = df["dt"].dt.strftime("%Y-%m-%d")
    return df


def _gauge(value: float) -> go.Figure:
    """Render a semicircular gauge for the F&G value."""
    label = get_fg_label(value)
    color = get_fg_color(value)

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=value,
        number={"font": {"size": 52, "color": color}},
        title={"text": f"<b>{label}</b>", "font": {"size": 20, "color": color}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "#8b949e",
                     "tickvals": [0, 25, 45, 55, 75, 100],
                     "ticktext": ["0","Fear","Neutral","","Greed","100"]},
            "bar":  {"color": color, "thickness": 0.25},
            "bgcolor": "#161b22",
            "borderwidth": 0,
            "steps": [
                {"range": [0, 25],   "color": "rgba(63,185,80,0.15)"},
                {"range": [25, 45],  "color": "rgba(88,166,255,0.15)"},
                {"range": [45, 55],  "color": "rgba(139,148,158,0.15)"},
                {"range": [55, 75],  "color": "rgba(227,179,65,0.15)"},
                {"range": [75, 100], "color": "rgba(248,81,73,0.15)"},
            ],
            "threshold": {"line": {"color": color, "width": 4}, "value": value},
        },
    ))
    fig.update_layout(height=280, margin=dict(l=20,r=20,t=20,b=20),
        paper_bgcolor=DARK, font=dict(color="#e6edf3"))
    return fig


def render():
    st.title("😱 Fear & Greed Index")
    cfg = load_config()

    fg = load_fg()
    if fg.empty:
        st.error("Could not load Fear & Greed data. Check internet connection.")
        return

    current_val  = float(fg.iloc[-1])
    yesterday    = float(fg.iloc[-2]) if len(fg) > 1 else current_val
    week_ago     = float(fg.iloc[-8]) if len(fg) > 7 else current_val
    month_ago    = float(fg.iloc[-31]) if len(fg) > 30 else current_val

    # ── Gauge + deltas ────────────────────────────────────────────────────────
    col_gauge, col_stats = st.columns([2, 3])

    with col_gauge:
        st.plotly_chart(_gauge(current_val), use_container_width=True)

    with col_stats:
        st.markdown("### Historical Comparison")
        s1, s2, s3 = st.columns(3)
        s1.metric("Yesterday",  f"{yesterday:.0f}",  f"{current_val-yesterday:+.0f}", delta_color="normal")
        s2.metric("Week Ago",   f"{week_ago:.0f}",   f"{current_val-week_ago:+.0f}",  delta_color="normal")
        s3.metric("Month Ago",  f"{month_ago:.0f}",  f"{current_val-month_ago:+.0f}", delta_color="normal")

        st.markdown("### Classification Guide")
        st.markdown("""
| Range | Sentiment | Trading Bias |
|-------|-----------|--------------|
| 0–24  | 🟢 Extreme Fear   | Potential long opportunities |
| 25–44 | 🔵 Fear            | Mild long bias |
| 45–55 | ⚫ Neutral          | No directional bias |
| 56–74 | 🟡 Greed           | Mild short bias |
| 75–100| 🔴 Extreme Greed  | Potential short / take profit |
""")

    st.divider()

    # ── History chart + BTC correlation ───────────────────────────────────────
    fg_df = fg.reset_index()
    fg_df.columns = ["date", "fg_value"]
    fg_df["date"] = pd.to_datetime(fg_df["date"])

    symbol = cfg["trading"]["default_symbol"]
    price_df = load_price(symbol, "1d")

    tabs = st.tabs(["📈 F&G History", "🔗 BTC Correlation", "📊 Distribution"])

    # ── Tab 1: History ────────────────────────────────────────────────────────
    with tabs[0]:
        rows = 2 if not price_df.empty else 1
        heights = [0.4, 0.6] if rows == 2 else [1.0]
        titles  = ("Fear & Greed Index", f"{symbol} Price") if rows == 2 else ("Fear & Greed Index",)

        fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
            row_heights=heights, vertical_spacing=0.03,
            subplot_titles=titles)

        # Colour F&G line by zone
        fig.add_trace(go.Scatter(
            x=fg_df["date"], y=fg_df["fg_value"],
            mode="lines", name="F&G",
            line=dict(color="#58a6ff", width=2),
            fill="tozeroy", fillcolor="rgba(88,166,255,0.08)",
        ), row=1, col=1)

        # Zone bands
        for y0, y1, color, label in [
            (0,25,"rgba(63,185,80,0.08)","Extreme Fear"),
            (25,45,"rgba(88,166,255,0.06)","Fear"),
            (55,75,"rgba(227,179,65,0.06)","Greed"),
            (75,100,"rgba(248,81,73,0.08)","Extreme Greed"),
        ]:
            fig.add_hrect(y0=y0, y1=y1, fillcolor=color,
                line_width=0, row=1, col=1)

        fig.add_hline(y=50, line_color=GRID, line_dash="dot", row=1, col=1)

        if not price_df.empty:
            price_df["dt"] = pd.to_datetime(price_df["dt"])
            daily_price = price_df.groupby("date")["close"].last().reset_index()
            daily_price["date"] = pd.to_datetime(daily_price["date"])
            fig.add_trace(go.Scatter(
                x=daily_price["date"], y=daily_price["close"],
                mode="lines", name=symbol,
                line=dict(color="#e3b341", width=1.5),
            ), row=2, col=1)

        fig.update_layout(template="plotly_dark", height=500,
            margin=dict(l=0,r=0,t=30,b=0),
            paper_bgcolor=DARK, plot_bgcolor=DARK)
        fig.update_xaxes(gridcolor=GRID)
        fig.update_yaxes(gridcolor=GRID)
        fig.update_yaxes(range=[0,100], row=1, col=1)
        st.plotly_chart(fig, use_container_width=True)

    # ── Tab 2: Correlation ────────────────────────────────────────────────────
    with tabs[1]:
        if price_df.empty:
            st.info("No price data available for correlation analysis.")
        else:
            daily_price = price_df.groupby("date")["close"].last().reset_index()
            daily_price["date"] = pd.to_datetime(daily_price["date"])
            fg_df2 = fg_df.copy()
            fg_df2["date"] = pd.to_datetime(fg_df2["date"])
            merged = pd.merge(fg_df2, daily_price, on="date", how="inner")
            merged["price_return"] = merged["close"].pct_change() * 100

            if len(merged) > 10:
                corr = merged[["fg_value","close"]].corr().iloc[0,1]
                st.metric("F&G vs Price Correlation", f"{corr:.3f}",
                    help="1 = perfect positive, -1 = perfect negative, 0 = no correlation")

                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(
                    x=merged["fg_value"], y=merged["close"],
                    mode="markers", name="F&G vs Price",
                    marker=dict(
                        color=merged["fg_value"],
                        colorscale=["#3fb950","#8b949e","#f85149"],
                        size=6, opacity=0.7,
                        colorbar=dict(title="F&G"),
                    ),
                ))
                # Trend line
                z = np.polyfit(merged["fg_value"].dropna(), merged["close"].dropna(), 1)
                p = np.poly1d(z)
                x_line = np.linspace(merged["fg_value"].min(), merged["fg_value"].max(), 100)
                fig2.add_trace(go.Scatter(x=x_line, y=p(x_line), mode="lines",
                    name="Trend", line=dict(color="#58a6ff", dash="dash", width=2)))
                fig2.update_layout(template="plotly_dark", height=380,
                    margin=dict(l=0,r=0,t=0,b=0),
                    xaxis_title="Fear & Greed Value",
                    yaxis_title=f"{symbol} Price",
                    paper_bgcolor=DARK, plot_bgcolor=DARK)
                fig2.update_xaxes(gridcolor=GRID)
                fig2.update_yaxes(gridcolor=GRID)
                st.plotly_chart(fig2, use_container_width=True)

                # Returns by F&G zone
                st.subheader("Next-Day Returns by F&G Zone")
                merged["fg_zone"] = pd.cut(merged["fg_value"],
                    bins=[0,25,45,55,75,100],
                    labels=["Extreme Fear","Fear","Neutral","Greed","Extreme Greed"])
                zone_returns = merged.groupby("fg_zone")["price_return"].agg(
                    ["mean","std","count"]).reset_index()
                zone_returns.columns = ["zone","avg_next_day_return","std","n_days"]
                fig3 = go.Figure(go.Bar(
                    x=zone_returns["zone"], y=zone_returns["avg_next_day_return"],
                    marker_color=["#3fb950","#58a6ff","#8b949e","#e3b341","#f85149"],
                    text=[f"{v:.2f}%" for v in zone_returns["avg_next_day_return"]],
                    textposition="outside",
                ))
                fig3.add_hline(y=0, line_color=GRID, line_dash="dash")
                fig3.update_layout(template="plotly_dark", height=300,
                    margin=dict(l=0,r=0,t=0,b=0),
                    yaxis_title="Avg Next-Day Return %",
                    paper_bgcolor=DARK, plot_bgcolor=DARK)
                st.plotly_chart(fig3, use_container_width=True)

    # ── Tab 3: Distribution ───────────────────────────────────────────────────
    with tabs[2]:
        import plotly.express as px
        col_a, col_b = st.columns(2)
        with col_a:
            fig4 = px.histogram(fg_df, x="fg_value", nbins=20,
                color_discrete_sequence=["#58a6ff"], template="plotly_dark",
                title="F&G Distribution (1 Year)")
            fig4.update_layout(height=300, margin=dict(l=0,r=0,t=40,b=0),
                paper_bgcolor=DARK, plot_bgcolor=DARK)
            st.plotly_chart(fig4, use_container_width=True)
        with col_b:
            zone_counts = pd.cut(fg_df["fg_value"],
                bins=[0,25,45,55,75,100],
                labels=["Extreme Fear","Fear","Neutral","Greed","Extreme Greed"]
            ).value_counts().reset_index()
            zone_counts.columns = ["zone","days"]
            fig5 = px.pie(zone_counts, values="days", names="zone",
                color_discrete_sequence=["#3fb950","#58a6ff","#8b949e","#e3b341","#f85149"],
                template="plotly_dark", hole=0.4, title="Days Spent in Each Zone")
            fig5.update_layout(height=300, margin=dict(l=0,r=0,t=40,b=0),
                paper_bgcolor=DARK)
            st.plotly_chart(fig5, use_container_width=True)
