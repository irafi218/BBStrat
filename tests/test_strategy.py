"""tests/test_strategy.py — Unit tests for strategy & risk components."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import unittest
from datetime import date

from risk.risk_manager import RiskManager, RiskState, TradeProposal


class TestRiskManager(unittest.TestCase):

    def setUp(self):
        self.rm = RiskManager({
            "max_position_pct": 0.10,
            "max_open_trades": 3,
            "max_daily_drawdown_pct": 0.05,
            "max_total_drawdown_pct": 0.15,
            "stop_loss_atr_mult": 2.0,
            "trailing_stop_pct": 0.03,
            "take_profit_rr": 2.0,
        })
        self.state = RiskState(
            peak_equity=10000.0,
            current_equity=10000.0,
            daily_start_equity=10000.0,
            daily_date=date.today(),
            open_trades=0,
            is_halted=False,
        )

    def test_approved_trade(self):
        proposal = TradeProposal(
            symbol="BTCUSDT", side="BUY",
            entry_price=50000.0, stop_loss=48000.0,
            quantity=0.01,
            portfolio_value=10000.0, cash=10000.0,
        )
        result = self.rm.check_trade(proposal, self.state)
        self.assertTrue(result.approved)

    def test_max_open_trades_blocked(self):
        self.state.open_trades = 3
        proposal = TradeProposal(
            symbol="BTCUSDT", side="BUY",
            entry_price=50000.0, stop_loss=48000.0,
            quantity=0.01,
            portfolio_value=10000.0, cash=10000.0,
        )
        result = self.rm.check_trade(proposal, self.state)
        self.assertFalse(result.approved)
        self.assertIn("Max open trades", result.reason)

    def test_halted_system_blocked(self):
        self.state.is_halted  = True
        self.state.halt_reason = "Test halt"
        proposal = TradeProposal(
            symbol="BTCUSDT", side="BUY",
            entry_price=50000.0, stop_loss=48000.0,
            quantity=0.01,
            portfolio_value=10000.0, cash=10000.0,
        )
        result = self.rm.check_trade(proposal, self.state)
        self.assertFalse(result.approved)

    def test_oversized_position_adjusted(self):
        # quantity 0.05 × 50000 = 2500 = 25% of 10000 > max 10%
        proposal = TradeProposal(
            symbol="BTCUSDT", side="BUY",
            entry_price=50000.0, stop_loss=48000.0,
            quantity=0.05,
            portfolio_value=10000.0, cash=10000.0,
        )
        result = self.rm.check_trade(proposal, self.state)
        self.assertTrue(result.approved)
        self.assertIsNotNone(result.adjusted_quantity)
        self.assertLess(result.adjusted_quantity * 50000, 10000 * 0.105)

    def test_daily_drawdown_halt(self):
        self.state.current_equity    = 9400.0    # 6% down
        self.state.daily_start_equity = 10000.0
        proposal = TradeProposal(
            symbol="BTCUSDT", side="BUY",
            entry_price=50000.0, stop_loss=48000.0,
            quantity=0.001,
            portfolio_value=9400.0, cash=9400.0,
        )
        result = self.rm.check_trade(proposal, self.state)
        self.assertFalse(result.approved)
        self.assertTrue(self.state.is_halted)

    def test_position_sizing(self):
        size = self.rm.calculate_position_size(10000, 50000, 48000, risk_pct=0.02)
        # risk = 200, per unit = 2000 → size = 0.1
        self.assertAlmostEqual(size, 0.1, places=5)

    def test_stop_loss_long(self):
        stop = self.rm.calculate_stop_loss(50000, 1000, "BUY", mult=2.0)
        self.assertEqual(stop, 48000.0)

    def test_stop_loss_short(self):
        stop = self.rm.calculate_stop_loss(50000, 1000, "SELL", mult=2.0)
        self.assertEqual(stop, 52000.0)

    def test_take_profit(self):
        tp = self.rm.calculate_take_profit(50000, 48000, rr_ratio=2.0, side="BUY")
        self.assertEqual(tp, 54000.0)


if __name__ == "__main__":
    unittest.main()
