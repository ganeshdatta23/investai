"""Research matrix: backtest variants A-E on identical data/period/costs, rank by
profit factor, and declare failure if no variant clears Profit Factor 1.0.

All variants are long-only (shorts can't be held in a cash account and were the
disproven baseline's likely drag). Each variant adds ONE more gate so the matrix
isolates each condition's marginal effect — no cherry-picking, no per-variant
parameter tuning.
"""
from __future__ import annotations

from .engine import Backtester, Filters

VARIANTS = [
    Filters("A_long_only", long_only=True),
    Filters("B_long_regime", long_only=True, require_regime=True),
    Filters("C_long_regime_adx25", long_only=True, require_regime=True, min_adx=25.0),
    Filters("D_long_regime_rs", long_only=True, require_regime=True, require_rs=True),
    Filters("E_long_regime_rs_adx25", long_only=True, require_regime=True,
            require_rs=True, min_adx=25.0),
]


def run_matrix(cfg, adapter, seed, days, interval="day", split=0.7) -> dict:
    bt = Backtester(cfg, adapter)
    ctx = bt.prepare(seed, days, interval)          # fetch + features ONCE

    rows = []
    for v in VARIANTS:
        res = bt.simulate(ctx, v, split)
        row = res.metrics_row()
        row["oos_profit_factor"] = res.out_of_sample.get("profit_factor")
        row["overfitting_risk"] = res.overfitting.get("risk")
        rows.append(row)

    # Rank by profit factor (None -> worst), then by expectancy.
    def key(r):
        pf = r["profit_factor"]
        return (pf if pf is not None else -1e9, r["expectancy"] or -1e9)

    ranked = sorted(rows, key=key, reverse=True)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i

    pfs = [r["profit_factor"] for r in rows]
    all_fail = all((pf is None or pf < 1.0) for pf in pfs)
    best = ranked[0]

    return {
        "research_matrix": "variants A-E (long-only family)",
        "period": f"{ctx.start} -> {ctx.end}",
        "symbols_tested": len(ctx.frames),
        "benchmark": ctx.benchmark_symbol,
        "interval": interval,
        "fees_pct": cfg.costs.fee_pct,
        "slippage_pct": cfg.costs.slippage_pct,
        "split_in_sample": split,
        "variants_ranked": ranked,
        "verdict": "FAILURE_NO_EDGE" if all_fail else "EDGE_CANDIDATE",
        "declaration": (
            "All variants remain below Profit Factor 1.0 — no measurable edge; "
            "do NOT consider live deployment."
            if all_fail else
            f"Best variant '{best['variant']}' shows PF {best['profit_factor']} "
            f"(OOS PF {best['oos_profit_factor']}). Candidate edge — requires further "
            "out-of-sample validation before any live consideration."
        ),
    }
