"""InvestAI command-line interface.

  python -m investai auth                 # one-time-per-day Upstox OAuth
  python -m investai scan --offline       # one scan cycle (synthetic data, no creds)
  python -m investai scan                 # one scan cycle (live Upstox)
  python -m investai run --offline -i 5    # loop every 5 min
  python -m investai report                # paper-trade performance
  python -m investai universe --offline    # sanity-check the universe
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .pipeline import run_report, run_scan
from .scheduler import run_loop


def _seed_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [s.strip().upper() for s in value.split(",") if s.strip()]


def cmd_auth(args) -> int:
    from pathlib import Path
    from .data.upstox import UpstoxAuth
    cfg = load_config(args.config)
    auth = UpstoxAuth.from_env(cfg.path("token_store"))
    if not (args.code or args.url):
        print("Step 1 — open this URL in a browser and log in to Upstox:\n")
        print("   " + auth.authorize_url() + "\n")
        print("Step 2 — after login you are redirected to your redirect_uri with\n"
              "         ?code=XXXX in the address bar. Re-run with that value:\n")
        print("   python -m investai auth --url \"<the full redirected URL>\"")
        print("   (or)  python -m investai auth --code XXXX\n")
        return 0
    code = args.code
    if args.url:
        vals = parse_qs(urlparse(args.url).query).get("code") or []
        code = vals[0] if vals else None
    if not code:
        print("Could not find ?code= in the provided URL.", file=sys.stderr)
        return 2
    rec = auth.exchange_code(code)
    print(f"Authenticated. Token stored for trading_date={rec['trading_date']}.")
    print("Re-run this each trading morning (tokens expire ~03:30 IST).")
    return 0


def cmd_scan(args) -> int:
    cfg = load_config(args.config)
    result = run_scan(cfg, offline=args.offline, seed=_seed_arg(args.seed))
    print(json.dumps(result, indent=None if args.compact else 2))
    return 0 if result.get("status") != "data_unavailable" else 1


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    run_loop(cfg, offline=args.offline, interval_minutes=args.interval,
             once=args.once, market_hours_only=not args.all_hours)
    return 0


def cmd_report(args) -> int:
    cfg = load_config(args.config)
    print(json.dumps(run_report(cfg), indent=2))
    return 0


def cmd_backtest(args) -> int:
    cfg = load_config(args.config)
    from .backtest.engine import Backtester
    from .pipeline import build_adapter
    adapter = build_adapter(cfg, offline=args.offline)
    if not adapter.is_ready():
        print(json.dumps({"status": "data_unavailable",
                          "error": f"adapter {adapter.name} not ready"}), file=sys.stderr)
        return 1
    bt = Backtester(cfg, adapter)
    seed = _seed_arg(args.seed) or cfg.get("universe", "seed", default=None)
    result = bt.run(seed, days=int(args.years * 365), interval=args.interval, split=args.split)
    out = result.summary()
    out["data_classification"] = adapter.classification
    out["adapter_used"] = adapter.name
    if args.trades:
        out["trades"] = result.trades
    print(json.dumps(out, indent=2))
    return 0


def cmd_research(args) -> int:
    cfg = load_config(args.config)
    from .backtest.matrix import run_matrix
    from .pipeline import build_adapter
    adapter = build_adapter(cfg, offline=args.offline)
    if not adapter.is_ready():
        print(json.dumps({"status": "data_unavailable",
                          "error": f"adapter {adapter.name} not ready"}), file=sys.stderr)
        return 1
    seed = _seed_arg(args.seed) or cfg.get("universe", "seed", default=None)
    result = run_matrix(cfg, adapter, seed, days=int(args.years * 365),
                        interval=args.interval, split=args.split)
    result["data_classification"] = adapter.classification
    print(json.dumps(result, indent=2))
    return 0


def cmd_factor(args) -> int:
    cfg = load_config(args.config)
    from .backtest.factor import run_factor_research
    from .pipeline import build_adapter
    adapter = build_adapter(cfg, offline=args.offline)
    if not adapter.is_ready():
        print(json.dumps({"status": "data_unavailable",
                          "error": f"adapter {adapter.name} not ready"}), file=sys.stderr)
        return 1
    res = run_factor_research(cfg, adapter, symbols=_seed_arg(args.seed),
                              years=args.years, split=args.split)
    print(json.dumps(res, indent=2, default=str))
    return 0 if res.get("verdict") else 1


def cmd_ingest_eodhd(args) -> int:
    import os
    cfg = load_config(args.config)
    from .data.eodhd import EODHDClient, ingest_nse
    try:
        client = EODHDClient(os.environ.get("EODHD_API_KEY", ""))
    except RuntimeError as e:
        print(json.dumps({"error": str(e),
                          "hint": "Set EODHD_API_KEY in .env after subscribing."}),
              file=sys.stderr)
        return 1
    summary = ingest_nse(cfg, client, start=args.start, limit=args.limit)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_ingest_bhavcopy(args) -> int:
    cfg = load_config(args.config)
    from .data.bhavcopy import build_adjusted, ingest_bhavcopy
    dl = ingest_bhavcopy(cfg, start=args.start, end=args.end)
    out = {"download": dl}
    if not args.no_adjust:
        out["adjust"] = build_adjusted(cfg)
    print(json.dumps(out, indent=2))
    return 0


def cmd_factor_sf(args) -> int:
    cfg = load_config(args.config)
    from .backtest.factor import run_factor_research_sf
    try:
        res = run_factor_research_sf(cfg, start=args.start, top_turnover=args.top,
                                     split=args.split, with_tax=args.tax)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, default=str))
    return 0


def cmd_dashboard(args) -> int:
    from pathlib import Path
    from .dashboard import generate
    from .pipeline import run_report, run_scan
    cfg = load_config(args.config)
    result = run_scan(cfg, offline=args.offline, seed=_seed_arg(args.seed))
    report = run_report(cfg)
    out = generate(cfg, result, report, Path(args.out))
    print(f"dashboard written: {out}  (status={result.get('status')})")
    if args.email:
        from . import notify
        if notify.is_configured(cfg):
            try:
                subj, html, text = notify.build_summary(cfg, result, report)
                info = notify.send(cfg, subj, html, text, attachment=out)
                print(f"email sent to {len(info['sent_to'])} recipient(s)")
            except Exception as e:  # noqa: BLE001 - never fail the run on email trouble
                print(f"[email] send failed: {type(e).__name__}: {e}", file=sys.stderr)
        else:
            print("[email] not configured (need email.enabled + EMAIL_FROM + "
                  "EMAIL_APP_PASSWORD + email.to)", file=sys.stderr)
    return 0 if result.get("status") != "data_unavailable" else 1


def cmd_universe(args) -> int:
    cfg = load_config(args.config)
    from .pipeline import build_adapter
    adapter = build_adapter(cfg, offline=args.offline)
    seed = _seed_arg(args.seed) or cfg.get("universe", "seed", default=None)
    insts = adapter.resolve_universe(seed)
    print(f"Resolved {len(insts)} instruments.")
    for i in insts[:15]:
        print(f"  {i.symbol:14} {i.instrument_key}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="investai", description="Paper-trading / research engine")
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth", help="Upstox OAuth (run once per trading day)")
    a.add_argument("--code", help="authorization code")
    a.add_argument("--url", help="full redirected URL containing ?code=")
    a.set_defaults(func=cmd_auth)

    s = sub.add_parser("scan", help="run one scan cycle")
    s.add_argument("--offline", action="store_true", help="use synthetic data (no creds)")
    s.add_argument("--seed", help="comma-separated symbols to restrict the universe")
    s.add_argument("--compact", action="store_true", help="single-line JSON")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("run", help="run the scan loop")
    r.add_argument("--offline", action="store_true")
    r.add_argument("-i", "--interval", type=float, default=5.0, help="minutes between scans")
    r.add_argument("--once", action="store_true", help="run a single cycle and exit")
    r.add_argument("--all-hours", action="store_true", help="ignore market-hours gate")
    r.set_defaults(func=cmd_run)

    rp = sub.add_parser("report", help="paper-trade performance report")
    rp.set_defaults(func=cmd_report)

    u = sub.add_parser("universe", help="resolve and print the universe")
    u.add_argument("--offline", action="store_true")
    u.add_argument("--seed", help="comma-separated symbols")
    u.set_defaults(func=cmd_universe)

    db = sub.add_parser("dashboard", help="run a scan and render the static HTML dashboard")
    db.add_argument("--offline", action="store_true", help="synthetic data (no network)")
    db.add_argument("--seed", help="comma-separated symbols")
    db.add_argument("--out", default="docs", help="output dir for index.html (default: docs)")
    db.add_argument("--email", action="store_true", help="email the summary to configured recipients")
    db.set_defaults(func=cmd_dashboard)

    b = sub.add_parser("backtest", help="historical backtest (reuses live decision logic)")
    b.add_argument("--offline", action="store_true", help="synthetic data (no network)")
    b.add_argument("--seed", help="comma-separated symbols (default: config universe)")
    b.add_argument("--years", type=float, default=3.0, help="lookback in years")
    b.add_argument("--interval", default="day", help="day | 30minute | 1minute")
    b.add_argument("--split", type=float, default=0.7, help="in-sample fraction")
    b.add_argument("--trades", action="store_true", help="include per-trade rows")
    b.set_defaults(func=cmd_backtest)

    rs = sub.add_parser("research", help="run the variant research matrix (A-E) and rank")
    rs.add_argument("--offline", action="store_true", help="synthetic data (no network)")
    rs.add_argument("--seed", help="comma-separated symbols (default: config universe)")
    rs.add_argument("--years", type=float, default=3.0, help="lookback in years")
    rs.add_argument("--interval", default="day", help="day | 30minute | 1minute")
    rs.add_argument("--split", type=float, default=0.7, help="in-sample fraction")
    rs.set_defaults(func=cmd_research)

    fc = sub.add_parser("factor", help="factor rotation research (LowVol->Mom, pure Mom) on NIFTY200")
    fc.add_argument("--offline", action="store_true", help="synthetic data (no network)")
    fc.add_argument("--seed", help="comma-separated symbols (default: frozen NIFTY200)")
    fc.add_argument("--years", type=float, default=10.0, help="lookback in years")
    fc.add_argument("--split", type=float, default=0.6, help="in-sample fraction (OOS = rest)")
    fc.set_defaults(func=cmd_factor)

    ib = sub.add_parser("ingest-bhavcopy", help="free survivorship-free NSE history from official Bhavcopy")
    ib.add_argument("--start", default="2016-01-01", help="start date YYYY-MM-DD")
    ib.add_argument("--end", default=None, help="end date YYYY-MM-DD (default: today)")
    ib.add_argument("--no-adjust", action="store_true", help="skip split/bonus adjustment step")
    ib.set_defaults(func=cmd_ingest_bhavcopy)

    ie = sub.add_parser("ingest-eodhd", help="pull survivorship-free NSE history (needs EODHD_API_KEY)")
    ie.add_argument("--start", default="2010-01-01", help="history start date YYYY-MM-DD")
    ie.add_argument("--limit", type=int, default=None, help="cap symbols (for a test pull)")
    ie.set_defaults(func=cmd_ingest_eodhd)

    fs = sub.add_parser("factor-sf", help="survivorship-free factor research (uses EODHD store)")
    fs.add_argument("--start", default=None, help="restrict history start YYYY-MM-DD")
    fs.add_argument("--top", type=int, default=200, help="point-in-time universe size (by turnover)")
    fs.add_argument("--split", type=float, default=0.6, help="in-sample fraction (OOS = rest)")
    fs.add_argument("--tax", action="store_true", help="model India STCG/LTCG tax drag")
    fs.set_defaults(func=cmd_factor_sf)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
