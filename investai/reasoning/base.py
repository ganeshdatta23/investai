"""Reasoning interface + portfolio context passed to it."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..schemas import Candidate, Decision


@dataclass
class PortfolioContext:
    """Snapshot of portfolio state used for risk-budget and concentration checks."""
    equity: float
    open_positions: int = 0
    open_risk_pct: float = 0.0          # sum of open-trade risk as % of equity
    daily_realized_loss_pct: float = 0.0  # today's realized loss as % (positive number)
    open_symbols: set[str] = field(default_factory=set)
    sector_counts: dict[str, int] = field(default_factory=dict)


class ReasoningEngine(ABC):
    @abstractmethod
    def decide(self, candidate: Candidate, portfolio: PortfolioContext) -> Decision:
        ...
