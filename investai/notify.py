"""Email notifications for the paper-trading run (Gmail SMTP).

Credentials are read from the environment and NEVER logged:
  EMAIL_FROM          - sender Gmail address
  EMAIL_APP_PASSWORD  - Gmail App Password (16 chars; spaces are stripped)
Recipients + sender default come from config.yaml `email:` block.

This sends informational PAPER summaries only — it does not place or confirm any
real trade.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from .config import Config


def recipients(cfg: Config) -> list[str]:
    return list(cfg.get("email", "to", default=[]) or [])


def sender(cfg: Config) -> str:
    return os.environ.get("EMAIL_FROM") or str(cfg.get("email", "from", default="") or "")


def is_configured(cfg: Config) -> bool:
    return bool(
        cfg.get("email", "enabled", default=False)
        and sender(cfg)
        and os.environ.get("EMAIL_APP_PASSWORD")
        and recipients(cfg)
    )


def send(cfg: Config, subject: str, html_body: str, text_body: str,
         to: list[str] | None = None, attachment=None) -> dict:
    frm = sender(cfg)
    pwd = (os.environ.get("EMAIL_APP_PASSWORD") or "").replace(" ", "")
    to = to or recipients(cfg)
    if not (frm and pwd and to):
        raise RuntimeError("Email not configured: need EMAIL_FROM, EMAIL_APP_PASSWORD, email.to")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("InvestAI", frm))
    msg["To"] = ", ".join(to)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    if attachment is not None:
        from pathlib import Path
        p = Path(attachment)
        if p.exists():
            msg.add_attachment(p.read_bytes(), maintype="text", subtype="html",
                               filename=p.name)

    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(frm, pwd)
        s.send_message(msg)
    return {"sent_to": to, "subject": subject}


def build_summary(cfg: Config, scan: dict, report: dict) -> tuple[str, str, str]:
    """Return (subject, html, text) for a daily paper-run summary."""
    perf = report.get("performance", {})
    equity0 = report.get("equity", cfg.equity)
    pnl = perf.get("total_pnl", 0.0) or 0.0
    equity = equity0 + pnl
    opps = scan.get("top_opportunities", [])
    status = scan.get("status", "")
    ts = report.get("as_of", "")

    subject = (f"InvestAI PAPER — {len(opps)} signal(s), "
               f"equity ₹{equity:,.0f} ({pnl:+,.0f}) [{status}]")

    def opp_line(o):
        return (f"{o.get('action'):4} {o.get('symbol'):12} conf {o.get('confidence')}  "
                f"entry {o.get('entry_price')}  stop {o.get('stop_loss')}  "
                f"T1 {o.get('target_1')}  RR {o.get('reward_risk')}  qty {o.get('position_size')}")

    text = (
        "InvestAI — PAPER TRADING summary (NOT real money).\n"
        f"as of {ts} | source {scan.get('data_source')} ({scan.get('data_classification')})\n"
        f"market regime: {scan.get('market_regime')}\n\n"
        f"Paper equity: Rs {equity:,.2f}  (P&L {pnl:+,.2f}, start {equity0:,.0f})\n"
        f"Open positions: {len(report.get('open_positions', []))}  "
        f"| win rate {perf.get('win_rate_pct','-')}%  PF {perf.get('profit_factor','-')}\n\n"
        f"Today's candidates ({status}):\n  " +
        ("\n  ".join(opp_line(o) for o in opps) if opps else "none") +
        "\n\nNote: rigorous backtesting found NO deployable edge; this is an honest "
        "forward-test, not advice. No real orders are placed.\n"
    )

    rows = "".join(
        f"<tr><td>{o.get('action')}</td><td>{o.get('symbol')}</td>"
        f"<td>{o.get('confidence')}</td><td>{o.get('entry_price')}</td>"
        f"<td>{o.get('stop_loss')}</td><td>{o.get('target_1')}</td>"
        f"<td>{o.get('reward_risk')}</td><td>{o.get('position_size')}</td></tr>"
        for o in opps) or '<tr><td colspan="8">none</td></tr>'
    html = (
        '<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:680px">'
        '<h2 style="margin:0">InvestAI <span style="color:#b35">PAPER</span></h2>'
        f'<p style="color:#666;margin:2px 0">as of {ts} · {scan.get("data_source")} '
        f'({scan.get("data_classification")}) · regime: {scan.get("market_regime")}</p>'
        '<div style="background:#fdecec;border:1px solid #f0b7b7;border-radius:8px;'
        'padding:10px;color:#a33;margin:10px 0"><b>PAPER TRADING — NOT REAL MONEY.</b> '
        'Backtesting found no proven edge; this is a transparent forward-test, not advice. '
        'No real orders are placed.</div>'
        f'<p><b>Paper equity:</b> ₹{equity:,.2f} '
        f'(<span style="color:{"#2a8" if pnl>=0 else "#c33"}">{pnl:+,.2f}</span>) · '
        f'open positions {len(report.get("open_positions", []))} · '
        f'win rate {perf.get("win_rate_pct","-")}% · PF {perf.get("profit_factor","-")}</p>'
        f'<h3>Today\'s candidates ({status})</h3>'
        '<table style="border-collapse:collapse;width:100%;font-size:14px" border="0">'
        '<tr style="text-align:left;color:#666">'
        '<th>Action</th><th>Symbol</th><th>Conf</th><th>Entry</th><th>Stop</th>'
        '<th>Target</th><th>R:R</th><th>Qty</th></tr>'
        f'{rows}</table>'
        '<p style="color:#888;font-size:12px;margin-top:14px">Capital-preservation-first. '
        'Sent automatically by InvestAI.</p></div>'
    )
    return subject, html, text
