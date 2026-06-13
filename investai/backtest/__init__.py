"""Event-driven historical backtester.

Reuses the SAME indicator engine, scanner scoring, rule engine, sizing, and cost
model as the live/paper path, so the backtest measures the strategy that would
actually trade — not a parallel reimplementation. Built to avoid lookahead bias:
features and the decision use data only up to the signal bar's close, entry is at
that close, and exits are evaluated on subsequent bars.
"""
from .engine import Backtester, BacktestResult

__all__ = ["Backtester", "BacktestResult"]
