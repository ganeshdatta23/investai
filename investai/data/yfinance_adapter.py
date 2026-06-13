"""Yahoo Finance fallback adapter (public v8 chart API via `requests`).

Deliberately does NOT use the `yfinance` package — it calls Yahoo's chart
endpoint directly, so there are no compiled transitive deps and we control
retry/rate-limiting. Provides REAL (≈15-min delayed) NSE data so the autonomous
scan works with no broker authentication. Upstox remains the production adapter;
this is the automatic fallback.
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from .._log import log
from ..schemas import Instrument
from .base import DataAdapter
from .universe import resolve_universe as _resolve_universe

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# config interval -> (yahoo interval, max lookback days Yahoo permits)
_INTERVAL_MAP: dict[str, tuple[str, int]] = {
    "1minute": ("1m", 7),
    "5minute": ("5m", 60),
    "15minute": ("15m", 60),
    "30minute": ("30m", 60),
    "day": ("1d", 3650),
    "week": ("1wk", 3650),
    "month": ("1mo", 3650),
}


class YFinanceAdapter(DataAdapter):
    name = "yfinance"
    feed_type = "delayed"
    classification = "REAL_MARKET_DATA"

    def __init__(self, cfg, min_interval_s: float = 0.15, max_retries: int = 3):
        self.cfg = cfg
        self.instruments_cache = cfg.path("instruments_cache")
        self.ttl_hours = float(cfg.get("data", "instruments_cache_hours", default=24))
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA, "Accept": "application/json"})
        self._last_call = 0.0
        self._ready: bool | None = None

    # ---- rate-limited GET with retry/backoff ----------------------------- #
    def _throttle(self) -> None:
        wait = self.min_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get_json(self, symbol: str, params: dict) -> dict | None:
        url = _CHART.format(symbol=symbol)
        backoff = 0.5
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                r = self._session.get(url, params=params, timeout=20)
            except requests.RequestException as e:
                if attempt == self.max_retries:
                    log(f"[yfinance] {symbol}: request error {e}")
                    return None
                time.sleep(backoff); backoff *= 2; continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    return None
            if r.status_code in (429, 503, 999):  # throttled / temporarily blocked
                if attempt == self.max_retries:
                    log(f"[yfinance] {symbol}: throttled HTTP {r.status_code}")
                    return None
                time.sleep(backoff * 2); backoff *= 2; continue
            log(f"[yfinance] {symbol}: HTTP {r.status_code}")  # 404/400 -> not on Yahoo
            return None
        return None

    # ---- DataAdapter interface ------------------------------------------ #
    def is_ready(self) -> bool:
        """Probe Yahoo once; cached. False on a real outage -> data_unavailable."""
        if self._ready is None:
            data = self._get_json("RELIANCE.NS", {"range": "5d", "interval": "1d"})
            self._ready = bool(data and (data.get("chart", {}) or {}).get("result"))
        return self._ready

    def resolve_universe(self, seed: list[str] | None) -> list[Instrument]:
        instruments, missing = _resolve_universe(self.instruments_cache, seed, self.ttl_hours)
        if missing:
            log(f"[universe] {len(missing)} seed symbols not on NSE master: {missing}")
        return instruments

    @staticmethod
    def yahoo_symbol(inst: Instrument) -> str:
        return f"{inst.symbol}.NS"

    def fetch_history(self, instrument: Instrument, interval: str, days: int) -> pd.DataFrame:
        return self.fetch_raw(self.yahoo_symbol(instrument), interval, days)

    def fetch_raw(self, yahoo_symbol: str, interval: str, days: int) -> pd.DataFrame:
        """Fetch by an explicit Yahoo symbol (e.g. '^NSEI' for the NIFTY 50 index,
        which takes no '.NS' suffix)."""
        y_int, max_days = _INTERVAL_MAP.get(interval, ("1d", 3650))
        days = min(days, max_days)
        period2 = int(time.time())
        period1 = period2 - days * 86400
        data = self._get_json(yahoo_symbol,
                              {"period1": period1, "period2": period2, "interval": y_int})
        return chart_to_frame(data)

    def last_prices(self, instruments: list[Instrument]) -> dict[str, float]:
        out: dict[str, float] = {}
        for inst in instruments:
            data = self._get_json(self.yahoo_symbol(inst), {"range": "1d", "interval": "1d"})
            if not data:
                continue
            try:
                meta = data["chart"]["result"][0]["meta"]
            except (KeyError, IndexError, TypeError):
                continue
            price = meta.get("regularMarketPrice")
            if price is not None:
                out[inst.instrument_key] = float(price)
        return out


def chart_to_frame(data: dict | None) -> pd.DataFrame:
    """Parse a Yahoo chart payload into an ascending OHLCV frame. Never fabricates:
    on any malformed/empty payload it returns an empty frame."""
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).astype(float)
    try:
        res = data["chart"]["result"][0]
    except (TypeError, KeyError, IndexError):
        return empty
    ts = res.get("timestamp")
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    if not ts or not quote:
        return empty
    df = pd.DataFrame(
        {
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        },
        index=pd.to_datetime(ts, unit="s", utc=True),
    )
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = df["volume"].fillna(0.0)
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.sort_index()[["open", "high", "low", "close", "volume"]]
