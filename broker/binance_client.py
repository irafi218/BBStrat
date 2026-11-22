"""
broker/binance_client.py
────────────────────────
Thin wrapper around python-binance that covers:
  • REST: historical OHLCV download, account info, order placement
  • WebSocket: live kline streaming (callback-based)
"""
 
import logging
import os
from typing import Callable, Optional
 
logger = logging.getLogger(__name__)
 
try:
    from binance.client import Client
    from binance.enums import SIDE_BUY, SIDE_SELL, TIME_IN_FORCE_GTC
    from binance.exceptions import BinanceAPIException, BinanceOrderException
    try:
        from binance.streams import BinanceSocketManager
    except ImportError:
        BinanceSocketManager = None
    BINANCE_AVAILABLE = True
except ImportError:
    BINANCE_AVAILABLE = False
    BinanceSocketManager = None
    logger.warning("python-binance not installed — BinanceClient will run in stub mode.")
 
INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
}
 
 
class BinanceClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = True,
    ):
        self.testnet    = testnet
        self.api_key    = api_key    or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        self._client: Optional["Client"] = None
        self._ws_manager = None
        self._ws_conn_key = None
 
        if BINANCE_AVAILABLE:
            self._connect()
 
    def _connect(self) -> None:
        try:
            # Empty strings are fine for public endpoints
            self._client = Client(
                self.api_key or "",
                self.api_secret or "",
                testnet=self.testnet,
            )
            self._client.ping()
            logger.info("Binance %s connected", "TESTNET" if self.testnet else "LIVE")
        except Exception as exc:
            logger.error("Binance connection failed: %s", exc)
            self._client = None
 
    def is_connected(self) -> bool:
        return self._client is not None
 
    # ── Market Data ───────────────────────────────────────────────────────────
 
    def get_historical_klines(
        self,
        symbol: str,
        interval: str,
        start_str: str,
        end_str: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        if not self.is_connected():
            logger.warning("Not connected — returning empty klines")
            return []
 
        bi = INTERVAL_MAP.get(interval, interval)
        try:
            raw = self._client.get_historical_klines(
                symbol, bi, start_str, end_str=end_str, limit=limit
            )
        except BinanceAPIException as exc:
            logger.error("get_historical_klines error: %s", exc)
            return []
 
        rows = []
        for k in raw:
            rows.append({
                "symbol":     symbol,
                "timeframe":  interval,
                "open_time":  int(k[0]),
                "open":       float(k[1]),
                "high":       float(k[2]),
                "low":        float(k[3]),
                "close":      float(k[4]),
                "volume":     float(k[5]),
                "close_time": int(k[6]),
            })
        logger.info("Fetched %d klines for %s/%s", len(rows), symbol, interval)
        return rows
 
    def get_ticker(self, symbol: str) -> Optional[dict]:
        if not self.is_connected():
            return None
        try:
            return self._client.get_symbol_ticker(symbol=symbol)
        except BinanceAPIException as exc:
            logger.error("get_ticker error: %s", exc)
            return None
 
    # ── Account (requires API key) ────────────────────────────────────────────
 
    def get_account_balance(self, asset: str = "USDT") -> float:
        if not self.is_connected() or not self.api_key:
            return 0.0
        try:
            balance = self._client.get_asset_balance(asset=asset)
            return float(balance["free"]) if balance else 0.0
        except BinanceAPIException as exc:
            logger.error("get_account_balance error: %s", exc)
            return 0.0
 
    def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        if not self.is_connected() or not self.api_key:
            return []
        try:
            return self._client.get_open_orders(symbol=symbol) if symbol else self._client.get_open_orders()
        except BinanceAPIException as exc:
            logger.error("get_open_orders error: %s", exc)
            return []
 
    # ── Order Execution (requires API key) ────────────────────────────────────
 
    def place_market_order(self, symbol: str, side: str, quantity: float) -> Optional[dict]:
        if not self.is_connected() or not self.api_key:
            logger.warning("Stub market order: %s %s %.6f", side, symbol, quantity)
            return {"status": "STUB", "symbol": symbol, "side": side, "qty": quantity}
        try:
            bs = SIDE_BUY if side.upper() == "BUY" else SIDE_SELL
            order = self._client.order_market(symbol=symbol, side=bs, quantity=quantity)
            logger.info("Market order placed: %s", order)
            return order
        except (BinanceAPIException, BinanceOrderException) as exc:
            logger.error("place_market_order error: %s", exc)
            return None
 
    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Optional[dict]:
        if not self.is_connected() or not self.api_key:
            logger.warning("Stub limit order: %s %s %.6f @ %.4f", side, symbol, quantity, price)
            return {"status": "STUB"}
        try:
            bs = SIDE_BUY if side.upper() == "BUY" else SIDE_SELL
            return self._client.order_limit(
                symbol=symbol, side=bs, quantity=quantity,
                price=str(round(price, 8)), timeInForce=TIME_IN_FORCE_GTC,
            )
        except (BinanceAPIException, BinanceOrderException) as exc:
            logger.error("place_limit_order error: %s", exc)
            return None
 
    def cancel_order(self, symbol: str, order_id: int) -> bool:
        if not self.is_connected() or not self.api_key:
            return False
        try:
            self._client.cancel_order(symbol=symbol, orderId=order_id)
            return True
        except BinanceAPIException as exc:
            logger.error("cancel_order error: %s", exc)
            return False
 
    # ── WebSocket ─────────────────────────────────────────────────────────────
 
    def start_kline_stream(self, symbol: str, interval: str, callback: Callable) -> None:
        if not self.is_connected() or BinanceSocketManager is None:
            logger.warning("WebSocket unavailable")
            return
 
        def _handler(msg):
            if msg.get("e") == "error":
                logger.error("WS error: %s", msg)
                return
            k = msg.get("k", {})
            callback({
                "symbol":     symbol,
                "timeframe":  interval,
                "open_time":  k.get("t"),
                "open":       float(k.get("o", 0)),
                "high":       float(k.get("h", 0)),
                "low":        float(k.get("l", 0)),
                "close":      float(k.get("c", 0)),
                "volume":     float(k.get("v", 0)),
                "close_time": k.get("T"),
                "is_closed":  k.get("x", False),
            })
 
        self._ws_manager  = BinanceSocketManager(self._client)
        bi = INTERVAL_MAP.get(interval, interval)
        self._ws_conn_key = self._ws_manager.start_kline_socket(symbol, _handler, interval=bi)
        self._ws_manager.start()
        logger.info("Kline stream started: %s/%s", symbol, interval)
 
    def stop_stream(self) -> None:
        if self._ws_manager and self._ws_conn_key:
            self._ws_manager.stop_socket(self._ws_conn_key)
            self._ws_manager.close()
            logger.info("WebSocket stream stopped")