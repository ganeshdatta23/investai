"""Offline synthetic data adapter — deterministic, no credentials, no network.

Lets the full pipeline (scan -> reason -> paper ledger -> report) run and be
tested before Upstox onboarding. Data is a seeded random walk with occasional
trend/volatility regimes; it is clearly synthetic and never presented as real.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..schemas import Instrument
from .base import DataAdapter


def _seed_for(symbol: str) -> int:
    return abs(hash(symbol)) % (2**32)


class SyntheticAdapter(DataAdapter):
    name = "synthetic"
    feed_type = "simulated"
    classification = "SIMULATED_DATA"

    def __init__(self, cfg=None, base_seed: int = 7):
        self.cfg = cfg
        self.base_seed = base_seed
        self._last_close: dict[str, float] = {}

    def is_ready(self) -> bool:
        return True

    def resolve_universe(self, seed: list[str] | None) -> list[Instrument]:
        symbols = seed or ["SYN1", "SYN2", "SYN3", "SYN4", "SYN5"]
        return [
            Instrument(symbol=s.upper(), instrument_key=f"SYN|{s.upper()}", name=f"{s} (synthetic)")
            for s in symbols
        ]

    def fetch_history(self, instrument: Instrument, interval: str, days: int) -> pd.DataFrame:
        rng = np.random.default_rng(self.base_seed + _seed_for(instrument.symbol))
        n = max(days, 220)
        # Drift/vol vary by symbol so the ranker has something to discriminate on.
        drift = rng.normal(0.0004, 0.0006)
        vol = abs(rng.normal(0.015, 0.005)) + 0.005
        start = 50 + rng.uniform(0, 4000)
        rets = rng.normal(drift, vol, n)
        close = start * np.exp(np.cumsum(rets))
        # Build OHLC around close with intrabar noise.
        intrabar = np.abs(rng.normal(0, vol, n)) * close
        high = close + intrabar * rng.uniform(0.3, 1.0, n)
        low = close - intrabar * rng.uniform(0.3, 1.0, n)
        open_ = np.concatenate([[close[0]], close[:-1]]) + rng.normal(0, vol / 2, n) * close
        high = np.maximum.reduce([high, open_, close])
        low = np.minimum.reduce([low, open_, close])
        base_vol = rng.uniform(2e5, 5e6)
        volume = (base_vol * (1 + np.abs(rng.normal(0, 0.6, n)))).round()
        # Occasional volume spike on the last bar to exercise breakout logic.
        if rng.uniform() > 0.5:
            volume[-1] *= rng.uniform(1.8, 3.5)

        idx = pd.date_range(end=dt.date.today(), periods=n, freq="B", tz="UTC")
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )
        self._last_close[instrument.instrument_key] = float(close[-1])
        return df

    def last_prices(self, instruments: list[Instrument]) -> dict[str, float]:
        out = {}
        for inst in instruments:
            if inst.instrument_key not in self._last_close:
                self.fetch_history(inst, "day", 220)
            out[inst.instrument_key] = self._last_close[inst.instrument_key]
        return out
