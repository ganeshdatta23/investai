"""Portfolio-level, event-driven backtest with research variants.

The strategy logic (indicators, scanner score, rule engine, sizing, costs) is
REUSED unchanged. Variants are expressed only as ENTRY FILTERS layered on top of
the rule engine's decision — so "existing entry/risk logic" stays byte-identical
across variants; the matrix isolates the effect of each added condition.

No-lookahead: features and the decision use data only up to the signal bar's
close; entry fills at that close; exits are evaluated on subsequent bars (exits
are processed before entries each day).

Speed: features for every (symbol, bar) are computed ONCE in prepare() and cached,
so running N variants does not recompute indicators N times.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from ..config import Config
from ..data.base import DataAdapter
from ..indicators.engine import compute_features, ema
from ..paper.tracker import performance_report
from ..reasoning.base import PortfolioContext
from ..reasoning.rule_engine import RuleEngine
from ..scanner.scanner import score_features
from ..schemas import Action, Candidate, Instrument

WARMUP_BARS = 210
FEATURE_WINDOW = 300
DEFAULT_MAX_HOLD = 40
RS_LOOKBACK = 63            # ~3 months, for relative strength vs benchmark


@dataclass
class Filters:
    """Entry gates layered on the rule engine. None/False = gate disabled."""
    name: str
    long_only: bool = False
    require_regime: bool = False        # benchmark close > benchmark 200-EMA
    min_adx: Optional[float] = None     # require features adx14 > threshold
    require_rs: bool = False            # stock RS_LOOKBACK return > benchmark's


@dataclass
class BTContext:
    frames: dict
    inst_by_symbol: dict
    pos_index: dict
    all_dates: list
    feat_cache: dict                    # sym -> {k: (features, score)}
    stock_ret: dict                     # sym -> Series(pct_change RS_LOOKBACK)
    regime_map: dict                    # Timestamp -> bool
    bench_ret_map: dict                 # Timestamp -> float
    benchmark_symbol: Optional[str]
    start: str
    end: str
    interval: str
    days: int


@dataclass
class BTTrade:
    symbol: str
    direction: str
    entry_date: str
    entry_raw: float
    entry_fill: float
    qty: int
    stop: float
    target_1: float
    per_share_risk: float
    risk_amount: float
    confidence: int
    setup_quality: str
    exit_date: Optional[str] = None
    exit_raw: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    r_multiple: float = 0.0
    hold_days: int = 0


@dataclass
class BacktestResult:
    variant_name: str
    symbols: list[str]
    start: str
    end: str
    interval: str
    starting_equity: float
    ending_equity: float
    overall: dict
    in_sample: dict
    out_of_sample: dict
    overfitting: dict
    fee_sensitivity: list[dict]
    slippage_sensitivity: list[dict]
    cagr_pct: Optional[float]
    sharpe: Optional[float]
    sortino: Optional[float]
    max_drawdown_pct: float
    benchmark_symbol: Optional[str]
    trades: list[dict] = field(default_factory=list)

    def metrics_row(self) -> dict:
        o = self.overall
        tr = round((self.ending_equity / self.starting_equity - 1) * 100, 2) \
            if self.starting_equity else None
        return {
            "variant": self.variant_name,
            "trades": o.get("trades", 0),
            "win_rate_pct": o.get("win_rate_pct"),
            "profit_factor": o.get("profit_factor"),
            "expectancy": o.get("expectancy_per_trade"),
            "cagr_pct": self.cagr_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "total_return_pct": tr,
            "ending_equity": round(self.ending_equity, 2),
        }

    def summary(self) -> dict:
        return {
            "variant": self.variant_name,
            "symbols_tested": len(self.symbols),
            "period": f"{self.start} -> {self.end}",
            "interval": self.interval,
            "benchmark": self.benchmark_symbol,
            "starting_equity": round(self.starting_equity, 2),
            "ending_equity": round(self.ending_equity, 2),
            "total_return_pct": round((self.ending_equity / self.starting_equity - 1) * 100, 2),
            "cagr_pct": self.cagr_pct,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "max_drawdown_pct": self.max_drawdown_pct,
            "overall": self.overall,
            "in_sample": self.in_sample,
            "out_of_sample": self.out_of_sample,
            "overfitting_check": self.overfitting,
            "fee_sensitivity": self.fee_sensitivity,
            "slippage_sensitivity": self.slippage_sensitivity,
        }


def _pnl(entry_raw, exit_raw, qty, direction, fee_pct, slip_pct) -> tuple[float, float]:
    slip = slip_pct / 100.0
    if direction == "long":
        entry_fill = entry_raw * (1 + slip)
        exit_fill = exit_raw * (1 - slip)
        gross = (exit_fill - entry_fill) * qty
    else:
        entry_fill = entry_raw * (1 - slip)
        exit_fill = exit_raw * (1 + slip)
        gross = (entry_fill - exit_fill) * qty
    fees = (entry_fill + exit_fill) * qty * (fee_pct / 100.0)
    return gross - fees, entry_fill


def equity_metrics(curve: list[tuple], start_equity: float) -> dict:
    if len(curve) < 3:
        return {"cagr_pct": None, "sharpe": None, "sortino": None, "max_drawdown_pct": 0.0}
    dates = [c[0] for c in curve]
    eq = [float(c[1]) for c in curve]
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] != 0]
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / n) if n else 0.0
    dstd = math.sqrt(sum(r * r for r in rets if r < 0) / n) if n else 0.0
    ann = math.sqrt(252)
    sharpe = (mean / std * ann) if std > 0 else None       # daily risk-free = 0
    sortino = (mean / dstd * ann) if dstd > 0 else None
    years = (dates[-1] - dates[0]).days / 365.25
    ending = eq[-1]
    if years > 0 and start_equity > 0:
        cagr = ((ending / start_equity) ** (1 / years) - 1) * 100 if ending > 0 else -100.0
    else:
        cagr = None
    peak, max_dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v - peak) / peak * 100.0)
    return {
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "max_drawdown_pct": round(max_dd, 2),
    }


class Backtester:
    def __init__(self, cfg: Config, adapter: DataAdapter, max_hold: int = DEFAULT_MAX_HOLD):
        self.cfg = cfg
        self.adapter = adapter
        self.costs = cfg.costs
        self.risk = cfg.risk
        self.max_hold = max_hold
        self.engine = RuleEngine(cfg)
        self.start_equity = cfg.equity

    # ------------------------------------------------------------------ #
    def prepare(self, seed, days, interval) -> BTContext:
        instruments = self.adapter.resolve_universe(seed)
        frames, inst_by_symbol = {}, {}
        for inst in instruments:
            df = self.adapter.fetch_history(inst, interval, days)
            if not df.empty and len(df) > WARMUP_BARS:
                frames[inst.symbol] = df
                inst_by_symbol[inst.symbol] = inst
        if not frames:
            raise RuntimeError("No usable history fetched for backtest.")

        pos_index = {s: {ts: k for k, ts in enumerate(df.index)} for s, df in frames.items()}
        all_dates = sorted(set().union(*[set(df.index) for df in frames.values()]))

        # Precompute features ONCE per (symbol, bar).
        feat_cache, stock_ret = {}, {}
        for s, df in frames.items():
            cache = {}
            for k in range(WARMUP_BARS, len(df)):
                window = df.iloc[max(0, k - FEATURE_WINDOW + 1): k + 1]
                f = compute_features(window)
                score, direction, reasons = score_features(f)
                f["direction"] = direction
                f["score_reasons"] = reasons
                cache[k] = (f, score)
            feat_cache[s] = cache
            stock_ret[s] = df["close"].pct_change(RS_LOOKBACK)

        regime_map, bench_ret_map, bench_sym = self._benchmark(all_dates, days, interval)

        return BTContext(
            frames=frames, inst_by_symbol=inst_by_symbol, pos_index=pos_index,
            all_dates=all_dates, feat_cache=feat_cache, stock_ret=stock_ret,
            regime_map=regime_map, bench_ret_map=bench_ret_map, benchmark_symbol=bench_sym,
            start=all_dates[0].date().isoformat(), end=all_dates[-1].date().isoformat(),
            interval=interval, days=days,
        )

    def _benchmark(self, all_dates, days, interval) -> tuple[dict, dict, Optional[str]]:
        a = self.adapter
        bench, sym = None, None
        if hasattr(a, "fetch_raw"):                       # YFinance
            bench, sym = a.fetch_raw("^NSEI", interval, days), "^NSEI"
        else:                                             # synthetic / other
            try:
                bench = a.fetch_history(Instrument("NIFTY", "SYN|NIFTY"), interval, days)
                sym = "SYN|NIFTY"
            except Exception:
                bench = None
        if bench is None or bench.empty or len(bench) <= 200:
            return {}, {}, None
        bclose = bench["close"]
        regime = (bclose > ema(bclose, 200))
        bret = bclose.pct_change(RS_LOOKBACK)
        idx = pd.DatetimeIndex(all_dates)
        regime = regime.reindex(idx, method="ffill").fillna(False)
        bret = bret.reindex(idx, method="ffill")
        return regime.to_dict(), bret.to_dict(), sym

    # ------------------------------------------------------------------ #
    def simulate(self, ctx: BTContext, filters: Filters, split: float = 0.7) -> BacktestResult:
        frames, pos_index = ctx.frames, ctx.pos_index
        equity = self.start_equity
        open_pos: dict[str, BTTrade] = {}
        closed: list[BTTrade] = []
        day_pnl: dict[str, float] = {}
        equity_curve: list[tuple] = []

        for d in ctx.all_dates:
            d_iso = d.date().isoformat()

            # 1) EXITS
            for sym in list(open_pos.keys()):
                if d not in pos_index[sym]:
                    continue
                tr = open_pos[sym]
                if d_iso <= tr.entry_date:
                    continue
                bar = frames[sym].iloc[pos_index[sym][d]]
                exit_raw, reason = self._exit_price(tr, bar)
                tr.hold_days += 1
                if exit_raw is None and tr.hold_days >= self.max_hold:
                    exit_raw, reason = float(bar["close"]), "max_hold"
                if exit_raw is not None:
                    self._close(tr, d_iso, exit_raw, reason)
                    equity += tr.pnl
                    day_pnl[d_iso] = day_pnl.get(d_iso, 0.0) + tr.pnl
                    closed.append(tr)
                    del open_pos[sym]

            # 2) ENTRIES
            self.engine.equity = equity
            portfolio = self._portfolio(equity, open_pos, day_pnl.get(d_iso, 0.0))
            for sym, df in frames.items():
                if sym in open_pos or d not in pos_index[sym]:
                    continue
                k = pos_index[sym][d]
                cached = ctx.feat_cache[sym].get(k)
                if cached is None:
                    continue
                f, score = cached
                cand = Candidate(instrument=ctx.inst_by_symbol[sym],
                                 last_price=f.get("price"), features=f, score=score)
                decision = self.engine.decide(cand, portfolio)
                if decision.action not in (Action.BUY.value, Action.SELL.value) or not decision.position_size:
                    continue
                ddir = "long" if decision.action == Action.BUY.value else "short"

                # ---- variant entry gates (do not alter rule/risk logic) ----
                if filters.long_only and ddir == "short":
                    continue
                if filters.require_regime and not ctx.regime_map.get(d, False):
                    continue
                if filters.min_adx is not None:
                    av = f.get("adx14")
                    if av is None or av <= filters.min_adx:
                        continue
                if filters.require_rs:
                    sr = ctx.stock_ret[sym].iloc[k]
                    br = ctx.bench_ret_map.get(d)
                    if sr is None or pd.isna(sr) or br is None or pd.isna(br) or sr <= br:
                        continue

                open_pos[sym] = self._open(sym, decision, d_iso, ddir)
                portfolio = self._portfolio(equity, open_pos, day_pnl.get(d_iso, 0.0))

            equity_curve.append((d, equity + self._unrealized(open_pos, frames, pos_index, d)))

        for sym, tr in list(open_pos.items()):
            self._close(tr, frames[sym].index[-1].date().isoformat(),
                        float(frames[sym]["close"].iloc[-1]), "end_of_data")
            equity += tr.pnl
            closed.append(tr)

        return self._assemble(ctx, filters, closed, equity, equity_curve, split)

    def run(self, seed, days, interval="day", split=0.7, filters: Filters | None = None) -> BacktestResult:
        ctx = self.prepare(seed, days, interval)
        return self.simulate(ctx, filters or Filters(name="baseline"), split)

    # ------------------------------------------------------------------ #
    def _exit_price(self, tr, bar):
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if tr.direction == "long":
            if l <= tr.stop:
                return (o if o <= tr.stop else tr.stop), "stop"
            if h >= tr.target_1:
                return (o if o >= tr.target_1 else tr.target_1), "target1"
        else:
            if h >= tr.stop:
                return (o if o >= tr.stop else tr.stop), "stop"
            if l <= tr.target_1:
                return (o if o <= tr.target_1 else tr.target_1), "target1"
        return None, None

    def _open(self, sym, decision, d_iso, direction) -> BTTrade:
        _, entry_fill = _pnl(decision.entry_price, decision.entry_price,
                             decision.position_size, direction, 0.0, self.costs.slippage_pct)
        per_share_risk = abs(entry_fill - decision.stop_loss)
        return BTTrade(
            symbol=sym, direction=direction, entry_date=d_iso,
            entry_raw=decision.entry_price, entry_fill=round(entry_fill, 4),
            qty=decision.position_size, stop=decision.stop_loss, target_1=decision.target_1,
            per_share_risk=round(per_share_risk, 4),
            risk_amount=round(per_share_risk * decision.position_size, 2),
            confidence=decision.confidence, setup_quality=decision.setup_quality,
        )

    def _close(self, tr, d_iso, exit_raw, reason):
        pnl, _ = _pnl(tr.entry_raw, exit_raw, tr.qty, tr.direction,
                      self.costs.fee_pct, self.costs.slippage_pct)
        tr.exit_date, tr.exit_raw, tr.exit_reason = d_iso, round(exit_raw, 4), reason
        tr.pnl = round(pnl, 2)
        tr.r_multiple = round(pnl / tr.risk_amount, 3) if tr.risk_amount else 0.0

    def _portfolio(self, equity, open_pos, today_pnl) -> PortfolioContext:
        open_risk_pct = sum(t.risk_amount for t in open_pos.values()) / equity * 100.0 if equity else 0.0
        return PortfolioContext(
            equity=equity, open_positions=len(open_pos), open_risk_pct=round(open_risk_pct, 4),
            daily_realized_loss_pct=round(max(0.0, -today_pnl) / equity * 100.0, 4) if equity else 0.0,
            open_symbols=set(open_pos.keys()), sector_counts={},
        )

    def _unrealized(self, open_pos, frames, pos_index, d) -> float:
        total = 0.0
        for sym, tr in open_pos.items():
            if d in pos_index[sym]:
                c = float(frames[sym].iloc[pos_index[sym][d]]["close"])
                total += (c - tr.entry_fill) * tr.qty * (1 if tr.direction == "long" else -1)
        return total

    # ------------------------------------------------------------------ #
    def _assemble(self, ctx, filters, closed, ending_equity, equity_curve, split) -> BacktestResult:
        rows = [{"pnl": t.pnl, "r_multiple": t.r_multiple, "hold_days": t.hold_days} for t in closed]
        overall = performance_report(rows)
        cut = ctx.all_dates[int(len(ctx.all_dates) * split)].date().isoformat() \
            if len(ctx.all_dates) >= 2 else ctx.end
        in_sample = performance_report([r for r, t in zip(rows, closed) if t.entry_date <= cut])
        out_of_sample = performance_report([r for r, t in zip(rows, closed) if t.entry_date > cut])
        em = equity_metrics(equity_curve, self.start_equity)
        return BacktestResult(
            variant_name=filters.name, symbols=list(ctx.frames.keys()),
            start=ctx.start, end=ctx.end, interval=ctx.interval,
            starting_equity=self.start_equity, ending_equity=ending_equity,
            overall=overall, in_sample=in_sample, out_of_sample=out_of_sample,
            overfitting=self._overfitting(in_sample, out_of_sample, cut),
            fee_sensitivity=self._sensitivity(closed, "fee"),
            slippage_sensitivity=self._sensitivity(closed, "slippage"),
            cagr_pct=em["cagr_pct"], sharpe=em["sharpe"], sortino=em["sortino"],
            max_drawdown_pct=em["max_drawdown_pct"], benchmark_symbol=ctx.benchmark_symbol,
            trades=[t.__dict__ for t in closed],
        )

    @staticmethod
    def _overfitting(is_m, oos_m, cut) -> dict:
        flags = []
        if oos_m.get("trades", 0) < 10:
            flags.append("insufficient_oos_trades")
        if (oos_m.get("profit_factor") or 0) < 1.0:
            flags.append("oos_unprofitable")
        ie, oe = is_m.get("expectancy_per_trade"), oos_m.get("expectancy_per_trade")
        if ie is not None and oe is not None and ie > 0 and oe < 0.5 * ie:
            flags.append("oos_expectancy_decayed_>50pct")
        return {"split_date": cut, "risk": "high" if flags else "low", "flags": flags}

    def _sensitivity(self, closed, kind) -> list[dict]:
        base_fee, base_slip = self.costs.fee_pct, self.costs.slippage_pct
        out = []
        for mult in (0.0, 1.0, 2.0, 3.0):
            fee = base_fee * mult if kind == "fee" else base_fee
            slip = base_slip * mult if kind == "slippage" else base_slip
            pnls = [_pnl(t.entry_raw, t.exit_raw, t.qty, t.direction, fee, slip)[0]
                    for t in closed if t.exit_raw is not None]
            if not pnls:
                continue
            wins = sum(p for p in pnls if p > 0)
            losses = -sum(p for p in pnls if p <= 0)
            out.append({
                "multiplier": mult, "fee_pct": round(fee, 4), "slippage_pct": round(slip, 4),
                "expectancy": round(sum(pnls) / len(pnls), 2),
                "profit_factor": round(wins / losses, 2) if losses > 0 else None,
                "total_pnl": round(sum(pnls), 2),
            })
        return out
