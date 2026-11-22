"""
strategies/confluence_strategy.py
──────────────────────────────────
Multi-factor confluence strategy.

Hard requirements (BOTH must be true to consider entry):
  - BB breakout (close crosses band)
  - ATR expanding (trending volatility)

Scoring factors (need min_score out of 3 remaining):
  - Market structure (price respects S/R)
  - Volume spike or rising volume trend
  - Fear & Greed trend bias

Total possible score: 3. Default min_score: 2.
"""

import logging
import backtrader as bt
import backtrader.indicators as btind

logger = logging.getLogger(__name__)


class ATRExpansion(bt.Indicator):
    lines = ("expanding",)
    params = dict(atr_period=14, ma_period=20)

    def __init__(self):
        self.atr    = btind.AverageTrueRange(self.data, period=self.p.atr_period)
        self.atr_ma = btind.SimpleMovingAverage(self.atr, period=self.p.ma_period)

    def next(self):
        self.lines.expanding[0] = 1.0 if self.atr[0] > self.atr_ma[0] else 0.0


class ConfluenceStrategy(bt.Strategy):
    params = dict(
        # Bollinger Bands
        bb_period=20,
        bb_std_dev=2.0,
        # Volume
        volume_ma_period=20,
        volume_multiplier=1.5,
        # ATR / risk
        atr_period=14,
        atr_ma_period=20,
        stop_loss_atr_mult=2.0,
        take_profit_rr=3.0,
        trailing_stop=False,
        trailing_stop_pct=0.03,
        # Market structure
        swing_period=5,
        structure_lookback=50,
        structure_tolerance=0.002,
        # Scoring (out of 3 soft factors)
        min_score=2,
        # Fear & Greed
        fg_data=None,
        fg_trend_period=5,
        # Position sizing
        risk_per_trade_pct=0.02,
        # Callbacks
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
        self.atr_expand = ATRExpansion(
            self.data,
            atr_period=self.p.atr_period,
            ma_period=self.p.atr_ma_period,
        )
        self.vol_ma    = btind.SimpleMovingAverage(self.data.volume, period=self.p.volume_ma_period)
        self.vol_trend = btind.SimpleMovingAverage(self.data.volume, period=self.p.volume_ma_period * 2)

        self.order       = None
        self.stop_price  = None
        self.tp_price    = None
        self.trade_side  = None
        self.entry_price = None

    def next(self):
        if self.order:
            return

        price = self.data.close[0]

        if self.position:
            self._manage_exits(price)
            return

        # ── Hard requirement 1: ATR must be expanding ─────────────────────────
        if not self.atr_expand.expanding[0]:
            return

        # ── Hard requirement 2: BB breakout ───────────────────────────────────
        long_bb  = self.data.close[-1] <= self.bb.top[-1] and price > self.bb.top[0]
        short_bb = self.data.close[-1] >= self.bb.bot[-1] and price < self.bb.bot[0]

        if not long_bb and not short_bb:
            return

        direction = "LONG" if long_bb else "SHORT"

        # ── Soft scoring (3 factors) ──────────────────────────────────────────
        score, factors = self._score_soft(direction, price)

        if score < self.p.min_score:
            return

        self._enter(direction, price, score, factors)

    def _score_soft(self, direction, price):
        score   = 0
        factors = {}

        # 1. Market structure
        struct_ok = self._check_structure(direction, price)
        factors["market_structure"] = struct_ok
        if struct_ok:
            score += 1

        # 2. Volume
        vol_spike    = self.data.volume[0] > self.vol_ma[0] * self.p.volume_multiplier
        vol_trend_up = self.vol_ma[0] > self.vol_trend[0]
        vol_ok = vol_spike or vol_trend_up
        factors["volume"] = vol_ok
        if vol_ok:
            score += 1

        # 3. Fear & Greed trend
        fg_ok = self._check_fg_trend(direction)
        factors["fg_trend"] = fg_ok
        if fg_ok:
            score += 1

        return score, factors

    def _check_structure(self, direction, price):
        lookback = min(self.p.structure_lookback, len(self.data) - 1)
        tol      = self.p.structure_tolerance

        if direction == "LONG":
            levels = []
            for i in range(2, lookback):
                lo = self.data.low[-i]
                if lo < self.data.low[-i + 1] and lo < self.data.low[-i - 1]:
                    levels.append(lo)
            if not levels:
                return False
            nearest_support = max(levels)  # closest support below
            return nearest_support * (1 - tol) <= price
        else:
            levels = []
            for i in range(2, lookback):
                hi = self.data.high[-i]
                if hi > self.data.high[-i + 1] and hi > self.data.high[-i - 1]:
                    levels.append(hi)
            if not levels:
                return False
            nearest_resistance = min(levels)  # closest resistance above
            return price <= nearest_resistance * (1 + tol)

    def _check_fg_trend(self, direction):
        if self.p.fg_data is None:
            return True
        try:
            current_date = self.data.datetime.date(0)
            fg_series    = self.p.fg_data
            recent = fg_series[fg_series.index <= str(current_date)].tail(self.p.fg_trend_period + 1)
            if len(recent) < 2:
                return True
            fg_rising  = recent.iloc[-1] > recent.iloc[0]
            return fg_rising if direction == "LONG" else not fg_rising
        except Exception:
            return True

    def _enter(self, direction, price, score, factors):
        atr = self.atr[0]
        if atr == 0:
            return

        if direction == "LONG":
            stop = price - atr * self.p.stop_loss_atr_mult
            tp   = price + abs(price - stop) * self.p.take_profit_rr
        else:
            stop = price + atr * self.p.stop_loss_atr_mult
            tp   = price - abs(stop - price) * self.p.take_profit_rr

        size = self._calc_size(price, stop)
        if size <= 0:
            return

        self.stop_price  = stop
        self.tp_price    = tp
        self.entry_price = price
        self.trade_side  = direction

        self.order = self.buy(size=size) if direction == "LONG" else self.sell(size=size)

        logger.info(
            "%s entry  score=%d/3  price=%.2f  stop=%.2f  tp=%.2f  size=%.6f  soft=%s",
            direction, score, price, stop, tp, size,
            {k: "✓" if v else "✗" for k, v in factors.items()},
        )

    def _manage_exits(self, price):
        if self.stop_price is None:
            return

        if self.p.trailing_stop:
            if self.trade_side == "LONG":
                self.stop_price = max(self.stop_price, price * (1 - self.p.trailing_stop_pct))
            else:
                self.stop_price = min(self.stop_price, price * (1 + self.p.trailing_stop_pct))

        if self.trade_side == "LONG":
            if price <= self.stop_price:
                self.order = self.close()
            elif price >= self.tp_price:
                self.order = self.close()
        else:
            if price >= self.stop_price:
                self.order = self.close()
            elif price <= self.tp_price:
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
        if order.status == order.Completed:
            self.order = None
        elif order.status in [order.Canceled, order.Rejected, order.Margin]:
            logger.warning("Order failed: %s", order.getstatusname())
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
