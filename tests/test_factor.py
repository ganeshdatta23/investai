from investai.backtest.factor import CONFIGS, run_factor_research
from investai.backtest.rotation import RotationBacktester, RotationConfig
from investai.data.synthetic import SyntheticAdapter


def test_factor_research_structure(tmp_cfg):
    adapter = SyntheticAdapter(tmp_cfg)
    syms = [f"S{i}" for i in range(1, 13)]
    out = run_factor_research(tmp_cfg, adapter, symbols=syms, years=4, split=0.6)

    assert out["verdict"] in ("EDGE_CANDIDATE", "FAILURE_NO_EDGE")
    assert len(out["results_ranked"]) == len(CONFIGS) == 2
    for r in out["results_ranked"]:
        if "error" in r:
            continue
        for k in ("out_of_sample", "full_period", "benchmark_equalweight_oos",
                  "passed_oos", "pass_detail"):
            assert k in r
        oos = r["out_of_sample"]
        for k in ("cagr_pct", "sharpe", "sortino", "max_drawdown_pct",
                  "profit_factor", "expectancy_pct", "trades"):
            assert k in oos
        # pass_detail must encode the 4 acceptance checks
        assert set(r["pass_detail"]) == {
            "pf>=1.2", "expectancy>0", "maxDD>-20%", "beats_eqw_benchmark_cagr"}


def test_tax_drag_never_increases_returns(tmp_cfg):
    from investai.schemas import Instrument
    adapter = SyntheticAdapter(tmp_cfg)
    frames = {s: adapter.fetch_history(Instrument(s, f"SYN|{s}"), "day", 1500)
              for s in [f"S{i}" for i in range(1, 11)]}
    bench = adapter.fetch_history(Instrument("NIFTY", "SYN|NIFTY"), "day", 1500)
    bt = RotationBacktester(tmp_cfg)
    base = bt.run(frames, bench, RotationConfig("notax", top_n=5, require_regime=False), 0.6)
    taxed = bt.run(frames, bench, RotationConfig("tax", top_n=5, require_regime=False,
                                                 stcg_rate=0.20, ltcg_rate=0.125), 0.6)
    b = base["full_period"]["cagr_pct"] or 0.0
    t = taxed["full_period"]["cagr_pct"] or 0.0
    assert t <= b + 1e-6                              # tax can only drag returns down


def test_rotation_runs_gated_and_ungated(tmp_cfg):
    from investai.schemas import Instrument
    adapter = SyntheticAdapter(tmp_cfg)
    frames = {s: adapter.fetch_history(Instrument(s, f"SYN|{s}"), "day", 1500)
              for s in [f"S{i}" for i in range(1, 11)]}
    bench = adapter.fetch_history(Instrument("NIFTY", "SYN|NIFTY"), "day", 1500)
    bt = RotationBacktester(tmp_cfg)
    for regime in (True, False):
        res = bt.run(frames, bench,
                     RotationConfig("c", top_n=5, require_regime=regime), split=0.6)
        assert res["full_period"]["trades"] >= 0
        assert "passed_oos" in res
        assert res["out_of_sample"]["max_drawdown_pct"] <= 0.0
