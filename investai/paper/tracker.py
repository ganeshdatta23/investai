"""Outcome metrics over closed paper trades."""
from __future__ import annotations

from typing import Any, Sequence


def performance_report(closed: Sequence[Any]) -> dict:
    """Compute the standard trade-analytics block from closed trade rows.

    Each row needs: pnl, r_multiple, hold_days (sqlite3.Row or mapping-like)."""
    trades = [dict(r) for r in closed]
    n = len(trades)
    if n == 0:
        return {"trades": 0, "note": "No closed trades yet."}

    pnls = [t["pnl"] or 0.0 for t in trades]
    rs = [t["r_multiple"] or 0.0 for t in trades]
    holds = [t["hold_days"] or 0.0 for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)  # positive number

    win_rate = len(wins) / n * 100.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    expectancy = sum(pnls) / n
    avg_r = sum(rs) / n
    avg_win = (gross_win / len(wins)) if wins else 0.0
    avg_loss = (-gross_loss / len(losses)) if losses else 0.0

    # Max drawdown on the realized equity curve (cumulative P&L).
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    return {
        "trades": n,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "expectancy_per_trade": round(expectancy, 2),
        "avg_r_multiple": round(avg_r, 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "total_pnl": round(sum(pnls), 2),
        "max_drawdown": round(max_dd, 2),
        "avg_hold_days": round(sum(holds) / n, 2),
        "wins": len(wins),
        "losses": len(losses),
    }
