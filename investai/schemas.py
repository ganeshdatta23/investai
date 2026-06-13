"""Typed structures shared across the engine."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    AVOID = "AVOID"
    WAIT = "WAIT_FOR_CONFIRMATION"


@dataclass(frozen=True)
class RiskPolicy:
    max_risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_portfolio_risk_pct: float = 5.0
    min_reward_risk: float = 2.0
    max_open_positions: int = 8
    max_correlated_positions: int = 3


@dataclass(frozen=True)
class ExecutionCosts:
    fee_pct: float = 0.03       # round-trip, percent
    slippage_pct: float = 0.05  # per side, percent


@dataclass
class Instrument:
    symbol: str                 # NSE trading symbol, e.g. "RELIANCE"
    instrument_key: str         # Upstox key, e.g. "NSE_EQ|INE002A01018"
    name: str = ""
    sector: str = ""


@dataclass
class Candidate:
    """A symbol that passed the deterministic scanner, with its computed features."""
    instrument: Instrument
    last_price: float
    features: dict[str, Any] = field(default_factory=dict)  # indicator snapshot
    score: float = 0.0          # numeric expectancy score from the scanner

    @property
    def symbol(self) -> str:
        return self.instrument.symbol


@dataclass
class Decision:
    """Mirrors the required output schema exactly."""
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    action: str = Action.HOLD.value
    confidence: int = 0
    setup_quality: str = "F"            # A|B|C|D|F
    trend_state: str = ""
    market_regime: str = ""
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    reward_risk: Optional[float] = None
    position_size: Optional[int] = None
    risk_per_trade_percent: Optional[float] = None
    invalidations: list[str] = field(default_factory=list)
    key_factors: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    decision_rationale: str = ""
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
