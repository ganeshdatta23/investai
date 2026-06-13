import numpy as np
import pandas as pd

from investai.indicators.engine import (
    adx, atr, compute_features, ema, macd, rsi, volume_spike,
)


def _frame(close, vol=None):
    n = len(close)
    close = np.asarray(close, dtype=float)
    high = close * 1.01
    low = close * 0.99
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = np.full(n, 1e6) if vol is None else np.asarray(vol, dtype=float)
    idx = pd.date_range("2023-01-01", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_rsi_bounds_and_uptrend():
    close = np.linspace(100, 200, 300)  # monotonically rising
    r = rsi(_frame(close)["close"])
    last = r.dropna().iloc[-1]
    assert 0.0 <= last <= 100.0
    assert last > 70.0  # persistent uptrend -> high RSI


def test_ema_ordering_in_uptrend():
    close = np.linspace(50, 150, 300)
    df = _frame(close)
    assert ema(df["close"], 20).iloc[-1] > ema(df["close"], 50).iloc[-1]
    assert ema(df["close"], 50).iloc[-1] > ema(df["close"], 200).iloc[-1]


def test_atr_and_adx_positive():
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0.2, 1.0, 300))
    df = _frame(close)
    assert atr(df).dropna().iloc[-1] > 0
    a = adx(df).dropna()
    assert not a.empty and a["adx"].iloc[-1] >= 0


def test_macd_columns():
    close = np.linspace(100, 120, 100)
    m = macd(_frame(close)["close"])
    assert set(m.columns) == {"macd", "macd_signal", "macd_hist"}


def test_volume_spike():
    vol = np.concatenate([np.full(40, 1e6), [4e6]])
    vs = volume_spike(pd.Series(vol))
    assert vs.iloc[-1] > 3.0


def test_compute_features_uptrend_flags():
    close = np.linspace(80, 160, 260)
    f = compute_features(_frame(close))
    assert f["bars"] == 260
    assert f["uptrend_stack"] is True
    assert f["price"] > f["ema20"] > f["ema50"]
    assert 0 <= f["rsi14"] <= 100


def test_short_history_yields_none_not_error():
    close = np.linspace(100, 110, 10)  # too short for most indicators
    f = compute_features(_frame(close))
    assert f["bars"] == 10
    assert f["ema200"] is None
    assert f["adx14"] is None  # not enough bars
