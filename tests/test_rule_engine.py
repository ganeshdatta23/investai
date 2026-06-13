import pytest

from investai.reasoning.base import PortfolioContext
from investai.reasoning.rule_engine import RuleEngine
from investai.schemas import Action, Candidate, Instrument


def _features(**over):
    f = {
        "bars": 200, "price": 100.0,
        "ema20": 96.0, "ema50": 92.0, "ema200": 85.0,
        "rsi14": 60.0, "macd": 1.0, "macd_signal": 0.2, "macd_hist": 1.2,
        "atr14": 2.0, "atr_pct": 2.0, "adx14": 28.0, "plus_di": 30.0, "minus_di": 14.0,
        "vwap20": 95.0, "vol_spike": 1.6, "avg_vol20": 500_000.0,
        "high_20": 100.0, "low_20": 80.0, "high_55": 101.0,
        "dist_ema20_pct": 4.0, "dist_vwap_pct": 5.0, "ret_20": 8.0, "ret_50": 15.0,
        "uptrend_stack": True, "downtrend_stack": False, "above_vwap": True,
        "near_20d_high": True, "near_20d_low": False,
        "direction": "long", "score_reasons": ["ema_stack_up", "macd_pos"],
    }
    f.update(over)
    return f


def _cand(features, score=70.0):
    inst = Instrument(symbol="TEST", instrument_key="NSE_EQ|TEST", name="Test")
    return Candidate(instrument=inst, last_price=features["price"], features=features, score=score)


@pytest.fixture
def engine(tmp_cfg):
    return RuleEngine(tmp_cfg)


def _empty_pf(cfg):
    return PortfolioContext(equity=cfg.equity)


def test_strong_setup_buys(engine, tmp_cfg):
    d = engine.decide(_cand(_features()), _empty_pf(tmp_cfg))
    assert d.action == Action.BUY.value
    assert d.position_size and d.position_size > 0
    assert d.reward_risk >= 2.0
    assert d.setup_quality in ("A", "B", "C")
    assert 0 < d.risk_per_trade_percent <= 1.0 + 1e-6


def test_choppy_holds(engine, tmp_cfg):
    d = engine.decide(_cand(_features(adx14=15.0)), _empty_pf(tmp_cfg))
    assert d.action == Action.HOLD.value


def test_missing_atr_waits(engine, tmp_cfg):
    d = engine.decide(_cand(_features(atr14=None)), _empty_pf(tmp_cfg))
    assert d.action == Action.WAIT.value


def test_low_liquidity_avoids(engine, tmp_cfg):
    d = engine.decide(_cand(_features(avg_vol20=1_000.0)), _empty_pf(tmp_cfg))
    assert d.action == Action.AVOID.value


def test_overextended_holds(engine, tmp_cfg):
    d = engine.decide(_cand(_features(rsi14=82.0, dist_ema20_pct=14.0)), _empty_pf(tmp_cfg))
    assert d.action == Action.HOLD.value


def test_daily_loss_breaker_blocks(engine, tmp_cfg):
    pf = PortfolioContext(equity=tmp_cfg.equity, daily_realized_loss_pct=3.5)
    d = engine.decide(_cand(_features()), pf)
    assert d.action == Action.HOLD.value
    assert "daily_loss_limit_hit" in d.red_flags


def test_already_open_blocks_pyramiding(engine, tmp_cfg):
    pf = PortfolioContext(equity=tmp_cfg.equity, open_positions=1, open_symbols={"TEST"})
    d = engine.decide(_cand(_features()), pf)
    assert d.action == Action.HOLD.value


def test_portfolio_risk_budget_blocks(engine, tmp_cfg):
    pf = PortfolioContext(equity=tmp_cfg.equity, open_positions=5, open_risk_pct=4.8)
    d = engine.decide(_cand(_features()), pf)
    assert d.action == Action.HOLD.value
