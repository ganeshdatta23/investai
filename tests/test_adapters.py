import types

import pytest
import requests

from investai.data.base import DataAdapter
from investai.data.synthetic import SyntheticAdapter
from investai.data.yfinance_adapter import YFinanceAdapter, chart_to_frame
from investai.pipeline import build_adapter


# --------------------------------------------------------------------------- #
# Yahoo payload parsing — never fabricates, drops nulls, returns ascending.
# --------------------------------------------------------------------------- #
def _payload():
    return {"chart": {"result": [{
        "timestamp": [1_700_000_000, 1_700_086_400, 1_700_172_800],
        "indicators": {"quote": [{
            "open": [10.0, 11.0, None],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, None],   # 3rd bar has no close -> dropped
            "volume": [1000, 2000, None],
        }]},
    }]}}


def test_chart_to_frame_parses_and_drops_nulls():
    df = chart_to_frame(_payload())
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2                       # null-close row removed
    assert df.index.is_monotonic_increasing
    assert df["close"].iloc[0] == 10.5


@pytest.mark.parametrize("bad", [None, {}, {"chart": {"result": []}}, {"chart": {"result": [{}]}}])
def test_chart_to_frame_malformed_returns_empty(bad):
    assert chart_to_frame(bad).empty


# --------------------------------------------------------------------------- #
# Retry / graceful failure with a fake session (no network).
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _adapter(tmp_cfg):
    a = YFinanceAdapter(tmp_cfg, min_interval_s=0.0, max_retries=3)
    return a


def test_get_json_retries_then_succeeds(tmp_cfg, monkeypatch):
    monkeypatch.setattr("investai.data.yfinance_adapter.time.sleep", lambda *_: None)
    a = _adapter(tmp_cfg)
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.RequestException("boom")
        if calls["n"] == 2:
            return _Resp(429)
        return _Resp(200, _payload())

    monkeypatch.setattr(a._session, "get", fake_get)
    out = a._get_json("RELIANCE.NS", {"range": "5d"})
    assert out == _payload()
    assert calls["n"] == 3


def test_get_json_gives_up_gracefully(tmp_cfg, monkeypatch):
    monkeypatch.setattr("investai.data.yfinance_adapter.time.sleep", lambda *_: None)
    a = _adapter(tmp_cfg)
    monkeypatch.setattr(a._session, "get",
                        lambda *args, **kw: _Resp(404))
    assert a._get_json("NOPE.NS", {}) is None


def test_is_ready_true_false(tmp_cfg, monkeypatch):
    a = _adapter(tmp_cfg)
    monkeypatch.setattr(a, "_get_json", lambda *args, **kw: _payload())
    assert a.is_ready() is True

    b = _adapter(tmp_cfg)
    monkeypatch.setattr(b, "_get_json", lambda *args, **kw: None)
    assert b.is_ready() is False


# --------------------------------------------------------------------------- #
# Status reporting + provenance classification.
# --------------------------------------------------------------------------- #
def test_status_blocks(tmp_cfg):
    syn = SyntheticAdapter(tmp_cfg).status()
    assert syn["adapter"] == "synthetic"
    assert syn["feed_type"] == "simulated"
    assert syn["data_quality"] == "SIMULATED_DATA"
    assert syn["market_status"] in ("open", "closed")

    yf = YFinanceAdapter(tmp_cfg).status()
    assert yf["adapter"] == "yfinance"
    assert yf["feed_type"] == "delayed"
    assert yf["data_quality"] == "REAL_MARKET_DATA"


# --------------------------------------------------------------------------- #
# Auto-fallback selection (no creds in test env -> Upstox not ready -> YFinance).
# --------------------------------------------------------------------------- #
def test_build_adapter_falls_back_to_yfinance(tmp_cfg):
    a = build_adapter(tmp_cfg, offline=False)
    assert isinstance(a, YFinanceAdapter)
    assert isinstance(a, DataAdapter)


def test_build_adapter_offline_is_synthetic(tmp_cfg):
    a = build_adapter(tmp_cfg, offline=True)
    assert isinstance(a, SyntheticAdapter)
