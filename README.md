# InvestAI

A disciplined, **paper-trading-first** market-analysis and trade-decision engine for
NSE equities. The design separates two jobs that must not be confused:

- **Deterministic Python** does the heavy, reproducible work — fetch data, compute
  indicators, score and rank the universe, size positions, enforce the risk policy,
  simulate fills, track outcomes.
- **The reasoning layer** (a rule engine today; Claude optionally, behind the same
  interface) judges only the *small pre-ranked candidate set* — never the whole
  universe. It earns its keep on judgement and the learn-from-outcomes loop, not on
  arithmetic.

```
UpstoxAdapter ──▶ IndicatorEngine ──▶ Scanner/ranker (top-N)
                                            │
                                            ▼
                                     ReasoningEngine  (rule | anthropic)
                                            │
                                            ▼
                                     PaperLedger ──▶ Tracker (win%, PF, expectancy, R, DD)
```

> **Safety:** default mode is `PAPER`. The engine never places real orders. Live
> execution would require human approval and validation gates that are intentionally
> not implemented here.

---

## Quickstart — runs today, no broker account

Set up the env (conda or venv):

```bash
# conda (recommended)
conda create -p .conda/envs/investai python=3.12 -y
.conda/envs/investai/python.exe -m pip install -r requirements.txt
# or venv:  python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

Then run the autonomous scan. **No credentials needed** — with Upstox unauthenticated
it automatically falls back to real (≈15-min delayed) Yahoo Finance NSE data:

```bash
python -m investai scan            # REAL NSE data via YFinance fallback -> JSON
python -m investai report          # paper-trade performance + open positions
python -m investai run -i 5         # loop every 5 min during market hours
python -m investai scan --offline   # synthetic data (no network) — plumbing test
```

`scan` prints schema-compliant JSON to **stdout** (diagnostics go to stderr, so the
output pipes cleanly) and opens compliant paper trades into `data/paper.db`. Delete
that file to reset the paper account.

> Every opportunity is tagged `REAL_MARKET_DATA` or `SIMULATED_DATA`. Offline/synthetic
> data is a **plumbing test only** — a seeded random walk, not a source of edge.

## Adapters & automatic fallback

The engine depends only on the `DataAdapter` interface. Selection priority
(`pipeline.build_adapter`), no user intervention required:

| Priority | Adapter | Feed | Classification | When |
|---|---|---|---|---|
| `--offline` | `SyntheticAdapter` | simulated | `SIMULATED_DATA` | explicit flag |
| 1 | `UpstoxAdapter` | realtime | `REAL_MARKET_DATA` | Upstox token present |
| 2 | `YFinanceAdapter` | delayed | `REAL_MARKET_DATA` | automatic fallback |

`YFinanceAdapter` calls Yahoo's public v8 chart API directly via `requests` (no
`yfinance` package / compiled deps), with throttling, retry+backoff, and graceful
per-symbol failure. Every scan reports an `adapter_status` block:
`{adapter, feed_type, market_status, data_quality}`.

---

## Going live on Upstox (real NSE data)

### 1. Register an API app (2 minutes)
1. Go to **https://account.upstox.com/developer/apps** and create a new app.
2. Set a **Redirect URI** you control, e.g. `https://127.0.0.1:5000/callback`
   (the page there doesn't need to load — you only read the `?code=` it receives).
3. Copy the **API Key** and **API Secret**.

### 2. Configure credentials
```bash
cp .env.example .env
# edit .env:
#   UPSTOX_API_KEY=...
#   UPSTOX_API_SECRET=...
#   UPSTOX_REDIRECT_URI=https://127.0.0.1:5000/callback   # must match the app exactly
```

### 3. Authenticate (once per trading day)
```bash
python -m investai auth
# -> prints a login URL. Open it, log in to Upstox.
# -> your browser redirects to your redirect_uri with ?code=XXXX in the address bar.
python -m investai auth --url "https://127.0.0.1:5000/callback?code=XXXX&state=investai"
```
Upstox access tokens **expire daily (~03:30 IST)**, so `auth` must be re-run each
trading morning. The token is cached in `data/.token.json` (git-ignored, chmod 600).

### 4. Scan live
```bash
python -m investai universe          # sanity-check the resolved NSE universe
python -m investai scan              # one live scan cycle
python -m investai run -i 5          # loop every 5 min during market hours (09:15-15:30 IST)
```

---

## Configuration — `config.yaml`

| Block | Key | Meaning |
|---|---|---|
| `risk` | `max_risk_per_trade_pct` | per-trade risk (default **1.0%**) |
| | `max_daily_loss_pct` | daily circuit-breaker (**3.0%**) |
| | `max_portfolio_risk_pct` | total open risk cap (**5.0%**) |
| | `min_reward_risk` | minimum **net** RR after costs (**2.0**) |
| | `max_open_positions` / `max_correlated_positions` | concentration caps |
| `scanner` | `top_n`, `min_avg_volume`, `min_price` | triage + liquidity floor |
| `execution` | `fee_pct`, `slippage_pct` | baked into RR and into paper fills |
| `reasoning` | `engine` | `rule` (default) or `anthropic` |
| `universe` | `seed` | restrict to a symbol list; empty = full NSE equity |

These numbers are enforced, not advisory. The rule engine rejects any trade whose
**cost-adjusted** reward:risk is below `min_reward_risk`, and the pipeline refuses to
open a position that would breach the daily-loss or portfolio-risk caps.

---

## CLI

| Command | Purpose |
|---|---|
| `investai auth [--url … \| --code …]` | Upstox OAuth (daily) |
| `investai scan [--offline] [--seed A,B] [--compact]` | one scan cycle → JSON |
| `investai run [--offline] [-i MIN] [--once] [--all-hours]` | scan loop |
| `investai report` | paper-trade performance + open positions |
| `investai universe [--offline] [--seed …]` | resolve & print the universe |

---

## Swapping in Claude as the reasoner

Set `reasoning.engine: anthropic` in `config.yaml` and `ANTHROPIC_API_KEY` in `.env`.
`investai/reasoning/anthropic_engine.py` calls Claude over the deterministic baseline
plan and is clamped by the same risk policy. **It is EXPERIMENTAL and not covered by
the test suite** — validate it against the rule engine before relying on it. The
pipeline falls back to the rule engine automatically if the API path fails.

---

## What is intentionally NOT here yet

Stated plainly so nothing is oversold:

- **No historical backtester.** This is a paper-*forward* engine. A backtest module
  (win rate / profit factor / expectancy / max DD / in-vs-out-of-sample) is the next
  build, reusing the same indicator + reasoning + sizing code.
- **Mark-to-market uses the last price**, not intrabar high/low, so on daily candles a
  gap can fill beyond the modelled stop. Use intraday candles for fill accuracy.
- **Sector is blank** in the Upstox instruments master, so the correlation/sector cap
  is inert until a sector map is supplied.
- The Upstox v2 historical-candle endpoint is marked *deprecated* by Upstox (v3
  exists). The adapter targets v2 for stability; swapping the URL is a one-line change.
- **News / sentiment / fundamentals are not wired.** The schema has slots for them;
  the data sources are not connected.

---

## Tests

```bash
python -m pytest -q     # 26 tests: indicators, sizing, rule engine, ledger, pipeline
```
