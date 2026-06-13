"""DataAdapter interface — the only data contract the engine depends on.

Swapping Upstox for Angel/Kite/yfinance later means implementing this ABC;
no other module changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from ..schemas import Instrument


class DataAdapter(ABC):
    # Identity / data-provenance metadata (overridden per adapter).
    name: str = "base"
    feed_type: str = "unknown"            # realtime | delayed | simulated
    classification: str = "UNKNOWN"       # REAL_MARKET_DATA | SIMULATED_DATA

    def status(self) -> dict:
        """Adapter status block for reporting. Concrete (does not break the interface)."""
        from ..scheduler import market_is_open  # local import avoids an import cycle
        return {
            "adapter": self.name,
            "feed_type": self.feed_type,
            "market_status": "open" if market_is_open() else "closed",
            "data_quality": self.classification,
        }

    @abstractmethod
    def is_ready(self) -> bool:
        """True if the adapter can serve data right now (authenticated, reachable)."""

    @abstractmethod
    def resolve_universe(self, seed: list[str] | None) -> list[Instrument]:
        """Return tradable instruments. If `seed` is given, restrict to those symbols."""

    @abstractmethod
    def fetch_history(self, instrument: Instrument, interval: str, days: int) -> pd.DataFrame:
        """OHLCV history as a DataFrame indexed by tz-aware timestamp (ascending),
        with float columns: open, high, low, close, volume.
        Returns an empty DataFrame on failure (never fabricated data)."""

    @abstractmethod
    def last_prices(self, instruments: list[Instrument]) -> dict[str, float]:
        """Map instrument_key -> last traded price. Missing keys are omitted."""


class DataUnavailable(RuntimeError):
    """Raised when a data source fails — distinct from 'no opportunities found'."""
