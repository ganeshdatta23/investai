"""Simple, cross-platform scan loop.

Uses a plain sleep loop (no APScheduler/tzdata dependency, which is fragile on
Windows). IST is computed as a fixed UTC+5:30 offset so market-hours gating works
without timezone data files. NSE cash session: 09:15-15:30 IST, Mon-Fri.
"""
from __future__ import annotations

import datetime as dt
import json
import time

from .config import Config
from .pipeline import run_scan

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SESSION_OPEN = dt.time(9, 15)
SESSION_CLOSE = dt.time(15, 30)


def market_is_open(now_utc: dt.datetime | None = None) -> bool:
    now = (now_utc or dt.datetime.now(dt.timezone.utc)).astimezone(IST)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return SESSION_OPEN <= now.time() <= SESSION_CLOSE


def _summary(result: dict) -> str:
    n_op = len(result.get("top_opportunities", []))
    closed = result.get("closed_this_run", [])
    return (f"[{result['scan_timestamp']}] status={result['status']} "
            f"regime={result.get('market_regime')} opportunities={n_op} "
            f"closed={len(closed)}")


def run_loop(cfg: Config, offline: bool = False, interval_minutes: float = 5.0,
             once: bool = False, market_hours_only: bool = True) -> None:
    print(f"[scheduler] interval={interval_minutes}m offline={offline} "
          f"market_hours_only={market_hours_only}. Ctrl-C to stop.")
    while True:
        if market_hours_only and not market_is_open():
            print(f"[scheduler] {dt.datetime.now(IST):%Y-%m-%d %H:%M IST} market closed — skipping.")
        else:
            try:
                result = run_scan(cfg, offline=offline)
                print(_summary(result))
                for op in result.get("top_opportunities", []):
                    print("  ->", json.dumps(op, separators=(",", ":")))
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                print(f"[scheduler] scan error: {type(e).__name__}: {e}")
        if once:
            break
        time.sleep(max(1.0, interval_minutes * 60.0))
