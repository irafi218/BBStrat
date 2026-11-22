"""
strategies/bb_breakout.py
─────────────────────────
Bollinger Band Breakout strategy for Backtrader.

Entry:
  LONG  → close crosses above upper band + optional volume spike
  SHORT → close crosses below lower band + optional volume spike

Exit:
  Stop-loss  : ATR-based, set on entry
  Take-profit: RR multiple of stop distance
  Trailing   : optional % trail updated every bar
"""

import logging
import backtrader as bt
import backtrader.indicators as btind

logger = logging.getLogger(__name__)


class BollingerBandBreakout(bt.Strategy):
    params = dict(
        bb_period=20,
        bb_std_dev=2.0,
        volume_confirm=False,
        volume_ma_period=20,
        volume_multiplier=1.5,
        atr_period=14,
        stop_loss_atr_mult=2.0,
        take_profit_rr=2.0,
        trailing_stop=True,
        trailing_stop_pct=0.03,
        squeeze_threshold=0.005,
        risk_per_trade_pct=0.02,
        max_open_trades=3,
        on_trade_open=None,
        on_trade_close=None,
    )

    def __init__(self):
        self.bb = btind.BollingerBands(
            self.data.close,
            period=self.p.bb_period,
            devfactor=self.p.bb_std_dev,
        )
        self.atr = btind.AverageTrueRange(self.data, period=self.p.atr_period)
        self.vol_ma = btind.SimpleMovingAverage(self.data.volume, period=self.p.volume_ma_period)

        # BB bandwidth for squeeze filter
        self.bandwidth = (self.bb.top - self.bb.bot) / self.bb.mid

        # Track order and trade state
        self.order       = None
        self.entry_price = None
        self.stop_price  = None
        self.tp_price    = None
        self.trade_side  = None

    def next(self):
        # Don't act while an order is pending
        if self.order:
            return

        price = self.data.close[0]

        # ── Manage open position ──────────────────────────────────────────────
        if self.position:
            self._manage_exits(price)
            return

        # ── Entry filters ─────────────────────────────────────────────────────
        if self.bandwidth[0] < self.p.squeeze_threshold:
            return  # in squeeze, wait for expansion

        vol_ok = (not self.p.volume_confirm) or (
            self.data.volume[0] > self.vol_ma[0] * self.p.volume_multiplier
        )
        if not vol_ok:
            return

        atr = self.atr[0]
        if atr == 0:
            return

        # ── Long signal: close crosses above upper band ───────────────────────
        if self.data.close[-1] <= self.bb.top[-1] and price > self.bb.top[0]:
            stop = price - atr * self.p.stop_loss_atr_mult
            tp   = price + abs(price - stop) * self.p.take_profit_rr
            size = self._calc_size(price, stop)
            if size > 0:
                self.entry_price = price
                self.stop_price  = stop
                self.tp_price    = tp
                self.trade_side  = "LONG"
                self.order       = self.buy(size=size)
                logger.info("LONG signal  price=%.2f  stop=%.2f  tp=%.2f  size=%.6f",
                            price, stop, tp, size)

        # ── Short signal: close crosses below lower band ──────────────────────
        elif self.data.close[-1] >= self.bb.bot[-1] and price < self.bb.bot[0]:
            stop = price + atr * self.p.stop_loss_atr_mult
            tp   = price - abs(stop - price) * self.p.take_profit_rr
            size = self._calc_size(price, stop)
            if size > 0:
                self.entry_price = price
                self.stop_price  = stop
                self.tp_price    = tp
                self.trade_side  = "SHORT"
                self.order       = self.sell(size=size)
                logger.info("SHORT signal  price=%.2f  stop=%.2f  tp=%.2f  size=%.6f",
                            price, stop, tp, size)

    def _manage_exits(self, price):
        """Check stop-loss, take-profit and trailing stop every bar."""
        if self.stop_price is None:
            return

        # Update trailing stop
        if self.p.trailing_stop:
            if self.trade_side == "LONG":
                trail = price * (1 - self.p.trailing_stop_pct)
                self.stop_price = max(self.stop_price, trail)
            else:
                trail = price * (1 + self.p.trailing_stop_pct)
                self.stop_price = min(self.stop_price, trail)

        # Check exits
        if self.trade_side == "LONG":
            if price <= self.stop_price:
                logger.info("LONG SL exit  price=%.2f  stop=%.2f", price, self.stop_price)
                self.order = self.close()
            elif price >= self.tp_price:
                logger.info("LONG TP exit  price=%.2f  tp=%.2f", price, self.tp_price)
                self.order = self.close()
        else:
            if price >= self.stop_price:
                logger.info("SHORT SL exit  price=%.2f  stop=%.2f", price, self.stop_price)
                self.order = self.close()
            elif price <= self.tp_price:
                logger.info("SHORT TP exit  price=%.2f  tp=%.2f", price, self.tp_price)
                self.order = self.close()

    def _calc_size(self, entry, stop):
        risk_amount   = self.broker.getvalue() * self.p.risk_per_trade_pct
        risk_per_unit = abs(entry - stop)
        if risk_per_unit == 0:
            return 0
        size     = risk_amount / risk_per_unit
        max_size = (self.broker.getcash() * 0.95) / entry
        return round(min(size, max_size), 6)

    def notify_order(self, order):
        if order.status in [order.Completed]:
            self.order = None  # clear pending order flag
        elif order.status in [order.Canceled, order.Rejected, order.Margin]:
            logger.warning("Order failed: %s", order.status)
            self.order      = None
            self.stop_price = None
            self.tp_price   = None

    def notify_trade(self, trade):
        if trade.isclosed:
            logger.info("Trade closed  pnl=%.2f  pnlcomm=%.2f", trade.pnl, trade.pnlcomm)
            self.stop_price  = None
            self.tp_price    = None
            self.entry_price = None
            self.trade_side  = None
