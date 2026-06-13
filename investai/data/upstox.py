"""Upstox v2 data adapter (OAuth2 + historical candles + LTP quotes).

Endpoints (grounded from Upstox API docs):
  authorize : GET  https://api.upstox.com/v2/login/authorization/dialog
  token     : POST https://api.upstox.com/v2/login/authorization/token
  history   : GET  https://api.upstox.com/v2/historical-candle/{key}/{interval}/{to}/{from}
  ltp       : GET  https://api.upstox.com/v2/market-quote/ltp?instrument_key=...

Access tokens expire daily (~03:30 IST), so `investai auth` must be re-run each
trading morning. Token + the date it was obtained are cached on disk.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from urllib.parse import quote, urlencode

import pandas as pd
import requests

from .._log import log
from ..schemas import Instrument
from .base import DataAdapter, DataUnavailable
from .universe import resolve_universe

API = "https://api.upstox.com/v2"
_INTERVALS = {"1minute", "30minute", "day", "week", "month"}


# --------------------------------------------------------------------------- #
# OAuth2
# --------------------------------------------------------------------------- #
class UpstoxAuth:
    def __init__(self, api_key: str, api_secret: str, redirect_uri: str, token_store: Path):
        self.api_key = api_key
        self.api_secret = api_secret
        self.redirect_uri = redirect_uri
        self.token_store = token_store

    @classmethod
    def from_env(cls, token_store: Path) -> "UpstoxAuth":
        key = os.environ.get("UPSTOX_API_KEY", "")
        secret = os.environ.get("UPSTOX_API_SECRET", "")
        redirect = os.environ.get("UPSTOX_REDIRECT_URI", "")
        if not (key and secret and redirect):
            raise DataUnavailable(
                "Missing Upstox credentials. Set UPSTOX_API_KEY, UPSTOX_API_SECRET, "
                "UPSTOX_REDIRECT_URI in .env (see .env.example)."
            )
        return cls(key, secret, redirect, token_store)

    def authorize_url(self, state: str = "investai") -> str:
        q = urlencode({
            "client_id": self.api_key,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        })
        return f"{API}/login/authorization/dialog?{q}"

    def exchange_code(self, code: str) -> dict:
        resp = requests.post(
            f"{API}/login/authorization/token",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
            data={
                "code": code,
                "client_id": self.api_key,
                "client_secret": self.api_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise DataUnavailable(f"Token exchange failed [{resp.status_code}]: {resp.text}")
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise DataUnavailable(f"No access_token in response: {payload}")
        record = {
            "access_token": token,
            "obtained_at": dt.datetime.now().isoformat(timespec="seconds"),
            "trading_date": dt.date.today().isoformat(),
        }
        self.token_store.write_text(json.dumps(record), encoding="utf-8")
        try:
            os.chmod(self.token_store, 0o600)
        except OSError:
            pass
        return record

    def load_token(self) -> str | None:
        if not self.token_store.exists():
            return None
        try:
            rec = json.loads(self.token_store.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return rec.get("access_token")

    def token_age(self) -> str | None:
        if not self.token_store.exists():
            return None
        rec = json.loads(self.token_store.read_text(encoding="utf-8"))
        return rec.get("trading_date")


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
class UpstoxAdapter(DataAdapter):
    name = "upstox"
    feed_type = "realtime"
    classification = "REAL_MARKET_DATA"

    def __init__(self, cfg):
        self.cfg = cfg
        self.token_store = cfg.path("token_store")
        self.instruments_cache = cfg.path("instruments_cache")
        self.ttl_hours = float(cfg.get("data", "instruments_cache_hours", default=24))
        self._token: str | None = None
        try:
            self._auth = UpstoxAuth.from_env(self.token_store)
            self._token = self._auth.load_token()
        except DataUnavailable:
            self._auth = None

    # ---- session ---------------------------------------------------------- #
    def _headers(self) -> dict:
        if not self._token:
            raise DataUnavailable("No Upstox access token. Run: investai auth")
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    def is_ready(self) -> bool:
        return bool(self._token)

    def validate(self) -> bool:
        """Hit a cheap authenticated endpoint to confirm the token works today."""
        try:
            r = requests.get(f"{API}/user/profile", headers=self._headers(), timeout=20)
            return r.status_code == 200
        except (requests.RequestException, DataUnavailable):
            return False

    # ---- universe --------------------------------------------------------- #
    def resolve_universe(self, seed: list[str] | None) -> list[Instrument]:
        instruments, missing = resolve_universe(self.instruments_cache, seed, self.ttl_hours)
        if missing:
            log(f"[universe] {len(missing)} seed symbols not found on NSE: {missing}")
        return instruments

    # ---- history ---------------------------------------------------------- #
    def fetch_history(self, instrument: Instrument, interval: str, days: int) -> pd.DataFrame:
        if interval not in _INTERVALS:
            raise ValueError(f"interval {interval!r} not in {_INTERVALS}")
        to_date = dt.date.today()
        from_date = to_date - dt.timedelta(days=days)
        key = quote(instrument.instrument_key, safe="")
        url = (f"{API}/historical-candle/{key}/{interval}/"
               f"{to_date.isoformat()}/{from_date.isoformat()}")
        try:
            r = requests.get(url, headers=self._headers(), timeout=30)
        except requests.RequestException as e:
            log(f"[history] {instrument.symbol}: request error {e}")
            return _empty_ohlcv()
        if r.status_code != 200:
            log(f"[history] {instrument.symbol}: HTTP {r.status_code} {r.text[:120]}")
            return _empty_ohlcv()
        candles = (r.json().get("data") or {}).get("candles") or []
        return _candles_to_frame(candles)

    # ---- quotes ----------------------------------------------------------- #
    def last_prices(self, instruments: list[Instrument]) -> dict[str, float]:
        out: dict[str, float] = {}
        keys = [i.instrument_key for i in instruments]
        for chunk in _chunks(keys, 480):
            url = f"{API}/market-quote/ltp?" + urlencode({"instrument_key": ",".join(chunk)})
            try:
                r = requests.get(url, headers=self._headers(), timeout=30)
            except requests.RequestException as e:
                log(f"[ltp] request error {e}")
                continue
            if r.status_code != 200:
                log(f"[ltp] HTTP {r.status_code} {r.text[:120]}")
                continue
            data = (r.json().get("data") or {})
            # Response is keyed by "<segment>:<symbol>"; match back via instrument_token.
            for val in data.values():
                token = val.get("instrument_token")
                price = val.get("last_price")
                if token and price is not None:
                    out[token] = float(price)
        return out


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).astype(float)


def _candles_to_frame(candles: list[list]) -> pd.DataFrame:
    """Upstox candles are [ts, open, high, low, close, volume, oi], newest-first."""
    if not candles:
        return _empty_ohlcv()
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume", "oi"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"]).set_index("ts").sort_index()  # -> ascending
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["open", "high", "low", "close", "volume"]]


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
