from investai import notify


def _scan_report():
    scan = {"data_source": "synthetic", "data_classification": "SIMULATED_DATA",
            "status": "opportunities", "market_regime": "x",
            "top_opportunities": [{"symbol": "AAA", "action": "BUY", "confidence": 80,
                                   "entry_price": 100, "stop_loss": 97, "target_1": 110,
                                   "reward_risk": 2.3, "position_size": 5}]}
    report = {"equity": 100000.0, "as_of": "2026-01-01", "open_positions": [],
              "performance": {"trades": 2, "win_rate_pct": 50, "profit_factor": 1.1,
                              "total_pnl": -100.0}}
    return scan, report


def test_build_summary(tmp_cfg):
    scan, report = _scan_report()
    subj, html, text = notify.build_summary(tmp_cfg, scan, report)
    assert "PAPER" in subj
    assert "AAA" in html and "NOT REAL MONEY" in html
    assert "AAA" in text and "No real orders" in text


def test_send_uses_smtp_and_strips_password(tmp_cfg, monkeypatch):
    tmp_cfg.raw["email"] = {"enabled": True, "from": "x@gmail.com", "to": ["a@b.com", "c@d.com"]}
    monkeypatch.setenv("EMAIL_FROM", "x@gmail.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    captured = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): pass
        def login(self, user, pwd): captured["login"] = (user, pwd)
        def send_message(self, msg): captured["to"] = msg["To"]

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    info = notify.send(tmp_cfg, "subj", "<b>h</b>", "t")
    assert info["sent_to"] == ["a@b.com", "c@d.com"]
    assert captured["login"] == ("x@gmail.com", "abcdefghijklmnop")   # spaces stripped
    assert "a@b.com" in captured["to"] and "c@d.com" in captured["to"]


def test_is_configured_requires_password(tmp_cfg, monkeypatch):
    tmp_cfg.raw["email"] = {"enabled": True, "from": "x@gmail.com", "to": ["a@b.com"]}
    monkeypatch.setenv("EMAIL_FROM", "x@gmail.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "pw")
    assert notify.is_configured(tmp_cfg) is True
    monkeypatch.delenv("EMAIL_APP_PASSWORD")
    assert notify.is_configured(tmp_cfg) is False
