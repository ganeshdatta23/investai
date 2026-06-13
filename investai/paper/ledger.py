"""SQLite paper-trade ledger.

Records every decision (audit trail) and simulates execution for BUY/SELL
decisions: entry/exit fills include slippage, realized P&L is net of fees, and
each closed trade carries its R-multiple. Mark-to-market closes positions when
the last price breaches the stop or reaches target_1.

This is SIMULATION ONLY. No real orders. Live execution requires human approval
and is out of scope for this module by design.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from ..reasoning.base import PortfolioContext
from ..schemas import Action, Decision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, trade_date TEXT, symbol TEXT, action TEXT,
    confidence INTEGER, setup_quality TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, instrument_key TEXT, direction TEXT, sector TEXT,
    qty INTEGER, entry_price REAL, fill_price REAL, stop_loss REAL,
    target_1 REAL, target_2 REAL, per_share_risk REAL, risk_amount REAL,
    risk_pct REAL, confidence INTEGER, setup_quality TEXT,
    status TEXT, opened_at TEXT, opened_date TEXT,
    closed_at TEXT, closed_date TEXT, exit_price REAL, exit_reason TEXT,
    pnl REAL, r_multiple REAL, hold_days REAL, rationale TEXT
);
"""


class PaperLedger:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db_path: Path = cfg.path("db")
        self.costs = cfg.costs
        self.equity = cfg.equity
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ---- audit ----------------------------------------------------------- #
    def record_decision(self, d: Decision) -> None:
        now = dt.datetime.now()
        self.conn.execute(
            "INSERT INTO decisions (ts, trade_date, symbol, action, confidence, "
            "setup_quality, payload) VALUES (?,?,?,?,?,?,?)",
            (now.isoformat(timespec="seconds"), now.date().isoformat(), d.symbol,
             d.action, d.confidence, d.setup_quality, json.dumps(d.to_dict())),
        )
        self.conn.commit()

    # ---- open ------------------------------------------------------------ #
    def open_trade(self, d: Decision, instrument_key: str, sector: str = "") -> int | None:
        if d.action not in (Action.BUY.value, Action.SELL.value):
            return None
        if not d.position_size or d.entry_price is None or d.stop_loss is None:
            return None
        direction = "long" if d.action == Action.BUY.value else "short"
        slip = self.costs.slippage_pct / 100.0
        fill = d.entry_price * (1 + slip) if direction == "long" else d.entry_price * (1 - slip)
        per_share_risk = abs(fill - d.stop_loss)
        risk_amount = per_share_risk * d.position_size
        now = dt.datetime.now()
        cur = self.conn.execute(
            "INSERT INTO trades (symbol, instrument_key, direction, sector, qty, "
            "entry_price, fill_price, stop_loss, target_1, target_2, per_share_risk, "
            "risk_amount, risk_pct, confidence, setup_quality, status, opened_at, "
            "opened_date, rationale) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.symbol, instrument_key, direction, sector, d.position_size,
             d.entry_price, round(fill, 2), d.stop_loss, d.target_1, d.target_2,
             round(per_share_risk, 4), round(risk_amount, 2), d.risk_per_trade_percent,
             d.confidence, d.setup_quality, "OPEN", now.isoformat(timespec="seconds"),
             now.date().isoformat(), d.decision_rationale),
        )
        self.conn.commit()
        return cur.lastrowid

    # ---- mark-to-market / close ----------------------------------------- #
    def mark_to_market(self, price_map: dict[str, float]) -> list[dict]:
        """Close any open trade whose last price breached its stop or reached T1.
        `price_map` maps instrument_key -> last price. Returns closed-trade rows."""
        closed = []
        for row in self.open_positions():
            price = price_map.get(row["instrument_key"])
            if price is None:
                continue
            reason = None
            exit_price = None
            if row["direction"] == "long":
                if price <= row["stop_loss"]:
                    reason, exit_price = "stop", row["stop_loss"]
                elif row["target_1"] is not None and price >= row["target_1"]:
                    reason, exit_price = "target1", row["target_1"]
            else:
                if price >= row["stop_loss"]:
                    reason, exit_price = "stop", row["stop_loss"]
                elif row["target_1"] is not None and price <= row["target_1"]:
                    reason, exit_price = "target1", row["target_1"]
            if reason and exit_price is not None:
                closed.append(self.close_trade(row["id"], float(exit_price), reason))
        return closed

    def close_trade(self, trade_id: int, exit_price: float, reason: str) -> dict:
        row = self.conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row is None or row["status"] != "OPEN":
            raise ValueError(f"trade {trade_id} not open")
        direction = row["direction"]
        qty = row["qty"]
        slip = self.costs.slippage_pct / 100.0
        exit_fill = exit_price * (1 - slip) if direction == "long" else exit_price * (1 + slip)
        gross = (exit_fill - row["fill_price"]) * qty
        if direction == "short":
            gross = -gross
        # Round-trip fees on traded notional.
        fees = (row["fill_price"] + exit_fill) * qty * (self.costs.fee_pct / 100.0)
        pnl = gross - fees
        r_multiple = pnl / row["risk_amount"] if row["risk_amount"] else 0.0
        now = dt.datetime.now()
        opened = dt.datetime.fromisoformat(row["opened_at"])
        hold_days = round((now - opened).total_seconds() / 86400.0, 3)
        self.conn.execute(
            "UPDATE trades SET status='CLOSED', closed_at=?, closed_date=?, "
            "exit_price=?, exit_reason=?, pnl=?, r_multiple=?, hold_days=? WHERE id=?",
            (now.isoformat(timespec="seconds"), now.date().isoformat(),
             round(exit_fill, 2), reason, round(pnl, 2), round(r_multiple, 3),
             hold_days, trade_id),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone())

    # ---- queries --------------------------------------------------------- #
    def open_positions(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()

    def closed_trades(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM trades WHERE status='CLOSED' ORDER BY closed_at").fetchall()

    def portfolio_context(self) -> PortfolioContext:
        equity = self.equity
        opens = self.open_positions()
        open_risk_pct = sum((r["risk_pct"] or 0.0) for r in opens)
        symbols = {r["symbol"] for r in opens}
        sectors: dict[str, int] = {}
        for r in opens:
            sec = r["sector"] or ""
            if sec:
                sectors[sec] = sectors.get(sec, 0) + 1
        today = dt.date.today().isoformat()
        day_pnl = self.conn.execute(
            "SELECT COALESCE(SUM(pnl),0) AS p FROM trades "
            "WHERE status='CLOSED' AND closed_date=?", (today,)).fetchone()["p"]
        daily_loss_pct = max(0.0, -day_pnl) / equity * 100.0 if equity else 0.0
        return PortfolioContext(
            equity=equity,
            open_positions=len(opens),
            open_risk_pct=round(open_risk_pct, 4),
            daily_realized_loss_pct=round(daily_loss_pct, 4),
            open_symbols=symbols,
            sector_counts=sectors,
        )

    def close(self) -> None:
        self.conn.close()
