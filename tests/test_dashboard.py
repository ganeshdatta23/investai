from investai.dashboard import generate


def test_dashboard_renders(tmp_cfg, tmp_path):
    scan = {
        "data_source": "synthetic", "data_classification": "SIMULATED_DATA",
        "status": "opportunities", "market_regime": "risk-off (n=20)",
        "top_opportunities": [{
            "symbol": "AAA", "action": "BUY", "confidence": 80, "entry_price": 100.0,
            "stop_loss": 97.0, "target_1": 110.0, "reward_risk": 2.3, "position_size": 5,
        }],
    }
    report = {
        "equity": 100000.0, "open_risk_pct": 0.9,
        "open_positions": [{"symbol": "AAA", "direction": "long", "qty": 5,
                            "fill_price": 100.0, "stop_loss": 97.0, "risk_pct": 0.9,
                            "opened_date": "2026-01-01"}],
        "performance": {"trades": 3, "win_rate_pct": 33.3, "profit_factor": 1.1,
                        "total_pnl": -150.0},
    }
    out = generate(tmp_cfg, scan, report, tmp_path / "docs")
    assert out.exists()
    h = out.read_text(encoding="utf-8")
    assert "PAPER TRADING — NOT REAL MONEY" in h          # honest banner present
    assert "AAA" in h                                      # candidate rendered
    assert (tmp_path / "docs" / "state.json").exists()
