"""utils/config_loader.py — Load YAML config + inject .env overrides."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | None = None) -> dict:
    """Load config/config.yaml and override credentials from .env."""
    cfg_path = Path(path) if path else _ROOT / "config" / "config.yaml"
    env_path = _ROOT / "config" / ".env"

    load_dotenv(env_path, override=False)          # load .env if present

    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    # Inject env vars into exchange section
    cfg["exchange"]["api_key"]    = os.getenv("BINANCE_API_KEY",    cfg["exchange"].get("api_key",    ""))
    cfg["exchange"]["api_secret"] = os.getenv("BINANCE_API_SECRET", cfg["exchange"].get("api_secret", ""))

    return cfg
