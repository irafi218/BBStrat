"""
database/db_manager.py
─────────────────────
SQLite database manager.
Handles schema creation, OHLCV storage, trade logs, and performance metrics.
"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────────────────

DDL = """
-- Raw price data
CREATE TABLE IF NOT EXISTS ohlcv (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    open_time   INTEGER NOT NULL,               -- Unix ms
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    close_time  INTEGER NOT NULL,
    UNIQUE (symbol, timeframe, open_time)
);

-- Trade journal
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        TEXT    UNIQUE NOT NULL,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,           -- BUY | SELL
    entry_price     REAL    NOT NULL,
    exit_price      REAL,
    quantity        REAL    NOT NULL,
    entry_time      TEXT    NOT NULL,
    exit_time       TEXT,
    status          TEXT    NOT NULL DEFAULT 'OPEN',  -- OPEN | CLOSED | CANCELLED
    pnl             REAL    DEFAULT 0.0,
    pnl_pct         REAL    DEFAULT 0.0,
    commission      REAL    DEFAULT 0.0,
    stop_loss       REAL,
    take_profit     REAL,
    exit_reason     TEXT,                       -- TP | SL | TRAILING | SIGNAL | MANUAL
    strategy        TEXT    DEFAULT 'BollingerBandBreakout',
    notes           TEXT
);

-- Equity curve snapshots (recorded periodically)
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_time   TEXT    NOT NULL,
    portfolio_value REAL    NOT NULL,
    cash            REAL    NOT NULL,
    invested        REAL    NOT NULL,
    open_pnl        REAL    NOT NULL,
    realized_pnl    REAL    NOT NULL,
    drawdown_pct    REAL    NOT NULL
);

-- System / error events
CREATE TABLE IF NOT EXISTS system_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    log_time    TEXT    NOT NULL,
    level       TEXT    NOT NULL,
    module      TEXT,
    message     TEXT    NOT NULL
);

-- Backtest run results
CREATE TABLE IF NOT EXISTS backtest_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_time        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL,
    start_date      TEXT    NOT NULL,
    end_date        TEXT    NOT NULL,
    initial_cash    REAL    NOT NULL,
    final_value     REAL    NOT NULL,
    total_return    REAL    NOT NULL,
    sharpe_ratio    REAL,
    max_drawdown    REAL,
    win_rate        REAL,
    total_trades    INTEGER,
    config_snapshot TEXT                        -- JSON dump of config used
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf_time ON ohlcv(symbol, timeframe, open_time);
CREATE INDEX IF NOT EXISTS idx_trades_symbol        ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_status        ON trades(status);
CREATE INDEX IF NOT EXISTS idx_equity_time          ON equity_snapshots(snapshot_time);
"""


class DBManager:
    """Lightweight SQLite wrapper with context-managed connections."""

    def __init__(self, db_path: str = "database/trading_bot.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Internals ─────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(DDL)
        logger.info("Database ready: %s", self.db_path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── OHLCV ─────────────────────────────────────────────────────────────────

    def upsert_ohlcv(self, rows: list[dict]) -> int:
        """Insert or ignore OHLCV rows. Returns number of rows inserted."""
        if not rows:
            return 0
        sql = """
            INSERT OR IGNORE INTO ohlcv
                (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
            VALUES
                (:symbol, :timeframe, :open_time, :open, :high, :low, :close, :volume, :close_time)
        """
        with self._conn() as conn:
            cursor = conn.executemany(sql, rows)
            return cursor.rowcount

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        sql = "SELECT * FROM ohlcv WHERE symbol=? AND timeframe=?"
        params: list = [symbol, timeframe]
        if start_time:
            sql += " AND open_time >= ?"
            params.append(start_time)
        if end_time:
            sql += " AND open_time <= ?"
            params.append(end_time)
        sql += " ORDER BY open_time DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        return df.sort_values("open_time").reset_index(drop=True)

    # ── Trades ────────────────────────────────────────────────────────────────

    def log_trade_open(self, trade: dict) -> None:
        sql = """
            INSERT OR REPLACE INTO trades
                (trade_id, symbol, side, entry_price, quantity,
                 entry_time, status, stop_loss, take_profit, strategy, notes)
            VALUES
                (:trade_id, :symbol, :side, :entry_price, :quantity,
                 :entry_time, 'OPEN', :stop_loss, :take_profit, :strategy, :notes)
        """
        with self._conn() as conn:
            conn.execute(sql, trade)

    def log_trade_close(self, trade_id: str, close_data: dict) -> None:
        sql = """
            UPDATE trades SET
                exit_price  = :exit_price,
                exit_time   = :exit_time,
                status      = 'CLOSED',
                pnl         = :pnl,
                pnl_pct     = :pnl_pct,
                commission   = :commission,
                exit_reason = :exit_reason
            WHERE trade_id = :trade_id
        """
        close_data["trade_id"] = trade_id
        with self._conn() as conn:
            conn.execute(sql, close_data)

    def get_trades(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        sql = "SELECT * FROM trades WHERE 1=1"
        params: list = []
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return pd.read_sql_query(sql, conn, params=params)

    # ── Equity ────────────────────────────────────────────────────────────────

    def save_equity_snapshot(self, snapshot: dict) -> None:
        sql = """
            INSERT INTO equity_snapshots
                (snapshot_time, portfolio_value, cash, invested, open_pnl, realized_pnl, drawdown_pct)
            VALUES
                (:snapshot_time, :portfolio_value, :cash, :invested, :open_pnl, :realized_pnl, :drawdown_pct)
        """
        with self._conn() as conn:
            conn.execute(sql, snapshot)

    def get_equity_curve(self, limit: int = 2000) -> pd.DataFrame:
        sql = "SELECT * FROM equity_snapshots ORDER BY snapshot_time DESC LIMIT ?"
        with self._conn() as conn:
            df = pd.read_sql_query(sql, conn, params=[limit])
        return df.sort_values("snapshot_time").reset_index(drop=True)

    # ── Backtest ──────────────────────────────────────────────────────────────

    def save_backtest_result(self, result: dict) -> None:
        sql = """
            INSERT INTO backtest_results
                (run_time, symbol, timeframe, start_date, end_date, initial_cash,
                 final_value, total_return, sharpe_ratio, max_drawdown,
                 win_rate, total_trades, config_snapshot)
            VALUES
                (:run_time, :symbol, :timeframe, :start_date, :end_date, :initial_cash,
                 :final_value, :total_return, :sharpe_ratio, :max_drawdown,
                 :win_rate, :total_trades, :config_snapshot)
        """
        with self._conn() as conn:
            conn.execute(sql, result)

    def get_backtest_results(self, limit: int = 50) -> pd.DataFrame:
        sql = "SELECT * FROM backtest_results ORDER BY run_time DESC LIMIT ?"
        with self._conn() as conn:
            return pd.read_sql_query(sql, conn, params=[limit])
