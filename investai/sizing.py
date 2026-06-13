"""Risk-based position sizing and cost-adjusted reward:risk.

Position size rule (per spec):
    size = floor( equity * (max_risk_per_trade_pct/100) / |entry - stop| )
Rounded DOWN. Reward:risk is computed NET of fees + slippage so that thin-ATR
setups whose edge is eaten by costs are correctly rejected.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import ExecutionCosts


@dataclass
class SizingResult:
    quantity: int
    risk_amount: float          # currency at risk if stop is hit
    per_share_risk: float
    risk_pct_used: float        # actual % of equity at risk after rounding


def position_size(equity: float, risk_pct: float, entry: float, stop: float) -> SizingResult:
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0 or equity <= 0 or risk_pct <= 0:
        return SizingResult(0, 0.0, per_share_risk, 0.0)
    budget = equity * risk_pct / 100.0
    qty = math.floor(budget / per_share_risk)
    qty = max(qty, 0)
    risk_amount = qty * per_share_risk
    return SizingResult(qty, risk_amount, per_share_risk, risk_amount / equity * 100.0)


def reward_risk(entry: float, stop: float, target: float, direction: str = "long") -> float | None:
    """Gross plan reward:risk for the placed target."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reward = (target - entry) if direction == "long" else (entry - target)
    if reward <= 0:
        return None
    return reward / risk


def net_reward_risk(
    entry: float, stop: float, target: float, costs: ExecutionCosts, direction: str = "long"
) -> float | None:
    """Reward:risk after deducting round-trip fees + slippage (both sides), in price terms."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    # Cost per share: round-trip fees on notional + slippage on entry and exit.
    cost_per_share = entry * (costs.fee_pct + 2.0 * costs.slippage_pct) / 100.0
    reward = (target - entry) if direction == "long" else (entry - target)
    net_reward = reward - cost_per_share
    net_risk = risk + cost_per_share
    if net_reward <= 0 or net_risk <= 0:
        return 0.0
    return net_reward / net_risk
