from investai.schemas import ExecutionCosts
from investai.sizing import net_reward_risk, position_size, reward_risk


def test_position_size_basic():
    r = position_size(equity=100_000, risk_pct=1.0, entry=100.0, stop=98.0)
    assert r.quantity == 500          # 1000 budget / 2 per-share risk
    assert r.risk_amount == 1000.0
    assert round(r.risk_pct_used, 4) == 1.0


def test_position_size_rounds_down():
    r = position_size(equity=100_000, risk_pct=1.0, entry=100.0, stop=97.0)
    assert r.quantity == 333          # floor(1000/3)
    assert r.risk_amount == 999.0


def test_position_size_zero_when_no_risk():
    assert position_size(100_000, 1.0, 100.0, 100.0).quantity == 0


def test_reward_risk_long():
    assert reward_risk(100, 98, 104, "long") == 2.0


def test_reward_risk_short():
    assert reward_risk(100, 102, 96, "short") == 2.0


def test_net_rr_below_gross():
    costs = ExecutionCosts(fee_pct=0.03, slippage_pct=0.05)
    gross = reward_risk(100, 97, 107.5, "long")
    net = net_reward_risk(100, 97, 107.5, costs, "long")
    assert net is not None and net < gross
    assert net > 2.0                  # still passes policy here


def test_net_rr_collapses_on_thin_atr():
    # Tiny stop distance -> fees dominate -> net RR should fall well below gross.
    costs = ExecutionCosts(fee_pct=0.10, slippage_pct=0.10)
    net = net_reward_risk(1000, 999.5, 1001.25, costs, "long")
    assert net is not None and net < 2.0
