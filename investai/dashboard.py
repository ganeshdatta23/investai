"""Static HTML dashboard for the paper-trading deployment.

Renders the paper ledger + latest scan into a single self-contained page
(`docs/index.html`) plus a machine-readable `docs/state.json`. No external
assets, so it serves cleanly from GitHub Pages. The banner is deliberately
blunt: this is PAPER money and no proven edge exists.
"""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

from .config import Config


def _fmt(x, nd=2, default="–"):
    if x is None:
        return default
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return html.escape(str(x))


def _rows(items, cols):
    if not items:
        return '<tr><td colspan="%d" class="muted">none</td></tr>' % len(cols)
    out = []
    for it in items:
        tds = "".join(f"<td>{html.escape(str(it.get(c[0], '')))}</td>" if c[2] == 's'
                      else f"<td>{_fmt(it.get(c[0]), c[2])}</td>" for c in cols)
        out.append(f"<tr>{tds}</tr>")
    return "".join(out)


def generate(cfg: Config, scan: dict, report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    perf = report.get("performance", {})
    equity0 = report.get("equity", cfg.equity)
    pnl = perf.get("total_pnl", 0.0) or 0.0
    paper_equity = equity0 + pnl
    ret_pct = (paper_equity / equity0 - 1) * 100 if equity0 else 0.0
    ts = dt.datetime.now().isoformat(timespec="seconds")

    opps = scan.get("top_opportunities", [])
    opp_cols = [("symbol", "Symbol", "s"), ("action", "Action", "s"),
                ("confidence", "Conf", 0), ("entry_price", "Entry", 2),
                ("stop_loss", "Stop", 2), ("target_1", "Target", 2),
                ("reward_risk", "R:R", 2), ("position_size", "Qty", 0)]
    pos = report.get("open_positions", [])
    pos_cols = [("symbol", "Symbol", "s"), ("direction", "Dir", "s"), ("qty", "Qty", 0),
                ("fill_price", "Fill", 2), ("stop_loss", "Stop", 2),
                ("risk_pct", "Risk%", 2), ("opened_date", "Opened", "s")]

    def card(label, value, sub=""):
        return (f'<div class="card"><div class="label">{label}</div>'
                f'<div class="val">{value}</div><div class="sub">{sub}</div></div>')

    pnl_cls = "pos" if pnl >= 0 else "neg"
    html_doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>InvestAI — Paper Dashboard</title>
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#e6edf3}}
.wrap{{max-width:1000px;margin:0 auto;padding:24px}}
h1{{font-size:22px;margin:0 0 2px}} .muted{{color:#8b949e}}
.banner{{background:#3d1d1d;border:1px solid #6e2b2b;border-radius:10px;padding:14px 16px;margin:16px 0;color:#ffb4b4}}
.banner b{{color:#fff}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px}}
.card .label{{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.card .val{{font-size:24px;font-weight:600;margin-top:4px}}
.card .sub{{color:#8b949e;font-size:12px;margin-top:2px}}
.pos{{color:#3fb950}} .neg{{color:#f85149}}
table{{width:100%;border-collapse:collapse;margin:8px 0 20px;font-size:14px}}
th,td{{text-align:right;padding:8px 10px;border-bottom:1px solid #21262d}}
th:first-child,td:first-child{{text-align:left}}
th{{color:#8b949e;font-weight:500;font-size:12px;text-transform:uppercase}}
h2{{font-size:15px;margin:22px 0 6px;color:#c9d1d9}}
a{{color:#58a6ff}} footer{{color:#8b949e;font-size:12px;margin-top:24px}}
.pill{{display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:20px;padding:2px 10px;font-size:12px;color:#8b949e}}
</style></head><body><div class="wrap">
<h1>InvestAI <span class="pill">PAPER</span></h1>
<div class="muted">Autonomous NSE forward-test · updated {ts} · source: {html.escape(str(scan.get('data_source','?')))} ({html.escape(str(scan.get('data_classification','')))})</div>

<div class="banner"><b>⚠ PAPER TRADING — NOT REAL MONEY.</b> This is a transparent forward-test.
Rigorous backtesting found <b>no strategy with a deployable edge</b> — every variant lost to a
low-cost index after costs and tax (see <a href="CONCLUSION.html">CONCLUSION</a>). No real orders
are placed. A hard live-gate stays locked until an edge is proven <i>and</i> a human approves each order.</div>

<div class="cards">
{card("Paper Equity", "₹" + _fmt(paper_equity), f"start ₹{_fmt(equity0)}")}
{card("Total P&L", f'<span class="{pnl_cls}">₹{_fmt(pnl)}</span>', f"{ret_pct:+.2f}%")}
{card("Open Positions", _fmt(len(pos), 0), f"open risk {_fmt(report.get('open_risk_pct'))}%")}
{card("Win Rate", _fmt(perf.get('win_rate_pct')) + "%", f"{perf.get('trades',0)} closed · PF {_fmt(perf.get('profit_factor'))}")}
</div>

<h2>Today's candidates ({html.escape(str(scan.get('status','')))})</h2>
<table><thead><tr>{''.join(f'<th>{c[1]}</th>' for c in opp_cols)}</tr></thead>
<tbody>{_rows(opps, opp_cols)}</tbody></table>

<h2>Open paper positions</h2>
<table><thead><tr>{''.join(f'<th>{c[1]}</th>' for c in pos_cols)}</tr></thead>
<tbody>{_rows(pos, pos_cols)}</tbody></table>

<footer>Generated by InvestAI · mode {html.escape(cfg.mode)} · market regime: {html.escape(str(scan.get('market_regime','?')))}<br>
Capital-preservation-first. This dashboard exists to test honestly, not to promise returns.</footer>
</div></body></html>"""

    (out_dir / "index.html").write_text(html_doc, encoding="utf-8")
    (out_dir / "state.json").write_text(
        json.dumps({"generated": ts, "paper_equity": paper_equity,
                    "scan": scan, "report": report}, indent=2, default=str),
        encoding="utf-8")
    return out_dir / "index.html"
