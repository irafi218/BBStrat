"""dashboard/app.py — Main Streamlit entry point."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st

st.set_page_config(
    page_title="Confluence Trading Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0d1117; }
[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #21262d; }
.block-container { padding-top: 1.2rem; padding-bottom: 1rem; }
div[data-testid="metric-container"] {
    background: #161b22; border: 1px solid #21262d;
    border-radius: 8px; padding: 12px 16px;
}
div[data-testid="metric-container"] label { color: #8b949e !important; font-size: 0.72rem; letter-spacing: 1px; text-transform: uppercase; }
div[data-testid="metric-container"] [data-testid="stMetricValue"] { font-size: 1.5rem; font-weight: 700; }
.stTabs [data-baseweb="tab"] { color: #8b949e; }
.stTabs [aria-selected="true"] { color: #58a6ff !important; border-bottom-color: #58a6ff !important; }
h1, h2, h3 { color: #e6edf3 !important; }
hr { border-color: #21262d; }
</style>
""", unsafe_allow_html=True)

from utils.config_loader import load_config
from database.db_manager import DBManager

@st.cache_resource(show_spinner=False)
def get_db():
    cfg = load_config()
    return DBManager(cfg["data"]["db_path"])

with st.sidebar:
    st.markdown("## ⚡ Confluence Bot")
    st.caption("Multi-factor algorithmic trading")
    st.divider()
    page = st.radio("", [
        "📊 Market Overview",
        "🔬 Backtest Analytics",
        "📋 Trade Breakdown",
        "🌊 Market Microstructure",
        "😱 Fear & Greed",
        "▶ Run Backtest",
        "⚙️ Config",
    ], label_visibility="collapsed")
    st.divider()
    cfg = load_config()
    mode  = cfg["trading"]["trade_mode"].upper()
    color = "#3fb950" if mode == "PAPER" else "#f85149"
    st.markdown(f"**Mode:** <span style='color:{color}'>{mode}</span>", unsafe_allow_html=True)
    st.markdown(f"**Strategy:** Confluence v2")
    st.markdown(f"**Testnet:** {'✅' if cfg['exchange']['testnet'] else '❌'}")
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if page == "📊 Market Overview":
    from dashboard.components.market_overview import render
    render()
elif page == "🔬 Backtest Analytics":
    from dashboard.components.backtest_analytics import render
    render()
elif page == "📋 Trade Breakdown":
    from dashboard.components.trade_breakdown import render
    render()
elif page == "🌊 Market Microstructure":
    from dashboard.components.microstructure import render
    render()
elif page == "😱 Fear & Greed":
    from dashboard.components.fear_greed_page import render
    render()
elif page == "▶ Run Backtest":
    from dashboard.components.backtest_page import render_backtest_page
    render_backtest_page()
elif page == "⚙️ Config":
    from dashboard.components.config_page import render_config_page
    render_config_page()
