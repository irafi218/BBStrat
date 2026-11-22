"""
utils/notifier.py
─────────────────
Telegram notification helper.
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in config/.env
"""

import logging
import os
import requests
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token   = token   or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.info("Telegram not configured — notifications disabled")

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        try:
            url  = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id":    self.chat_id,
                "text":       message,
                "parse_mode": "HTML",
            }, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False

    def trade_opened(self, trade: dict) -> None:
        emoji = "🟢" if trade["side"] == "LONG" else "🔴"
        msg = (
            f"{emoji} <b>TRADE OPENED</b>\n"
            f"Symbol    : {trade['symbol']}\n"
            f"Side      : {trade['side']}\n"
            f"Entry     : ${trade['entry_price']:,.2f}\n"
            f"Stop Loss : ${trade['stop_loss']:,.2f}\n"
            f"Take Profit: ${trade['take_profit']:,.2f}\n"
            f"Quantity  : {trade['quantity']:.6f}\n"
            f"Score     : {trade.get('score', '—')}/3\n"
            f"Time      : {trade['entry_time']}"
        )
        self.send(msg)

    def trade_closed(self, trade: dict) -> None:
        pnl   = trade.get("pnl", 0)
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"Symbol    : {trade['symbol']}\n"
            f"Side      : {trade['side']}\n"
            f"Entry     : ${trade['entry_price']:,.2f}\n"
            f"Exit      : ${trade['exit_price']:,.2f}\n"
            f"PnL       : ${pnl:+.2f} ({trade.get('pnl_pct', 0)*100:+.2f}%)\n"
            f"Reason    : {trade.get('exit_reason', '—')}\n"
            f"Time      : {trade['exit_time']}"
        )
        self.send(msg)

    def signal_detected(self, symbol: str, direction: str, score: int, factors: dict) -> None:
        emoji = "📡"
        factor_str = "  ".join([f"{'✓' if v else '✗'} {k}" for k, v in factors.items()])
        msg = (
            f"{emoji} <b>SIGNAL: {direction}</b>\n"
            f"Symbol  : {symbol}\n"
            f"Score   : {score}/3\n"
            f"Factors : {factor_str}"
        )
        self.send(msg)

    def error(self, message: str) -> None:
        self.send(f"⚠️ <b>BOT ERROR</b>\n{message}")

    def heartbeat(self, status: dict) -> None:
        msg = (
            f"💓 <b>BOT HEARTBEAT</b>\n"
            f"Status      : {'🟢 Running' if status.get('running') else '🔴 Stopped'}\n"
            f"Open Trades : {status.get('open_trades', 0)}\n"
            f"Portfolio   : ${status.get('portfolio_value', 0):,.2f}\n"
            f"Last Check  : {status.get('last_check', '—')}"
        )
        self.send(msg)
