"""EODHD data client + NSE ingestion into a local SQLite cache.

Purpose: a SURVIVORSHIP-FREE price history for the full NSE cross-section,
including delisted names, so factor backtests aren't biased by today's winners.
Uses adjusted_close (split+dividend adjusted) for returns and close*volume for a
liquidity (turnover) proxy used to define a point-in-time universe.

Needs an EODHD subscription that includes delisted data + an API key in
EODHD_API_KEY. Pull once, cache locally, re-use offline.
"""
from __future__ import annotations

import time

import requests

from .._log import log
from .pricestore import build_frames, connect as _connect, store_path  # noqa: F401 (re-exported)

BASE = "https://eodhd.com/api"


class EODHDClient:
    def __init__(self, api_key: str, session=None, min_interval: float = 0.05,
                 max_retries: int = 3):
        if not api_key:
            raise RuntimeError("EODHD_API_KEY not set")
        self.api_key = api_key
        self.session = session or requests.Session()
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last = 0.0

    def _get(self, path: str, params: dict):
        params = {**params, "api_token": self.api_key, "fmt": "json"}
        backoff = 0.6
        for attempt in range(1, self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(f"{BASE}/{path}", params=params, timeout=40)
            except requests.RequestException as e:
                if attempt == self.max_retries:
                    raise RuntimeError(f"EODHD request failed: {e}")
                time.sleep(backoff); backoff *= 2; continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 503):
                time.sleep(backoff * 2); backoff *= 2; continue
            raise RuntimeError(f"EODHD HTTP {r.status_code}: {r.text[:160]}")
        raise RuntimeError("EODHD retries exhausted")

    def list_symbols(self, exchange: str = "NSE", delisted: bool = False) -> list[dict]:
        params = {"delisted": 1} if delisted else {}
        data = self._get(f"exchange-symbol-list/{exchange}", params)
        return data if isinstance(data, list) else []

    def eod(self, code: str, exchange: str = "NSE", start: str = "2010-01-01") -> list[dict]:
        data = self._get(f"eod/{code}.{exchange}", {"from": start, "period": "d"})
        return data if isinstance(data, list) else []


def ingest_nse(cfg, client: EODHDClient, start: str = "2010-01-01",
               types=("Common Stock",), limit: int | None = None) -> dict:
    """Pull active + delisted NSE equities into the local store. Resumable:
    symbols already in `ingested` are skipped."""
    conn = _connect(cfg)
    active = {s["Code"]: s for s in client.list_symbols("NSE", delisted=False)
              if s.get("Type") in types}
    delisted = {s["Code"]: s for s in client.list_symbols("NSE", delisted=True)
                if s.get("Type") in types}
    universe = {**active, **delisted}
    done = {r[0] for r in conn.execute("SELECT symbol FROM ingested").fetchall()}
    todo = [c for c in universe if c not in done]
    if limit:
        todo = todo[:limit]
    log(f"[eodhd] universe: {len(active)} active + {len(delisted)} delisted "
        f"= {len(universe)} unique; {len(todo)} to fetch")

    for i, code in enumerate(todo, 1):
        meta = universe[code]
        try:
            rows = client.eod(code, "NSE", start)
        except RuntimeError as e:
            log(f"[eodhd] {code}: {e}")
            continue
        recs = [(code, r["date"], r.get("adjusted_close"), r.get("close"), r.get("volume"))
                for r in rows if r.get("date") and r.get("adjusted_close") is not None]
        conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?)", recs)
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?)",
                     (code, meta.get("Name", ""), meta.get("Type", ""),
                      1 if code in delisted else 0, meta.get("Isin", "")))
        conn.execute("INSERT OR REPLACE INTO ingested VALUES (?,?,datetime('now'))",
                     (code, len(recs)))
        conn.commit()
        if i % 100 == 0:
            log(f"[eodhd] {i}/{len(todo)} symbols ingested")
    summary = {
        "active": len(active), "delisted": len(delisted),
        "fetched_this_run": len(todo),
        "symbols_in_store": conn.execute("SELECT COUNT(*) FROM ingested").fetchone()[0],
        "rows_in_store": conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0],
        "store": str(store_path(cfg)),
    }
    conn.close()
    return summary
