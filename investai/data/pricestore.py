"""Source-neutral local price store (SQLite) shared by all ingesters.

Holds a survivorship-free price history. `prices` is the canonical table the
backtester reads (via build_frames); ingesters (EODHD, NSE bhavcopy) populate it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT, date TEXT, adj_close REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS meta (
    symbol TEXT PRIMARY KEY, name TEXT, type TEXT, delisted INTEGER, isin TEXT
);
CREATE TABLE IF NOT EXISTS ingested (symbol TEXT PRIMARY KEY, bars INTEGER, at TEXT);
"""


def store_path(cfg) -> Path:
    return cfg.path("db").parent / "prices.db"


def connect(cfg) -> sqlite3.Connection:
    conn = sqlite3.connect(store_path(cfg))
    conn.executescript(_SCHEMA)
    return conn


def build_frames(cfg, start: str | None = None, min_bars: int = 260) -> dict:
    """Per-symbol frames from the store: 'close' (=adjusted_close, for returns)
    and 'turnover' (=close*volume, a liquidity proxy for the point-in-time universe)."""
    conn = connect(cfg)
    q = "SELECT symbol, date, adj_close, close, volume FROM prices"
    if start:
        q += f" WHERE date >= '{start}'"
    df = pd.read_sql_query(q, conn, parse_dates=["date"])
    conn.close()
    frames = {}
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("date").set_index("date")
        if len(g) < min_bars:
            continue
        out = pd.DataFrame(index=g.index)
        out["close"] = g["adj_close"].astype(float)
        out["turnover"] = g["close"].astype(float) * g["volume"].astype(float)
        frames[sym] = out
    return frames
