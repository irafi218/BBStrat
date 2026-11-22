"""dashboard/pages/config_page.py — Live config viewer."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import yaml
import streamlit as st
from utils.config_loader import load_config


def render_config_page():
    st.title("⚙️ Configuration")
    cfg = load_config()

    tabs = st.tabs(["Exchange", "Strategy", "Risk", "Backtest", "Raw YAML"])

    with tabs[0]:
        st.json({k: ("***" if "secret" in k or "key" in k else v)
                 for k, v in cfg["exchange"].items()})
    with tabs[1]:
        st.json(cfg["strategy"])
    with tabs[2]:
        st.json(cfg["risk"])
    with tabs[3]:
        st.json(cfg["backtest"])
    with tabs[4]:
        safe = {k: v for k, v in cfg.items()}
        safe["exchange"] = {k: ("***" if "secret" in k or "key" in k else v)
                            for k, v in cfg["exchange"].items()}
        st.code(yaml.dump(safe, default_flow_style=False), language="yaml")

    st.info("Edit `config/config.yaml` or `config/.env` to change settings. Restart the app to apply.")
