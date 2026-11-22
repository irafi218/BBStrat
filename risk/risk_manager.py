"""
risk/risk_manager.py
────────────────────
Pre-trade and portfolio-level risk checks.
All limits are loaded from config; call check_trade() before every order.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    """Mutable risk state updated after each trade / equity snapshot."""
    peak_equity: float = 0.0
    current_equity: float = 0.0
    daily_start_equity: float = 0.0
    daily_date: date = field(default_factory=date.today)
    realized_daily_pnl: float = 0.0
    open_trades: int = 0
    is_halted: bool = False
    halt_reason: str = ""


@dataclass
class TradeProposal:
    symbol: str
    side: str           # BUY | SELL
    entry_price: float
    stop_loss: float
    quantity: float
    portfolio_value: float
    cash: float


@dataclass
class RiskCheckResult:
    approved: bool
    reason: str = ""
    adjusted_quantity: Optional[float] = None


class RiskManager:
    """
    Validates trade proposals against configurable risk limits.

    Usage
    -----
    rm = RiskManager(cfg["risk"])
    result = rm.check_trade(proposal, state)
    if result.approved:
        place_order(...)
    """

    def __init__(self, risk_cfg: dict):
        self.cfg = risk_cfg
        self.max_position_pct       = risk_cfg.get("max_position_pct", 0.10)
        self.max_open_trades        = risk_cfg.get("max_open_trades", 3)
        self.max_daily_dd_pct       = risk_cfg.get("max_daily_drawdown_pct", 0.05)
        self.max_total_dd_pct       = risk_cfg.get("max_total_drawdown_pct", 0.15)
        self.stop_loss_atr_mult     = risk_cfg.get("stop_loss_atr_mult", 2.0)
        self.trailing_stop_pct      = risk_cfg.get("trailing_stop_pct", 0.03)

    # ── Public API ────────────────────────────────────────────────────────────

    def check_trade(self, proposal: TradeProposal, state: RiskState) -> RiskCheckResult:
        """Run all risk checks and return approval status."""

        # 1. System halt
        if state.is_halted:
            return RiskCheckResult(False, f"System halted: {state.halt_reason}")

        # 2. Max concurrent trades
        if state.open_trades >= self.max_open_trades:
            return RiskCheckResult(False, f"Max open trades reached ({self.max_open_trades})")

        # 3. Max position size
        position_value = proposal.entry_price * proposal.quantity
        max_allowed    = proposal.portfolio_value * self.max_position_pct
        if position_value > max_allowed:
            adjusted_qty = max_allowed / proposal.entry_price
            logger.warning(
                "Position size reduced %.6f → %.6f (max_position_pct=%.0f%%)",
                proposal.quantity, adjusted_qty, self.max_position_pct * 100,
            )
            return RiskCheckResult(True, "Quantity adjusted to max position size", adjusted_qty)

        # 4. Sufficient cash
        if position_value > proposal.cash * 0.95:
            return RiskCheckResult(False, "Insufficient cash for trade")

        # 5. Stop-loss required
        if proposal.stop_loss is None or proposal.stop_loss == 0:
            return RiskCheckResult(False, "Stop-loss must be set")

        # 6. Daily drawdown check
        daily_dd = self._daily_drawdown(state)
        if daily_dd >= self.max_daily_dd_pct:
            state.is_halted  = True
            state.halt_reason = f"Daily drawdown limit breached ({daily_dd:.1%})"
            return RiskCheckResult(False, state.halt_reason)

        # 7. Total drawdown check
        total_dd = self._total_drawdown(state)
        if total_dd >= self.max_total_dd_pct:
            state.is_halted  = True
            state.halt_reason = f"Total drawdown limit breached ({total_dd:.1%})"
            return RiskCheckResult(False, state.halt_reason)

        return RiskCheckResult(True, "All checks passed")

    def update_equity(self, state: RiskState, new_equity: float) -> None:
        """Call after every portfolio value update."""
        # Reset daily tracking if new day
        today = date.today()
        if today != state.daily_date:
            state.daily_start_equity = new_equity
            state.realized_daily_pnl = 0.0
            state.daily_date         = today

        if new_equity > state.peak_equity:
            state.peak_equity = new_equity
        state.current_equity = new_equity

        # Auto-reset halt if equity recovered (optional logic)
        total_dd = self._total_drawdown(state)
        if state.is_halted and total_dd < self.max_total_dd_pct * 0.8:
            logger.info("Drawdown recovered, clearing halt")
            state.is_halted  = False
            state.halt_reason = ""

    def calculate_position_size(
        self,
        portfolio_value: float,
        entry_price: float,
        stop_price: float,
        risk_pct: float = 0.02,
    ) -> float:
        """Kelly-inspired fixed-fraction position sizing."""
        risk_amount   = portfolio_value * risk_pct
        risk_per_unit = abs(entry_price - stop_price)
        if risk_per_unit == 0:
            return 0.0
        return risk_amount / risk_per_unit

    def calculate_stop_loss(
        self,
        entry_price: float,
        atr: float,
        side: str,
        mult: Optional[float] = None,
    ) -> float:
        mult = mult or self.stop_loss_atr_mult
        return entry_price - atr * mult if side == "BUY" else entry_price + atr * mult

    def calculate_take_profit(
        self,
        entry_price: float,
        stop_price: float,
        rr_ratio: float = 2.0,
        side: str = "BUY",
    ) -> float:
        dist = abs(entry_price - stop_price) * rr_ratio
        return entry_price + dist if side == "BUY" else entry_price - dist

    # ── Internals ─────────────────────────────────────────────────────────────

    def _daily_drawdown(self, state: RiskState) -> float:
        if state.daily_start_equity == 0:
            return 0.0
        return max(0, (state.daily_start_equity - state.current_equity) / state.daily_start_equity)

    def _total_drawdown(self, state: RiskState) -> float:
        if state.peak_equity == 0:
            return 0.0
        return max(0, (state.peak_equity - state.current_equity) / state.peak_equity)
