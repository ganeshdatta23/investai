"""Orchestrates one scan cycle:

  mark-to-market open positions  ->  scan universe  ->  reason over candidates
  ->  (paper) open compliant trades  ->  assemble ranked opportunities.

Carefully distinguishes three states the user asked to keep separate:
  * data_unavailable  — the feed failed; no decisions produced.
  * no_opportunities  — feed worked, nothing passed the filters.
  * opportunities     — one or more actionable setups.
"""
from __future__ import annotations

import datetime as dt

from ._log import log
from .config import Config
from .data.base import DataAdapter, DataUnavailable
from .data.synthetic import SyntheticAdapter
from .data.upstox import UpstoxAdapter
from .reasoning.base import ReasoningEngine
from .reasoning.rule_engine import RuleEngine
from .scanner.scanner import Scanner
from .schemas import Action, Instrument
from .paper.ledger import PaperLedger
from .paper.tracker import performance_report


def build_adapter(cfg: Config, offline: bool) -> DataAdapter:
    """Adapter priority: synthetic (if --offline) -> Upstox (if authenticated)
    -> YFinance fallback. No user intervention required for the switch."""
    if offline:
        return SyntheticAdapter(cfg)
    upstox = UpstoxAdapter(cfg)
    if upstox.is_ready():
        return upstox
    from .data.yfinance_adapter import YFinanceAdapter
    log("[adapter] Upstox not authenticated -> automatic fallback to "
        "YFinanceAdapter (real, ~15-min delayed NSE data).")
    return YFinanceAdapter(cfg)


def build_reasoner(cfg: Config) -> ReasoningEngine:
    engine = str(cfg.get("reasoning", "engine", default="rule")).lower()
    if engine == "anthropic":
        try:
            from .reasoning.anthropic_engine import AnthropicEngine
            log("[reasoning] EXPERIMENTAL anthropic engine (unverified path).")
            return AnthropicEngine(cfg)
        except Exception as e:  # noqa: BLE001 - fall back rather than crash the loop
            log(f"[reasoning] anthropic engine unavailable ({e}); using rule engine.")
    return RuleEngine(cfg)


def _market_regime(candidates) -> str:
    if not candidates:
        return "unknown"
    up = sum(1 for c in candidates if c.features.get("uptrend_stack"))
    down = sum(1 for c in candidates if c.features.get("downtrend_stack"))
    n = len(candidates)
    pct_up = up / n * 100.0
    tone = "risk-on" if up > down else "risk-off" if down > up else "mixed"
    return f"{tone} ({pct_up:.0f}% of candidates trending up, n={n})"


def run_scan(cfg: Config, offline: bool = False, seed: list[str] | None = None) -> dict:
    adapter = build_adapter(cfg, offline)
    reasoner = build_reasoner(cfg)
    ledger = PaperLedger(cfg)
    ts = dt.datetime.now().isoformat(timespec="seconds")
    astatus = adapter.status()
    classification = adapter.classification

    def envelope(**extra) -> dict:
        base = {
            "scan_time": ts,
            "scan_timestamp": ts,            # back-compat alias
            "adapter_used": adapter.name,
            "feed_type": adapter.feed_type,
            "data_classification": classification,
            "adapter_status": astatus,
            "mode": cfg.mode,
            "data_source": adapter.name,
        }
        base.update(extra)
        return base

    if not adapter.is_ready():
        ledger.close()
        return envelope(
            status="data_unavailable", market_regime="unknown",
            stocks_scanned=0, candidates_found=0, top_opportunities=[],
            error=f"No data feed available (adapter={adapter.name}).")

    if seed is None:
        seed = cfg.get("universe", "seed", default=None)

    try:
        stocks_scanned = len(adapter.resolve_universe(seed))

        # 1) Manage existing positions first.
        opens = ledger.open_positions()
        closed_this_run = []
        if opens:
            insts = [Instrument(symbol=r["symbol"], instrument_key=r["instrument_key"])
                     for r in opens]
            prices = adapter.last_prices(insts)
            closed_this_run = ledger.mark_to_market(prices)

        # 2) Scan + rank.
        scanner = Scanner(adapter, cfg)
        candidates = scanner.scan(seed)
    except DataUnavailable as e:
        ledger.close()
        return envelope(
            status="data_unavailable", market_regime="unknown",
            stocks_scanned=0, candidates_found=0, top_opportunities=[], error=str(e))

    regime = _market_regime(candidates)

    # 3) Reason over each candidate; open compliant paper trades.
    all_decisions = []
    actionable = []
    paper_mode = cfg.mode in ("PAPER", "RESEARCH")
    for cand in candidates:
        portfolio = ledger.portfolio_context()  # rebuilt each loop so risk accrues
        decision = reasoner.decide(cand, portfolio)
        ledger.record_decision(decision)
        if decision.action in (Action.BUY.value, Action.SELL.value):
            if paper_mode:
                ledger.open_trade(decision, cand.instrument.instrument_key,
                                  cand.instrument.sector)
            actionable.append(decision)
        all_decisions.append(decision)

    actionable.sort(key=lambda d: d.confidence, reverse=True)
    watchlist = sorted(
        (d for d in all_decisions if d.action == Action.HOLD.value),
        key=lambda d: d.confidence, reverse=True)

    status = "opportunities" if actionable else "no_opportunities"

    def tag(d):
        # Mark every opportunity with its data provenance (additive; no change to
        # the Decision schema or the rule-engine logic).
        return {**d.to_dict(), "data_classification": classification}

    result = envelope(
        status=status,
        market_regime=regime,
        stocks_scanned=stocks_scanned,
        passed_filters=len(candidates),
        universe_evaluated=len(candidates),     # back-compat alias
        candidates_found=len(actionable),
        closed_this_run=[
            {"symbol": c["symbol"], "exit_reason": c["exit_reason"],
             "pnl": c["pnl"], "r_multiple": c["r_multiple"]}
            for c in closed_this_run
        ],
        top_opportunities=[tag(d) for d in actionable[:5]],
        watchlist=[
            {"symbol": d.symbol, "action": d.action, "confidence": d.confidence,
             "setup_quality": d.setup_quality, "trend_state": d.trend_state,
             "reward_risk": d.reward_risk, "rationale": d.decision_rationale}
            for d in watchlist[:8]
        ],
    )
    ledger.close()
    return result


def run_report(cfg: Config) -> dict:
    ledger = PaperLedger(cfg)
    closed = ledger.closed_trades()
    opens = ledger.open_positions()
    perf = performance_report(closed)
    portfolio = ledger.portfolio_context()
    out = {
        "as_of": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": cfg.mode,
        "equity": cfg.equity,
        "open_positions": [
            {"symbol": r["symbol"], "direction": r["direction"], "qty": r["qty"],
             "fill_price": r["fill_price"], "stop_loss": r["stop_loss"],
             "target_1": r["target_1"], "risk_pct": r["risk_pct"],
             "opened_date": r["opened_date"]}
            for r in opens
        ],
        "open_risk_pct": portfolio.open_risk_pct,
        "daily_realized_loss_pct": portfolio.daily_realized_loss_pct,
        "performance": perf,
    }
    ledger.close()
    return out
