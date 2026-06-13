"""Free, survivorship-free NSE price history from the official daily Bhavcopy.

Each day's Bhavcopy lists EVERY equity that traded that day (incl. names later
delisted), so a history assembled from them is survivorship-free by construction.

Two file formats are handled:
  * UDiFF (>= 2024-07-08): BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
  * legacy (< 2024-07-08): cmDDMONYYYYbhav.csv.zip

Corporate actions: Bhavcopy prices are UNADJUSTED, but each row carries the
official previous close (already adjusted for splits/bonuses/rights on ex-dates).
So the ratio prevclose[t] / close[t-1] is ~1 on normal days and the exact
adjustment factor on ex-dates — we back-adjust closes from that, with NO separate
corporate-actions feed. (Ordinary dividends are NOT in prevclose, so adj_close
here is split/bonus-adjusted, not total-return — a small, documented limitation.)
"""
from __future__ import annotations

import datetime as dt
import io
import time
import zipfile

import numpy as np
import pandas as pd
import requests

from .._log import log
from .pricestore import connect

UDIFF_CUTOVER = dt.date(2024, 7, 8)
_HOST = "https://nsearchives.nseindia.com"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS bhav_raw (
    symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
    prevclose REAL, volume REAL, isin TEXT, PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS bhav_days (date TEXT PRIMARY KEY, rows INTEGER);
"""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:
        s.get("https://www.nseindia.com/", timeout=20)   # warm cookies
    except requests.RequestException:
        pass
    return s


def _url(d: dt.date) -> str:
    if d >= UDIFF_CUTOVER:
        return f"{_HOST}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    mon = d.strftime("%b").upper()
    return f"{_HOST}/content/historical/EQUITIES/{d.year}/{mon}/cm{d.day:02d}{mon}{d.year}bhav.csv.zip"


def _parse(content: bytes, d: dt.date) -> pd.DataFrame | None:
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        raw = z.read(z.namelist()[0]).decode("latin1")
    except (zipfile.BadZipFile, KeyError):
        return None
    df = pd.read_csv(io.StringIO(raw))
    df.columns = [c.strip() for c in df.columns]
    if "TckrSymb" in df.columns:                          # UDiFF
        df = df[df.get("SctySrs", "").astype(str).str.strip() == "EQ"]
        out = pd.DataFrame({
            "symbol": df["TckrSymb"].astype(str).str.strip(),
            "open": df["OpnPric"], "high": df["HghPric"], "low": df["LwPric"],
            "close": df["ClsPric"], "prevclose": df["PrvsClsgPric"],
            "volume": df["TtlTradgVol"], "isin": df["ISIN"].astype(str).str.strip(),
        })
    else:                                                 # legacy
        df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
        out = pd.DataFrame({
            "symbol": df["SYMBOL"].astype(str).str.strip(),
            "open": df["OPEN"], "high": df["HIGH"], "low": df["LOW"],
            "close": df["CLOSE"], "prevclose": df["PREVCLOSE"],
            "volume": df["TOTTRDQTY"], "isin": df["ISIN"].astype(str).str.strip(),
        })
    out["date"] = d.isoformat()
    return out.dropna(subset=["close"])


def download_day(session: requests.Session, d: dt.date,
                 retries: int = 2) -> pd.DataFrame | None:
    """Return a day's EQ rows, or None if it's a holiday / unavailable."""
    url = _url(d)
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=30)
        except requests.RequestException:
            time.sleep(0.5 * (attempt + 1))
            continue
        if r.status_code == 200 and r.content[:2] == b"PK":
            return _parse(r.content, d)
        if r.status_code == 404:
            return None                                   # holiday / not published
        time.sleep(0.5 * (attempt + 1))
    return None


def ingest_bhavcopy(cfg, start: str, end: str | None = None,
                    pause: float = 0.15) -> dict:
    """Download every trading day's Bhavcopy in [start, end] into the raw store.
    Resumable: days already in `bhav_days` are skipped."""
    conn = connect(cfg)
    conn.executescript(_RAW_SCHEMA)
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end) if end else dt.date.today()
    done = {r[0] for r in conn.execute("SELECT date FROM bhav_days").fetchall()}
    session = make_session()

    d, fetched, total_rows = start_d, 0, 0
    while d <= end_d:
        iso = d.isoformat()
        if d.weekday() >= 5 or iso in done:               # skip weekends/done
            d += dt.timedelta(days=1)
            continue
        day = download_day(session, d)
        if day is not None and not day.empty:
            recs = [(r.symbol, r.date, r.open, r.high, r.low, r.close,
                     r.prevclose, r.volume, r.isin) for r in day.itertuples(index=False)]
            conn.executemany("INSERT OR REPLACE INTO bhav_raw VALUES (?,?,?,?,?,?,?,?,?)", recs)
            total_rows += len(recs)
        conn.execute("INSERT OR REPLACE INTO bhav_days VALUES (?,?)",
                     (iso, 0 if day is None else len(day)))
        conn.commit()
        fetched += 1
        if fetched % 100 == 0:
            log(f"[bhavcopy] {iso}: {fetched} days fetched, {total_rows} rows this run")
        time.sleep(pause)
        d += dt.timedelta(days=1)

    summary = {
        "range": f"{start} -> {end_d.isoformat()}",
        "days_fetched_this_run": fetched,
        "rows_added_this_run": total_rows,
        "days_in_store": conn.execute("SELECT COUNT(*) FROM bhav_days").fetchone()[0],
        "raw_rows_in_store": conn.execute("SELECT COUNT(*) FROM bhav_raw").fetchone()[0],
    }
    conn.close()
    return summary


# Plausible split/bonus price-ratios (post/pre) that move price beyond NSE's ~20%
# circuit band, so a gap this large is a corporate action, not a genuine move.
# NSE's `prevclose` is NOT split-adjusted, so we detect from the OPEN gap instead.
_CLEAN_CA = [
    0.60, 0.50, 0.40, 0.375, 0.3333, 0.3125, 0.30, 0.25, 0.20, 0.1667,
    0.125, 0.10, 0.0833, 0.0667, 0.05,                 # splits / large bonuses
    1.50, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0,          # reverse splits
]
_CA_LO, _CA_HI = 0.62, 1.60                            # gate: beyond genuine daily moves


def adjust_factors(close: np.ndarray, open_: np.ndarray, tol: float = 0.04) -> np.ndarray:
    """Per-bar corporate-action factor from the OPEN gap (open[t]/close[t-1]).

    Genuine single-day moves are capped near 20% by NSE circuits (worst real cases
    ~ -30%), so ANY open gap beyond the ~38% gate is treated as a corporate action:
    snapped to the nearest clean split/bonus ratio when close, otherwise the raw gap
    is used (catches odd/combined ratios + data errors). Moves inside the gate
    (e.g. an Adani -28% day) are left untouched.
    """
    n = len(close)
    factors = np.ones(n)
    for t in range(1, n):
        c0, o = close[t - 1], open_[t]
        if c0 and c0 > 0 and o and o > 0:
            g = o / c0
            if g <= _CA_LO or g >= _CA_HI:
                best = min(_CLEAN_CA, key=lambda r: abs(g - r))
                factors[t] = best if abs(g - best) <= tol * best else g
    return factors


def build_adjusted(cfg) -> dict:
    """From bhav_raw, build split/bonus-adjusted closes into the `prices` table."""
    conn = connect(cfg)
    raw = pd.read_sql_query(
        "SELECT symbol, date, open, close, volume, isin FROM bhav_raw", conn,
        parse_dates=["date"])
    if raw.empty:
        conn.close()
        raise RuntimeError("bhav_raw empty — run ingest-bhavcopy first.")
    conn.execute("DELETE FROM prices")
    n_sym, n_rows = 0, 0
    for sym, g in raw.groupby("symbol"):
        g = g.sort_values("date")
        close = g["close"].to_numpy(dtype=float)
        opn = g["open"].to_numpy(dtype=float)
        factors = adjust_factors(close, opn)
        adj = np.empty(len(close))
        running = 1.0
        for t in range(len(close) - 1, -1, -1):
            adj[t] = close[t] * running
            running *= factors[t]
        rows = [(sym, d.date().isoformat(), float(a), float(c), float(v))
                for d, a, c, v in zip(g["date"], adj, close, g["volume"].to_numpy(float))]
        conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?)", rows)
        isin = g["isin"].iloc[-1] if "isin" in g else ""
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?)",
                     (sym, "", "EQ", 0, isin))
        n_sym += 1
        n_rows += len(rows)
    conn.commit()
    summary = {"symbols_adjusted": n_sym, "price_rows": n_rows,
               "splits_bonuses_detected": int(_count_cas(raw))}
    conn.close()
    return summary


def _count_cas(raw: pd.DataFrame) -> int:
    total = 0
    for _, g in raw.groupby("symbol"):
        g = g.sort_values("date")
        f = adjust_factors(g["close"].to_numpy(float), g["open"].to_numpy(float))
        total += int((f != 1.0).sum())
    return total
