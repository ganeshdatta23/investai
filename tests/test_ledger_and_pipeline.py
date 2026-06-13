from investai.paper.ledger import PaperLedger
from investai.paper.tracker import performance_report
from investai.pipeline import run_scan
from investai.schemas import Action, Decision


def _buy_decision():
    return Decision(
        symbol="TEST", timeframe="day", action=Action.BUY.value, confidence=70,
        setup_quality="B", entry_price=100.0, stop_loss=97.0, target_1=107.5,
        target_2=112.0, reward_risk=2.3, position_size=333,
        risk_per_trade_percent=1.0, decision_rationale="test",
    )


def test_open_and_stop_out(tmp_cfg):
    led = PaperLedger(tmp_cfg)
    tid = led.open_trade(_buy_decision(), "NSE_EQ|TEST", sector="IT")
    assert tid is not None
    assert len(led.open_positions()) == 1

    # Price gaps below stop -> mark-to-market closes at a loss, ~ -1R.
    closed = led.mark_to_market({"NSE_EQ|TEST": 90.0})
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "stop"
    assert closed[0]["pnl"] < 0
    assert closed[0]["r_multiple"] < 0
    assert len(led.open_positions()) == 0

    perf = performance_report(led.closed_trades())
    assert perf["trades"] == 1
    assert perf["losses"] == 1
    assert perf["win_rate_pct"] == 0.0
    led.close()


def test_target_hit_is_profit(tmp_cfg):
    led = PaperLedger(tmp_cfg)
    led.open_trade(_buy_decision(), "NSE_EQ|TEST")
    closed = led.mark_to_market({"NSE_EQ|TEST": 108.0})
    assert closed[0]["exit_reason"] == "target1"
    assert closed[0]["pnl"] > 0
    assert closed[0]["r_multiple"] > 0
    led.close()


def test_portfolio_context_tracks_open_risk(tmp_cfg):
    led = PaperLedger(tmp_cfg)
    led.open_trade(_buy_decision(), "NSE_EQ|TEST", sector="IT")
    pf = led.portfolio_context()
    assert pf.open_positions == 1
    assert pf.open_risk_pct > 0
    assert "TEST" in pf.open_symbols
    led.close()


def test_run_scan_offline_end_to_end(tmp_cfg):
    result = run_scan(tmp_cfg, offline=True, seed=["AAA", "BBB", "CCC", "DDD", "EEE"])
    assert result["data_source"] == "synthetic"
    assert result["status"] in ("opportunities", "no_opportunities")
    assert "market_regime" in result
    assert isinstance(result["top_opportunities"], list)
    assert len(result["top_opportunities"]) <= 5
