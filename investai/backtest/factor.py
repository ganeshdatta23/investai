"""Factor research runner: fetch the frozen universe + NIFTY benchmark once, run
the pre-registered rotation configs, and return a ranked verdict.

Pre-registered configs (verdict is on these exact settings — no grid search):
  H2  LowVol -> Momentum : 60 lowest-vol (12m) then top-30 by 6m momentum
  H3  Pure 12-1 Momentum : top-30 by 12-1m momentum
Both long-only, monthly rebalance, NIFTY-200EMA regime gate (cash in bear).
PEAD is NOT run here — it needs point-in-time earnings data the free feed lacks.
"""
from __future__ import annotations

from ..schemas import Instrument
from .._log import log
from .rotation import RotationBacktester, RotationConfig
from .universes import NIFTY200

CONFIGS = [
    RotationConfig("H2_lowvol_momentum", mom_lookback=126, mom_skip=21,
                   vol_lookback=252, lowvol_keep=60, top_n=30, rebalance_days=21,
                   require_regime=True),
    RotationConfig("H3_pure_momentum_12_1", mom_lookback=252, mom_skip=21,
                   vol_lookback=252, lowvol_keep=None, top_n=30, rebalance_days=21,
                   require_regime=True),
]

ACCEPTANCE = ("OOS: profit_factor>=1.2 AND expectancy>0 AND maxDD>-20% "
              "AND beats equal-weight-basket CAGR")


def _benchmark(adapter, days):
    if hasattr(adapter, "fetch_raw"):
        return adapter.fetch_raw("^NSEI", "day", days), "^NSEI"
    return adapter.fetch_history(Instrument("NIFTY", "SYN|NIFTY"), "day", days), "SYN|NIFTY"


# Survivorship-free configs: dynamic point-in-time top-200-by-turnover universe.
SF_CONFIGS = [
    RotationConfig("H2sf_lowvol_momentum", mom_lookback=126, mom_skip=21,
                   vol_lookback=252, lowvol_keep=60, top_n=30, rebalance_days=21,
                   require_regime=True, univ_top_turnover=200, turnover_lookback=63),
    RotationConfig("H3sf_pure_momentum_12_1", mom_lookback=252, mom_skip=21,
                   vol_lookback=252, lowvol_keep=None, top_n=30, rebalance_days=21,
                   require_regime=True, univ_top_turnover=200, turnover_lookback=63),
]


def _eqw_market(frames) -> "object":
    """Self-contained broad-market proxy (equal-weight of normalized closes) used
    only for the 200-EMA regime gate — robust to which names are present."""
    import pandas as pd
    panel = pd.DataFrame({s: df["close"] for s, df in frames.items()}).sort_index()
    norm = panel.divide(panel.ffill().bfill().iloc[0])
    return pd.DataFrame({"close": norm.mean(axis=1) * 100.0})


STCG_RATE, LTCG_RATE = 0.20, 0.125     # current India equity rates (post Jul-2024)


def run_factor_research_sf(cfg, start: str | None = None, top_turnover: int = 200,
                           split: float = 0.6, min_bars: int = 400,
                           with_tax: bool = False) -> dict:
    """Survivorship-free factor research using the local NSE Bhavcopy store."""
    from ..data.pricestore import build_frames, store_path
    if not store_path(cfg).exists():
        raise RuntimeError("No price store. Run: investai ingest-bhavcopy (free NSE) "
                           "or investai ingest-eodhd (needs EODHD_API_KEY).")
    frames = build_frames(cfg, start=start, min_bars=min_bars)
    if not frames:
        raise RuntimeError("Price store has no symbols with enough history.")
    log(f"[factor-sf] {len(frames)} symbols from store (incl. delisted), tax={with_tax}")
    benchmark = _eqw_market(frames)
    bt = RotationBacktester(cfg)
    tax = {"stcg_rate": STCG_RATE, "ltcg_rate": LTCG_RATE} if with_tax else {}
    configs = [RotationConfig(**{**c.__dict__, "univ_top_turnover": top_turnover, **tax})
               for c in SF_CONFIGS]
    results = []
    for rc in configs:
        try:
            results.append(bt.run(frames, benchmark, rc, split))
        except RuntimeError as e:
            results.append({"variant": rc.name, "error": str(e)})
    ranked = sorted(results,
                    key=lambda r: (r.get("out_of_sample", {}).get("profit_factor") or -1e9),
                    reverse=True)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    any_pass = any(r.get("passed_oos") for r in results)
    return {
        "research": "SURVIVORSHIP-FREE factor rotation (NSE Bhavcopy, incl. delisted)",
        "universe": f"point-in-time top-{top_turnover} by trailing turnover (dynamic)",
        "symbols_in_store": len(frames),
        "oos_split": split,
        "fees_pct": cfg.costs.fee_pct, "slippage_pct": cfg.costs.slippage_pct,
        "tax_modelled": (f"STCG {STCG_RATE:.0%} / LTCG {LTCG_RATE:.1%}" if with_tax else "none"),
        "acceptance_criteria": ACCEPTANCE,
        "results_ranked": ranked,
        "verdict": "EDGE_CANDIDATE" if any_pass else "FAILURE_NO_EDGE",
        "caveats": [
            "Universe is point-in-time (top-N by turnover each rebalance) incl. "
            "delisted names => survivorship bias largely removed.",
            "Prices split/bonus-adjusted from Bhavcopy open-gaps (NOT dividend-adjusted).",
            "TAXES NOT MODELLED — monthly rebalancing in India incurs short-term capital "
            "gains tax (~15-20%), a first-order cost that can erase a thin edge.",
            "Single IS/OOS split; see walk_forward_stability for rolling robustness.",
        ],
    }


def run_factor_research(cfg, adapter, symbols=None, years: float = 10.0,
                        split: float = 0.6) -> dict:
    symbols = symbols or NIFTY200
    days = int(years * 365)
    frames = {}
    for s in symbols:
        df = adapter.fetch_history(Instrument(symbol=s, instrument_key=f"NSE_EQ|{s}"), "day", days)
        if not df.empty and len(df) >= 260:
            frames[s] = df
    if not frames:
        raise RuntimeError("No usable history fetched for the universe.")
    log(f"[factor] fetched {len(frames)}/{len(symbols)} symbols with usable history")

    benchmark, bench_sym = _benchmark(adapter, days)
    bt = RotationBacktester(cfg)

    results = []
    for rc in CONFIGS:
        try:
            results.append(bt.run(frames, benchmark, rc, split))
        except RuntimeError as e:
            results.append({"variant": rc.name, "error": str(e)})

    ranked = sorted(
        results,
        key=lambda r: (r.get("out_of_sample", {}).get("profit_factor") or -1e9),
        reverse=True,
    )
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    any_pass = any(r.get("passed_oos") for r in results)

    best = ranked[0]
    return {
        "research": "factor rotation (price-based, long-only)",
        "universe": "NIFTY200 (current constituents)",
        "symbols_fetched": len(frames),
        "benchmark_index": bench_sym,
        "years": years,
        "oos_split": split,
        "fees_pct": cfg.costs.fee_pct,
        "slippage_pct": cfg.costs.slippage_pct,
        "acceptance_criteria": ACCEPTANCE,
        "results_ranked": ranked,
        "verdict": "EDGE_CANDIDATE" if any_pass else "FAILURE_NO_EDGE",
        "declaration": (
            f"Best '{best.get('variant')}' PASSED out-of-sample — candidate edge; "
            "requires walk-forward + paper validation before any live consideration."
            if any_pass else
            "No variant clears the out-of-sample acceptance bar — no measurable edge."
        ),
        "caveats": [
            "Today's NIFTY200 membership applied to the past => survivorship + "
            "look-ahead bias (results are OPTIMISTIC). A fail here is therefore strong.",
            "Raw close (ex-dividend) used consistently for strategy AND benchmark.",
            "PEAD not tested: needs point-in-time earnings/consensus data the free feed lacks.",
            "Single IS/OOS split only (not full walk-forward / Monte Carlo yet).",
        ],
    }
