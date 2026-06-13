"""NSE equity universe from the Upstox instruments master file.

Source (grounded from Upstox docs):
  https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
Each record carries: instrument_key, trading_symbol, name, segment,
exchange, instrument_type, isin, lot_size, tick_size, ... .
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import requests

from ..schemas import Instrument

NSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_EQUITY_TYPES = {"EQ", "EQUITY"}


def _download_instruments(url: str, timeout: int = 60) -> list[dict]:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    raw = gzip.decompress(resp.content)
    return json.loads(raw)


def load_instruments(cache_path: Path, ttl_hours: float = 24.0,
                     url: str = NSE_INSTRUMENTS_URL) -> list[dict]:
    """Return the raw NSE instrument records, refreshing the cache past its TTL."""
    fresh = (
        cache_path.exists()
        and (time.time() - cache_path.stat().st_mtime) < ttl_hours * 3600
    )
    if fresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    records = _download_instruments(url)
    cache_path.write_text(json.dumps(records), encoding="utf-8")
    return records


def nse_equities(records: list[dict]) -> dict[str, Instrument]:
    """Map NSE trading symbol -> Instrument for cash-segment equities only."""
    out: dict[str, Instrument] = {}
    for r in records:
        segment = (r.get("segment") or "").upper()
        itype = (r.get("instrument_type") or "").upper()
        if segment != "NSE_EQ":
            continue
        if itype and itype not in _EQUITY_TYPES:
            continue  # skip ETFs/indices/etc. when type is known
        sym = (r.get("trading_symbol") or r.get("tradingsymbol") or "").strip()
        key = (r.get("instrument_key") or "").strip()
        if not sym or not key:
            continue
        out[sym] = Instrument(
            symbol=sym,
            instrument_key=key,
            name=(r.get("name") or "").strip(),
            sector="",  # Upstox master has no sector; left blank (see README).
        )
    return out


def resolve_universe(
    cache_path: Path, seed: list[str] | None, ttl_hours: float = 24.0
) -> tuple[list[Instrument], list[str]]:
    """Return (instruments, missing_symbols). `seed=None/empty` -> full NSE equity list."""
    records = load_instruments(cache_path, ttl_hours)
    mapping = nse_equities(records)
    if not seed:
        return list(mapping.values()), []
    chosen, missing = [], []
    for s in seed:
        s = s.strip().upper()
        if s in mapping:
            chosen.append(mapping[s])
        else:
            missing.append(s)
    return chosen, missing
