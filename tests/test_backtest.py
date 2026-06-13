from investai.backtest.engine import Backtester, _pnl
from investai.backtest.matrix import VARIANTS, run_matrix
from investai.data.synthetic import SyntheticAdapter


def test_pnl_cost_monotonicity():
    # Higher costs never increase P&L.
    p0, _ = _pnl(100, 110, 10, "long", fee_pct=0.0, slip_pct=0.0)
    p1, _ = _pnl(100, 110, 10, "long", fee_pct=0.1, slip_pct=0.1)
    p2, _ = _pnl(100, 110, 10, "long", fee_pct=0.3, slip_pct=0.3)
    assert p0 > p1 > p2

    # Short direction: profit when price falls.
    ps, _ = _pnl(110, 100, 10, "short", fee_pct=0.0, slip_pct=0.0)
    assert ps > 0


def test_backtest_runs_on_synthetic(tmp_cfg):
    adapter = SyntheticAdapter(tmp_cfg)
    bt = Backtester(tmp_cfg, adapter)
    result = bt.run(["AAA", "BBB", "CCC", "DDD", "EEE"], days=900, interval="day", split=0.7)
    s = result.summary()

    # Structure
    for key in ("overall", "in_sample", "out_of_sample", "overfitting_check",
                "fee_sensitivity", "slippage_sensitivity", "total_return_pct",
                "max_drawdown_pct"):
        assert key in s
    assert result.max_drawdown_pct <= 0.0           # drawdown is non-positive
    assert len(s["fee_sensitivity"]) == 4

    # Fee sensitivity must be monotonically non-increasing in total P&L.
    pnls = [row["total_pnl"] for row in s["fee_sensitivity"]]
    assert all(a >= b - 1e-6 for a, b in zip(pnls, pnls[1:]))


def test_no_lookahead_exit_after_entry(tmp_cfg):
    adapter = SyntheticAdapter(tmp_cfg)
    bt = Backtester(tmp_cfg, adapter)
    result = bt.run(["AAA", "BBB", "CCC"], days=900, interval="day", split=0.7)
    for t in result.trades:
        assert t["exit_date"] is None or t["exit_date"] >= t["entry_date"]
        # entry fill is derived from the signal-bar close (no future data)
        assert t["entry_raw"] is not None


def test_research_matrix_structure_and_ranking(tmp_cfg):
    adapter = SyntheticAdapter(tmp_cfg)
    out = run_matrix(tmp_cfg, adapter,
                     ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
                     days=900, interval="day", split=0.7)
    rows = out["variants_ranked"]
    assert len(rows) == len(VARIANTS) == 5
    assert sorted(r["rank"] for r in rows) == [1, 2, 3, 4, 5]
    for r in rows:
        for key in ("variant", "trades", "win_rate_pct", "profit_factor", "expectancy",
                    "cagr_pct", "max_drawdown_pct", "sharpe", "sortino"):
            assert key in r
    assert out["verdict"] in ("FAILURE_NO_EDGE", "EDGE_CANDIDATE")
    # ranked by profit factor descending (None treated as worst)
    pfs = [(-1.0 if r["profit_factor"] is None else r["profit_factor"]) for r in rows]
    assert pfs == sorted(pfs, reverse=True)


def test_variants_progressively_filter(tmp_cfg):
    # Adding gates should never increase trade count (each variant is a subset).
    adapter = SyntheticAdapter(tmp_cfg)
    out = run_matrix(tmp_cfg, adapter, ["AAA", "BBB", "CCC", "DDD", "EEE"],
                     days=900, interval="day", split=0.7)
    by = {r["variant"]: r["trades"] for r in out["variants_ranked"]}
    assert by["B_long_regime"] <= by["A_long_only"]
    assert by["C_long_regime_adx25"] <= by["B_long_regime"]
    assert by["E_long_regime_rs_adx25"] <= by["D_long_regime_rs"]
