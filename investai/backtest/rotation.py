"""Cross-sectional factor rotation backtester.

A monthly-rebalanced, equal-weight, long-only portfolio that ranks a universe by
a factor (momentum, optionally pre-filtered by low volatility) and holds the top
names, with a NIFTY-200-EMA regime gate (cash in bear regimes). This is the
canonical construction for testing factor PREMIA — distinct from the ATR-stop
engine used for the (disproven) trend hypothesis.

No-lookahead: signals at bar i use data up to i's close; the new weights take
effect from i+1 (close-to-close). Turnover costs (fee+slippage) are charged at
each rebalance. Returns use raw close (ex-dividend) consistently for BOTH the
strategy and its benchmark, so the comparison is apples-to-apples.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..indicators.engine import ema
from .engine import equity_metrics


@dataclass
class RotationConfig:
    name: str
    mom_lookback: int = 252        # formation window (trading days)
    mom_skip: int = 21             # skip most recent month (reversal)
    vol_lookback: int = 252
    lowvol_keep: Optional[int] = None   # pre-filter to N lowest-vol before momentum
    top_n: int = 30
    rebalance_days: int = 21
    require_regime: bool = True
    # Point-in-time survivorship-free universe: if set, each rebalance first
    # restricts to the top-K names by trailing-average turnover AS OF that date
    # (requires a 'turnover' column in the frames).
    univ_top_turnover: Optional[int] = None
    turnover_lookback: int = 63
    # Capital-gains tax drag (India): applied to realized GAINS at each rebalance.
    # 0 disables. STCG (<365d) vs LTCG (>=365d) chosen by holding period.
    stcg_rate: float = 0.0
    ltcg_rate: float = 0.0


def _panel(frames: dict) -> pd.DataFrame:
    return pd.DataFrame({s: df["close"] for s, df in frames.items()}).sort_index().ffill()


def stability(eq_curve: list[tuple], win: int = 252) -> dict:
    """Rolling-window robustness: calendar-year returns + how consistently a
    1-year holding window was profitable (the real test for a parameter-free
    strategy — is the edge there across regimes, not just one lucky split?)."""
    if len(eq_curve) < win + 5:
        return {}
    s = pd.Series([v for _, v in eq_curve],
                  index=pd.DatetimeIndex([d for d, _ in eq_curve]))
    yearly = {int(y): round((g.iloc[-1] / g.iloc[0] - 1) * 100, 2)
              for y, g in s.groupby(s.index.year) if len(g) > 1}
    roll = s.pct_change(win).dropna()
    return {
        "per_year_return_pct": yearly,
        "rolling_1y_pct_positive": round((roll > 0).mean() * 100, 1),
        "rolling_1y_median_pct": round(roll.median() * 100, 2),
        "rolling_1y_worst_pct": round(roll.min() * 100, 2),
    }


def _trade_stats(rets: list[float]) -> dict:
    if not rets:
        return {"trades": 0, "win_rate_pct": None, "profit_factor": None, "expectancy_pct": None}
    wins = [r for r in rets if r > 0]
    gl = -sum(r for r in rets if r <= 0)
    gw = sum(wins)
    return {
        "trades": len(rets),
        "win_rate_pct": round(len(wins) / len(rets) * 100, 2),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "expectancy_pct": round(sum(rets) / len(rets) * 100, 3),
    }


class RotationBacktester:
    def __init__(self, cfg):
        self.start_equity = cfg.equity
        self.fee = cfg.costs.fee_pct / 100.0
        self.slip = cfg.costs.slippage_pct / 100.0

    def run(self, frames: dict, benchmark: Optional[pd.DataFrame],
            rc: RotationConfig, split: float = 0.6) -> dict:
        panel = _panel(frames)
        dates = panel.index
        rets = panel.pct_change()
        mom = panel.shift(rc.mom_skip).pct_change(rc.mom_lookback)
        vol = rets.rolling(rc.vol_lookback).std()

        regime = None
        if benchmark is not None and not benchmark.empty and len(benchmark) > 200:
            bclose = benchmark["close"].reindex(dates, method="ffill")
            regime = (bclose > ema(bclose, 200))

        # Trailing turnover (for the point-in-time universe), if available.
        tmean = None
        if rc.univ_top_turnover and any("turnover" in df.columns for df in frames.values()):
            tpanel = pd.DataFrame({s: df["turnover"] for s, df in frames.items()
                                   if "turnover" in df.columns})
            tpanel = tpanel.reindex(index=dates, columns=panel.columns).sort_index()
            tmean = tpanel.rolling(rc.turnover_lookback).mean()

        warmup = max(rc.mom_lookback + rc.mom_skip, rc.vol_lookback, 200) + 5
        if warmup >= len(dates):
            raise RuntimeError("Not enough history for the configured lookbacks.")
        reb_set = set(range(warmup, len(dates), rc.rebalance_days))

        equity = self.start_equity
        prev_w = pd.Series(0.0, index=panel.columns)
        eq_curve, trades = [], []
        open_pos: dict[str, dict] = {}

        for i in range(warmup, len(dates)):
            d = dates[i]
            day_ret = float((prev_w * rets.iloc[i].fillna(0.0)).sum())
            equity *= (1 + day_ret)

            if i in reb_set:
                eligible = None
                if tmean is not None:
                    eligible = tmean.iloc[i].nlargest(rc.univ_top_turnover).index
                target = self._target(rc, mom.iloc[i], vol.iloc[i], regime, i,
                                      panel.columns, eligible)
                turnover = (target - prev_w).abs().sum()
                equity *= (1 - turnover * (self.fee + self.slip))
                price = panel.iloc[i]
                newtop = set(target[target > 0].index)
                tax_drag = 0.0
                for sym in list(open_pos):                 # close dropped names
                    if sym not in newtop:
                        info = open_pos.pop(sym)
                        cp = float(price[sym]) if not pd.isna(price[sym]) else info["price"]
                        ret = cp / info["price"] - 1
                        trades.append({"symbol": sym, "entry": info["date"], "ret": ret})
                        if ret > 0 and (rc.stcg_rate or rc.ltcg_rate):
                            held = (d.date() - dt.date.fromisoformat(info["date"])).days
                            rate = rc.ltcg_rate if held >= 365 else rc.stcg_rate
                            tax_drag += info.get("weight", 0.0) * ret * rate
                if tax_drag:
                    equity *= (1 - tax_drag)               # tax on realized gains
                for sym in newtop:                          # open new names
                    if sym not in open_pos and not pd.isna(price[sym]):
                        open_pos[sym] = {"date": d.date().isoformat(),
                                         "price": float(price[sym]), "weight": float(target[sym])}
                prev_w = target
            eq_curve.append((d, equity))

        last = panel.iloc[-1]
        for sym, info in open_pos.items():
            if not pd.isna(last[sym]):
                trades.append({"symbol": sym, "entry": info["date"], "ret": float(last[sym]) / info["price"] - 1})

        return self._assemble(rc, eq_curve, trades, rets, benchmark, dates, warmup, split)

    def _target(self, rc, m, v, regime, i, columns, eligible=None) -> pd.Series:
        target = pd.Series(0.0, index=columns)
        if rc.require_regime and regime is not None and not bool(regime.iloc[i]):
            return target                                   # bear regime -> cash
        valid = m.dropna().index.intersection(v.dropna().index)
        if eligible is not None:                            # point-in-time universe
            valid = valid.intersection(eligible)
        cand = m.loc[valid]
        if rc.lowvol_keep:
            cand = cand.loc[v.loc[valid].nsmallest(rc.lowvol_keep).index]
        top = cand.nlargest(rc.top_n).index
        if len(top):
            target.loc[top] = 1.0 / len(top)
        return target

    def _bench_curve(self, rets, dates, warmup) -> list[tuple]:
        bench_ret = rets.mean(axis=1)                        # equal-weight hold-all
        eq, out = self.start_equity, []
        for i in range(warmup, len(dates)):
            r = bench_ret.iloc[i]
            eq *= (1 + (0.0 if pd.isna(r) else float(r)))
            out.append((dates[i], eq))
        return out

    @staticmethod
    def _rebased_slice(curve, cut_ts, start_equity):
        sl = [p for p in curve if p[0] >= cut_ts]
        if len(sl) < 3:
            return sl
        scale = start_equity / sl[0][1]
        return [(t, v * scale) for t, v in sl]

    def _assemble(self, rc, eq_curve, trades, rets, benchmark, dates, warmup, split) -> dict:
        cut_ts = dates[int(len(dates) * split)]
        cut_iso = cut_ts.date().isoformat()
        bench_curve = self._bench_curve(rets, dates, warmup)

        full = {**equity_metrics(eq_curve, self.start_equity),
                **_trade_stats([t["ret"] for t in trades])}
        oos_eq = self._rebased_slice(eq_curve, cut_ts, self.start_equity)
        oos = {**equity_metrics(oos_eq, self.start_equity),
               **_trade_stats([t["ret"] for t in trades if t["entry"] > cut_iso])}
        bench_full = equity_metrics(bench_curve, self.start_equity)
        bench_oos = equity_metrics(self._rebased_slice(bench_curve, cut_ts, self.start_equity),
                                   self.start_equity)

        passed, reasons = self._verdict(oos, bench_oos)
        return {
            "variant": rc.name,
            "config": {"mom_lookback": rc.mom_lookback, "mom_skip": rc.mom_skip,
                       "vol_lookback": rc.vol_lookback, "lowvol_keep": rc.lowvol_keep,
                       "top_n": rc.top_n, "rebalance_days": rc.rebalance_days,
                       "require_regime": rc.require_regime},
            "period": f"{dates[warmup].date()} -> {dates[-1].date()}",
            "oos_split_date": cut_iso,
            "full_period": full,
            "out_of_sample": oos,
            "benchmark_equalweight_full": bench_full,
            "benchmark_equalweight_oos": bench_oos,
            "passed_oos": passed,
            "pass_detail": reasons,
            "walk_forward_stability": stability(eq_curve),
        }

    @staticmethod
    def _verdict(oos: dict, bench_oos: dict) -> tuple[bool, dict]:
        pf = oos.get("profit_factor")
        exp = oos.get("expectancy_pct")
        dd = oos.get("max_drawdown_pct")
        cagr = oos.get("cagr_pct")
        bcagr = bench_oos.get("cagr_pct")
        checks = {
            "pf>=1.2": pf is not None and pf >= 1.2,
            "expectancy>0": exp is not None and exp > 0,
            "maxDD>-20%": dd is not None and dd > -20.0,
            "beats_eqw_benchmark_cagr": cagr is not None and bcagr is not None and cagr > bcagr,
        }
        return all(checks.values()), checks
