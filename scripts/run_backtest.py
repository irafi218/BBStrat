"""
scripts/run_backtest.py
───────────────────────
Run a historical backtest of the Confluence strategy.

Usage:
    python scripts/run_backtest.py
    python scripts/run_backtest.py --symbol BTCUSDT --start 2023-01-01 --end 2024-12-31
    python scripts/run_backtest.py --strategy bb        # use original BB-only strategy
    python scripts/run_backtest.py --strategy confluence # use multi-factor strategy (default)
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtrader as bt
import pandas as pd
import yaml

from broker.binance_client import BinanceClient
from database.db_manager import DBManager
from strategies.bb_breakout import BollingerBandBreakout
from strategies.confluence_strategy import ConfluenceStrategy
from utils.config_loader import load_config
from utils.fear_greed import fetch_fear_greed
from utils.logger_setup import setup_logging

logger = logging.getLogger(__name__)


class PandasOHLCV(bt.feeds.PandasData):
    params = dict(
        datetime=None,
        open="open", high="high", low="low",
        close="close", volume="volume",
        openinterest=-1,
    )


def extract_metrics(cerebro, strat, initial_cash):
    final_value  = cerebro.broker.getvalue()
    total_return = (final_value - initial_cash) / initial_cash

    sharpe = max_dd = None
    trade_analysis = {}

    for name, analyzer in strat.analyzers.getitems():
        if "sharpe" in name.lower():
            r = analyzer.get_analysis()
            sharpe = r.get("sharperatio")
        elif "drawdown" in name.lower():
            r = analyzer.get_analysis()
            max_dd = r.get("max", {}).get("drawdown", 0) / 100
        elif "trade" in name.lower():
            trade_analysis = analyzer.get_analysis()

    total_trades = (
        trade_analysis.get("total", {}).get("closed", 0) or
        trade_analysis.get("total", {}).get("total", 0)
    )
    wins     = trade_analysis.get("won", {}).get("total", 0)
    win_rate = wins / total_trades if total_trades else 0.0

    return dict(
        initial_cash=initial_cash,
        final_value=round(final_value, 4),
        total_return=round(total_return, 6),
        sharpe_ratio=round(sharpe, 4) if sharpe else None,
        max_drawdown=round(max_dd, 6) if max_dd else None,
        win_rate=round(win_rate, 4),
        total_trades=total_trades,
    )


def run_backtest(cfg_override: dict | None = None) -> dict:
    cfg = load_config()
    if cfg_override:
        cfg["backtest"].update({k: v for k, v in cfg_override.items()
                                 if k in cfg["backtest"]})

    bt_cfg    = cfg["backtest"]
    strat_cfg = cfg["strategy"]
    risk_cfg  = cfg["risk"]

    symbol     = cfg_override.get("symbol",     cfg["trading"]["default_symbol"]) if cfg_override else cfg["trading"]["default_symbol"]
    timeframe  = cfg_override.get("timeframe",  cfg["trading"]["timeframe"])       if cfg_override else cfg["trading"]["timeframe"]
    start_date = cfg_override.get("start_date", bt_cfg["start_date"])              if cfg_override else bt_cfg["start_date"]
    end_date   = cfg_override.get("end_date",   bt_cfg["end_date"])                if cfg_override else bt_cfg["end_date"]
    strategy   = cfg_override.get("strategy",   "confluence")                      if cfg_override else "confluence"

    logger.info("Backtest [%s]: %s %s  %s → %s", strategy, symbol, timeframe, start_date, end_date)

    # ── Load OHLCV ────────────────────────────────────────────────────────────
    db = DBManager(cfg["data"]["db_path"])
    df = db.get_ohlcv(symbol, timeframe, limit=50_000)

    if df.empty:
        logger.info("No local data — fetching from Binance…")
        client = BinanceClient(testnet=cfg["exchange"]["testnet"])
        rows   = client.get_historical_klines(symbol, timeframe, start_date, end_date)
        if rows:
            db.upsert_ohlcv(rows)
            df = db.get_ohlcv(symbol, timeframe, limit=50_000)

    if df.empty:
        raise RuntimeError("No OHLCV data available for backtest")

    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("datetime").sort_index()
    df = df[["open", "high", "low", "close", "volume"]]
    df = df.loc[start_date:end_date]
    logger.info("Loaded %d candles", len(df))

    # ── Fear & Greed ──────────────────────────────────────────────────────────
    fg_data = None
    if strategy == "confluence":
        logger.info("Fetching Fear & Greed index…")
        fg_data = fetch_fear_greed(limit=1000)

    # ── Cerebro ───────────────────────────────────────────────────────────────
    cerebro = bt.Cerebro(stdstats=True)
    cerebro.adddata(PandasOHLCV(dataname=df), name=symbol)

    if strategy == "bb":
        cerebro.addstrategy(
            BollingerBandBreakout,
            bb_period          = strat_cfg["bb_period"],
            bb_std_dev         = strat_cfg["bb_std_dev"],
            volume_confirm     = strat_cfg["volume_confirm"],
            volume_ma_period   = strat_cfg["volume_ma_period"],
            volume_multiplier  = strat_cfg["volume_multiplier"],
            atr_period         = strat_cfg["atr_period"],
            stop_loss_atr_mult = risk_cfg["stop_loss_atr_mult"],
            take_profit_rr     = risk_cfg["take_profit_rr"],
            trailing_stop      = risk_cfg["trailing_stop"],
            trailing_stop_pct  = risk_cfg["trailing_stop_pct"],
            squeeze_threshold  = strat_cfg["squeeze_threshold"],
        )
    else:
        cerebro.addstrategy(
            ConfluenceStrategy,
            bb_period          = strat_cfg["bb_period"],
            bb_std_dev         = strat_cfg["bb_std_dev"],
            volume_ma_period   = strat_cfg["volume_ma_period"],
            volume_multiplier  = strat_cfg["volume_multiplier"],
            atr_period         = strat_cfg["atr_period"],
            stop_loss_atr_mult = risk_cfg["stop_loss_atr_mult"],
            take_profit_rr     = risk_cfg["take_profit_rr"],
            trailing_stop      = risk_cfg["trailing_stop"],
            trailing_stop_pct  = risk_cfg["trailing_stop_pct"],
            min_score          = strat_cfg.get("min_score", 2),
            swing_period       = strat_cfg.get("swing_period", 5),
            structure_lookback = strat_cfg.get("structure_lookback", 50),
            structure_tolerance= strat_cfg.get("structure_tolerance", 0.002),
            fg_trend_period    = strat_cfg.get("fg_trend_period", 5),
            fg_data            = fg_data,
        )

    initial_cash = float(bt_cfg["initial_cash"])
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=float(bt_cfg["commission"]))

    cerebro.addanalyzer(bt.analyzers.SharpeRatio,   _name="sharpe", riskfreerate=0.0, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown,       _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer,  _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns,        _name="returns")

    results = cerebro.run()
    strat   = results[0]

    metrics = extract_metrics(cerebro, strat, initial_cash)
    metrics.update(
        run_time=datetime.utcnow().isoformat(),
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        config_snapshot=json.dumps(cfg),
    )

    db.save_backtest_result(metrics)

    logger.info(
        "Backtest complete  return=%.2f%%  sharpe=%.2f  max_dd=%.2f%%  trades=%d  win_rate=%.1f%%",
        metrics["total_return"] * 100,
        metrics["sharpe_ratio"] or 0,
        (metrics["max_drawdown"] or 0) * 100,
        metrics["total_trades"],
        metrics["win_rate"] * 100,
    )
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run confluence backtest")
    parser.add_argument("--symbol",    default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--start",     default=None, dest="start_date")
    parser.add_argument("--end",       default=None, dest="end_date")
    parser.add_argument("--strategy",  default="confluence", choices=["bb", "confluence"])
    args = parser.parse_args()

    setup_logging()
    override = {k: v for k, v in vars(args).items() if v is not None}

    result = run_backtest(override if override else None)
    print("\n── Backtest Results ───────────────────────────────")
    for k, v in result.items():
        if k != "config_snapshot":
            print(f"  {k:<20} {v}")
