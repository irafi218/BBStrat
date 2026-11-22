"""
scripts/live_trader.py
──────────────────────
24/7 paper trading loop using the Confluence strategy.

How it works:
  1. Every N seconds, check if a new 4h candle has closed
  2. Fetch latest candles from Binance → store in SQLite
  3. Run confluence scoring on the latest bar
  4. If score >= min_score and BB breakout confirmed → open paper trade
  5. Check all open trades for stop-loss / take-profit hits
  6. Log everything to SQLite + send Telegram alerts
  7. Write equity snapshot every candle

Run:
    python scripts/live_trader.py
    python scripts/live_trader.py --symbol ETHUSDT --timeframe 1h

Stop:
    Ctrl+C  (graceful shutdown)
"""

import argparse
import json
import logging
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np

from broker.binance_client import BinanceClient
from database.db_manager import DBManager
from risk.risk_manager import RiskManager, RiskState, TradeProposal
from utils.config_loader import load_config
from utils.fear_greed import fetch_fear_greed
from utils.logger_setup import setup_logging
from utils.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

# ── Globals ───────────────────────────────────────────────────────────────────
RUNNING      = True
POLL_SECONDS = 60       # check every 60s if new candle closed
HEARTBEAT_HR = 6        # send Telegram heartbeat every N hours


def signal_handler(sig, frame):
    global RUNNING
    logger.info("Shutdown signal received — stopping gracefully…")
    RUNNING = False

signal.signal(signal.SIGINT,  signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ── Indicator calculations ────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Compute all indicators on the latest bar. Returns last row as Series."""
    sc = cfg["strategy"]
    rc = cfg["risk"]

    period  = sc["bb_period"]
    std_dev = sc["bb_std_dev"]

    df = df.copy()
    df["bb_mid"] = df["close"].rolling(period).mean()
    df["bb_std"] = df["close"].rolling(period).std()
    df["bb_top"] = df["bb_mid"] + std_dev * df["bb_std"]
    df["bb_bot"] = df["bb_mid"] - std_dev * df["bb_std"]
    df["bb_width"] = (df["bb_top"] - df["bb_bot"]) / df["bb_mid"]

    # ATR
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift()),
            abs(df["low"]  - df["close"].shift()),
        )
    )
    df["atr"]    = df["tr"].rolling(sc["atr_period"]).mean()
    df["atr_ma"] = df["atr"].rolling(20).mean()

    # Volume
    df["vol_ma"]    = df["volume"].rolling(sc["volume_ma_period"]).mean()
    df["vol_trend"] = df["volume"].rolling(sc["volume_ma_period"] * 2).mean()

    return df.iloc[-1], df.iloc[-2]   # last bar, previous bar


def confluence_score(last, prev, cfg, fg_data, direction) -> tuple[bool, int, dict]:
    """
    Returns (hard_req_met, soft_score, factors).
    Hard requirements: BB breakout + ATR expanding.
    Soft factors: market structure, volume, F&G trend.
    """
    sc  = cfg["strategy"]
    rc  = cfg["risk"]
    price = last["close"]

    # ── Hard: BB breakout ─────────────────────────────────────────────────────
    if direction == "LONG":
        bb_ok = prev["close"] <= prev["bb_top"] and price > last["bb_top"]
    else:
        bb_ok = prev["close"] >= prev["bb_bot"] and price < last["bb_bot"]

    # ── Hard: ATR expanding ───────────────────────────────────────────────────
    atr_ok = last["atr"] > last["atr_ma"]

    hard_met = bb_ok and atr_ok

    # ── Soft scoring ──────────────────────────────────────────────────────────
    factors = {}
    score   = 0

    # 1. Volume
    vol_spike    = last["volume"] > last["vol_ma"] * sc["volume_multiplier"]
    vol_trend_up = last["vol_ma"] > last["vol_trend"]
    vol_ok = vol_spike or vol_trend_up
    factors["volume"] = vol_ok
    if vol_ok: score += 1

    # 2. Market structure (simple: price above recent support for long)
    factors["market_structure"] = True   # simplified for live — use full version from strategy
    score += 1

    # 3. F&G trend
    fg_ok = True
    if fg_data is not None and not fg_data.empty:
        try:
            today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            recent  = fg_data[fg_data.index <= today].tail(6)
            if len(recent) >= 2:
                fg_rising = recent.iloc[-1] > recent.iloc[0]
                fg_ok = fg_rising if direction == "LONG" else not fg_rising
        except Exception:
            pass
    factors["fg_trend"] = fg_ok
    if fg_ok: score += 1

    return hard_met, score, factors


# ── Paper trade management ────────────────────────────────────────────────────

class PaperTrader:
    def __init__(self, cfg, db, notifier, risk_mgr, risk_state):
        self.cfg        = cfg
        self.db         = db
        self.notifier   = notifier
        self.risk_mgr   = risk_mgr
        self.risk_state = risk_state
        self.open_trades: dict[str, dict] = {}   # trade_id → trade dict

    def try_open(self, direction, last, score, factors, symbol, portfolio_value, cash):
        rc     = self.cfg["risk"]
        price  = last["close"]
        atr    = last["atr"]

        if direction == "LONG":
            stop = price - atr * rc["stop_loss_atr_mult"]
            tp   = price + abs(price - stop) * rc["take_profit_rr"]
        else:
            stop = price + atr * rc["stop_loss_atr_mult"]
            tp   = price - abs(stop - price) * rc["take_profit_rr"]

        qty = self.risk_mgr.calculate_position_size(
            portfolio_value, price, stop,
            risk_pct=self.cfg["strategy"].get("risk_per_trade_pct", 0.02),
        )
        if qty <= 0:
            return

        proposal = TradeProposal(
            symbol=symbol, side=direction,
            entry_price=price, stop_loss=stop,
            quantity=qty, portfolio_value=portfolio_value, cash=cash,
        )
        result = self.risk_mgr.check_trade(proposal, self.risk_state)
        if not result.approved:
            logger.info("Trade blocked by risk manager: %s", result.reason)
            return
        if result.adjusted_quantity:
            qty = result.adjusted_quantity

        trade_id = str(uuid.uuid4())[:8]
        now      = datetime.now(timezone.utc).isoformat()
        trade    = dict(
            trade_id=trade_id, symbol=symbol, side=direction,
            entry_price=price, stop_loss=stop, take_profit=tp,
            quantity=qty, entry_time=now, score=score,
            strategy="ConfluenceStrategy", notes=json.dumps(factors),
        )
        self.db.log_trade_open(trade)
        self.open_trades[trade_id] = trade
        self.risk_state.open_trades += 1

        logger.info("📈 PAPER TRADE OPENED  %s %s  price=%.2f  stop=%.2f  tp=%.2f  score=%d/3",
                    direction, symbol, price, stop, tp, score)
        self.notifier.trade_opened(trade)

    def check_exits(self, current_price: float, symbol: str) -> None:
        closed_ids = []
        for tid, trade in list(self.open_trades.items()):
            if trade["symbol"] != symbol:
                continue

            side = trade["side"]
            stop = trade["stop_loss"]
            tp   = trade["take_profit"]
            exit_reason = None

            if side == "LONG":
                if current_price <= stop:
                    exit_reason = "SL"
                elif current_price >= tp:
                    exit_reason = "TP"
            else:
                if current_price >= stop:
                    exit_reason = "SL"
                elif current_price <= tp:
                    exit_reason = "TP"

            if exit_reason:
                now     = datetime.now(timezone.utc).isoformat()
                pnl_raw = (current_price - trade["entry_price"]) * trade["quantity"]
                pnl     = pnl_raw if side == "LONG" else -pnl_raw
                comm    = current_price * trade["quantity"] * 0.001
                pnl    -= comm
                pnl_pct = pnl / (trade["entry_price"] * trade["quantity"])

                close_data = dict(
                    exit_price=current_price, exit_time=now,
                    pnl=round(pnl, 4), pnl_pct=round(pnl_pct, 6),
                    commission=round(comm, 4), exit_reason=exit_reason,
                )
                self.db.log_trade_close(tid, close_data)
                trade.update(close_data)
                closed_ids.append(tid)
                self.risk_state.open_trades = max(0, self.risk_state.open_trades - 1)

                emoji = "✅" if pnl >= 0 else "❌"
                logger.info("%s PAPER TRADE CLOSED  %s  exit=%.2f  pnl=%.2f  reason=%s",
                            emoji, symbol, current_price, pnl, exit_reason)
                self.notifier.trade_closed(trade)

        for tid in closed_ids:
            del self.open_trades[tid]


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_live(symbol: str, timeframe: str):
    cfg       = load_config()
    db        = DBManager(cfg["data"]["db_path"])
    client    = BinanceClient(testnet=cfg["exchange"]["testnet"])
    notifier  = TelegramNotifier()
    risk_mgr  = RiskManager(cfg["risk"])
    risk_state = RiskState()

    initial_cash = float(cfg["backtest"]["initial_cash"])
    cash         = initial_cash
    risk_state.peak_equity        = initial_cash
    risk_state.current_equity     = initial_cash
    risk_state.daily_start_equity = initial_cash

    paper = PaperTrader(cfg, db, notifier, risk_mgr, risk_state)
    min_score  = cfg["strategy"].get("min_score", 2)

    logger.info("🚀 Live paper trader started — %s/%s  min_score=%d", symbol, timeframe, min_score)
    notifier.send(f"🚀 <b>Bot Started</b>\nSymbol: {symbol}/{timeframe}\nMode: PAPER")

    last_candle_time = None
    last_heartbeat   = time.time()
    fg_data          = fetch_fear_greed(limit=365)

    while RUNNING:
        try:
            # ── Fetch latest candles ──────────────────────────────────────────
            rows = client.get_historical_klines(symbol, timeframe,
                                                start_str="3 days ago UTC", limit=100)
            if rows:
                db.upsert_ohlcv(rows)

            df = db.get_ohlcv(symbol, timeframe, limit=200)
            if df.empty or len(df) < 50:
                logger.warning("Not enough data, waiting…")
                time.sleep(POLL_SECONDS)
                continue

            # ── Check if new candle closed ────────────────────────────────────
            now_ms = datetime.now(timezone.utc).timestamp() * 1000

            # Find the most recently CLOSED candle (close_time < now)
            closed_df = df[df["close_time"] < now_ms]
            if closed_df.empty:
                time.sleep(POLL_SECONDS)
                continue

            latest_candle_time = int(closed_df.iloc[-1]["open_time"])
            new_candle = latest_candle_time != last_candle_time
            logger.info("Candle check: latest=%d  last=%s  new=%s  price=%.2f",
                        latest_candle_time, last_candle_time, new_candle,
                        float(closed_df.iloc[-1]["close"]))

            current_price = float(closed_df.iloc[-1]["close"])

            # ── Always check exits on every poll ──────────────────────────────
            paper.check_exits(current_price, symbol)

            # ── Only evaluate entries on new closed candle ────────────────────
            if new_candle:
                last_candle_time = latest_candle_time
                dt_str = datetime.fromtimestamp(latest_candle_time / 1000, tz=timezone.utc).isoformat()
                logger.info("New candle closed: %s  price=%.2f", dt_str, current_price)

                last_bar, prev_bar = compute_indicators(df, cfg)

                # Evaluate both directions
                for direction in ["LONG", "SHORT"]:
                    hard_met, score, factors = confluence_score(
                        last_bar, prev_bar, cfg, fg_data, direction)

                    logger.info(
                        "Score [%s]: hard=%s  score=%d/%d  factors=%s",
                        direction, hard_met, score, min_score,
                        {k: "✓" if v else "✗" for k, v in factors.items()}
                    )

                    if hard_met and score >= min_score and not paper.open_trades:
                        logger.info("Signal: %s  score=%d/3  factors=%s", direction, score, factors)
                        notifier.signal_detected(symbol, direction, score, factors)
                        paper.try_open(
                            direction, last_bar, score, factors,
                            symbol, risk_state.current_equity, cash,
                        )
                        break  # only one direction per candle

                # ── Equity snapshot ───────────────────────────────────────────
                open_pnl = sum(
                    (current_price - t["entry_price"]) * t["quantity"]
                    if t["side"] == "LONG"
                    else (t["entry_price"] - current_price) * t["quantity"]
                    for t in paper.open_trades.values()
                )
                portfolio_value = cash + open_pnl
                risk_state.current_equity = portfolio_value
                risk_mgr.update_equity(risk_state, portfolio_value)

                peak    = risk_state.peak_equity
                dd_pct  = (peak - portfolio_value) / peak if peak > 0 else 0
                db.save_equity_snapshot({
                    "snapshot_time":   datetime.now(timezone.utc).isoformat(),
                    "portfolio_value": round(portfolio_value, 4),
                    "cash":            round(cash, 4),
                    "invested":        round(open_pnl, 4),
                    "open_pnl":        round(open_pnl, 4),
                    "realized_pnl":    round(portfolio_value - initial_cash, 4),
                    "drawdown_pct":    round(dd_pct, 6),
                })
                logger.info("Portfolio: $%.2f  open_pnl=%.2f  drawdown=%.2f%%",
                            portfolio_value, open_pnl, dd_pct * 100)

                # Refresh F&G daily
                fg_data = fetch_fear_greed(limit=365)

            # ── Heartbeat every N hours ───────────────────────────────────────
            if time.time() - last_heartbeat > HEARTBEAT_HR * 3600:
                notifier.heartbeat({
                    "running":        True,
                    "open_trades":    len(paper.open_trades),
                    "portfolio_value": risk_state.current_equity,
                    "last_check":     datetime.now(timezone.utc).isoformat(),
                })
                last_heartbeat = time.time()

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            logger.error("Loop error: %s", exc, exc_info=True)
            notifier.error(str(exc))
            time.sleep(30)

    logger.info("Bot stopped.")
    notifier.send("🛑 <b>Bot Stopped</b>")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run live paper trader")
    parser.add_argument("--symbol",    default=None)
    parser.add_argument("--timeframe", default=None)
    args = parser.parse_args()

    setup_logging()
    cfg       = load_config()
    symbol    = args.symbol    or cfg["trading"]["default_symbol"]
    timeframe = args.timeframe or cfg["trading"]["timeframe"]

    run_live(symbol, timeframe)
