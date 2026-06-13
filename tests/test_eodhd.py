import datetime as dt

import numpy as np
import pandas as pd

from investai.backtest.rotation import RotationBacktester, RotationConfig, stability
from investai.data import eodhd


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p


def _eod_rows(n, start=dt.date(2018, 1, 1)):
    rows = []
    for i in range(n):
        d = start + dt.timedelta(days=i)
        px = 100 + i * 0.05
        rows.append({"date": d.isoformat(), "open": px, "high": px, "low": px,
                     "close": px, "adjusted_close": px, "volume": 100000})
    return rows


def _client(monkeypatch):
    c = eodhd.EODHDClient("KEY", min_interval=0.0)

    def fake_get(url, params=None, timeout=None):
        if "exchange-symbol-list" in url:
            if params and params.get("delisted"):
                return _Resp(200, [{"Code": "DEADCO", "Type": "Common Stock", "Name": "Dead"}])
            return _Resp(200, [{"Code": "AAA", "Type": "Common Stock", "Name": "A"},
                               {"Code": "BBB", "Type": "Common Stock", "Name": "B"},
                               {"Code": "ETFX", "Type": "ETF", "Name": "an etf"}])
        if "/eod/" in url:
            return _Resp(200, _eod_rows(450))
        return _Resp(404, {})

    monkeypatch.setattr(c.session, "get", fake_get)
    return c


def test_eodhd_client_parsing(monkeypatch):
    c = _client(monkeypatch)
    syms = c.list_symbols("NSE")
    assert {s["Code"] for s in syms} == {"AAA", "BBB", "ETFX"}
    eod = c.eod("AAA")
    assert eod[0]["adjusted_close"] == 100.0


def test_ingest_filters_to_equities_and_builds_frames(tmp_cfg, monkeypatch):
    c = _client(monkeypatch)
    summary = eodhd.ingest_nse(tmp_cfg, c, start="2018-01-01")
    # AAA, BBB (active equities) + DEADCO (delisted equity); ETF excluded
    assert summary["symbols_in_store"] == 3
    frames = eodhd.build_frames(tmp_cfg, min_bars=400)
    assert set(frames) == {"AAA", "BBB", "DEADCO"}
    for df in frames.values():
        assert list(df.columns) == ["close", "turnover"]
        assert (df["turnover"] > 0).all()


def test_ingest_is_resumable(tmp_cfg, monkeypatch):
    c = _client(monkeypatch)
    eodhd.ingest_nse(tmp_cfg, c, start="2018-01-01")
    second = eodhd.ingest_nse(tmp_cfg, c, start="2018-01-01")
    assert second["fetched_this_run"] == 0          # nothing re-fetched


def test_point_in_time_universe_runs(tmp_cfg):
    idx = pd.date_range("2016-01-01", periods=820, freq="B", tz="UTC")
    rng = np.random.default_rng(0)
    frames = {}
    for k in range(8):
        close = 50 + k + np.abs(np.cumsum(rng.normal(0.05, 1.0, 820)))
        frames[f"S{k}"] = pd.DataFrame(
            {"close": close, "turnover": np.full(820, (k + 1) * 1e6)}, index=idx)
    bench = pd.DataFrame({"close": 100 + np.abs(np.cumsum(rng.normal(0.05, 1, 820)))}, index=idx)
    bt = RotationBacktester(tmp_cfg)
    res = bt.run(frames, bench,
                 RotationConfig("piv", top_n=3, univ_top_turnover=4, require_regime=False),
                 split=0.6)
    assert "walk_forward_stability" in res
    assert res["full_period"]["trades"] >= 0


def test_stability_report_keys():
    idx = pd.date_range("2018-01-01", periods=700, freq="B", tz="UTC")
    curve = [(d, 100000 * (1.0003 ** i)) for i, d in enumerate(idx)]
    s = stability(curve)
    assert "per_year_return_pct" in s
    assert "rolling_1y_pct_positive" in s
    assert s["rolling_1y_pct_positive"] == 100.0     # monotonic up -> always positive
