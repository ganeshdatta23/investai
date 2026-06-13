# InvestAI — Research Log

**Discipline.** Every hypothesis is *pre-registered* before any backtest runs: the
premise, universe, exact rules, test period, **train/held-out split**, and
**pass/fail success criteria** are fixed in writing first. Verdicts are recorded
pass *or* fail with **no moving goalposts**. This log also counts how many
hypotheses have been tested — the multiple-testing budget — so we don't data-mine
our way into a fake edge. A backtest that "passes" only after N tweaks is not an
edge; it's overfitting.

---

## Hypothesis #1 — Trend/momentum on NIFTY large-caps — **STATUS: DISPROVEN / FROZEN** (2026-06-13)

**Premise.** EMA-stack trend-following with momentum/volume/breakout confirmations
has a tradeable edge on liquid NSE large-caps.

**Rules (frozen).** EMA20/50/200 alignment + MACD + RSI + VWAP + ADX + volume-spike
+ 20d breakout confirmations; ATR×1.5 stops, 2.5R/4.0R targets; 1% risk/trade,
5% portfolio cap, 3% daily-loss breaker; entry requires cost-adjusted RR ≥ 2.0.

**Tests (real Yahoo NSE data, 19 large-caps, 2023-06-14 → 2026-06-12, fee 0.03% /
slippage 0.05%):**

| Test | Trades | PF | Return | Max DD | IS PF / OOS PF |
|---|---|---|---|---|---|
| Baseline (long+short) | 211 | 0.79 | −27.5% | −30.5% | 0.78 / 0.80 |
| Variant A long-only | 199 | 0.94 | — | −33.6% | — / 0.69 |
| Variant B long+regime | 166 | **0.97** | — | −34.6% | — / 0.96 |
| Variant C long+regime+ADX25 | 137 | 0.89 | — | −26.3% | — / 1.11 |
| Variant D long+regime+RS | 141 | 0.81 | — | −32.9% | — / 0.97 |
| Variant E long+regime+RS+ADX25 | 113 | 0.87 | — | −24.5% | — / 1.42 |

**Benchmark (same window).** NIFTY 50 buy-and-hold: **+25.9% total / +8.0% CAGR** —
beat every variant. Baseline was unprofitable even at **zero** costs (PF 0.81).

**Verdict.** All variants PF < 1.0 → **FAILURE_NO_EDGE**. The two OOS PF > 1.0
readings (C, E) are small-sample noise with *worse* in-sample numbers — explicitly
NOT treated as edge. This is a **non-edge hypothesis** on this universe.

**Decision.** FROZEN. Do not deploy, do not fund a broker, do not tune these knobs
further. Reproduce with `investai research --years 3`.

**Multiple-testing budget used:** 1 hypothesis (6 configurations).

---

## Hypothesis #2 (mid-cap momentum) — **SUPERSEDED** → generalized into the NIFTY200 factor research below (#2 LowVol→Mom, #3 Pure Mom).

<details><summary>original mid-cap proposal (kept for record)</summary>

**Premise.** Cross-sectional momentum (relative strength) persists over 3–12 month
horizons and is historically *strongest in mid-caps* — precisely where Hypothesis #1
(large-cap trend) was weakest. Owning recent relative winners and rotating should
beat naively holding the basket.

**Why this is a different model, not a re-tune.** This is a **monthly-rebalanced,
equal-weight rotation**, NOT the ATR-stop engine from #1. Testing the momentum
*premise* requires the canonical rotation construction; reusing the stop engine
would test a different thing. New portfolio model; same cost assumptions.

**Universe (frozen before run).** The current Nifty Midcap 150 constituents,
recorded as an explicit symbol list in this log before the verdict run. Liquidity
filter: 20-day avg traded value ≥ ₹25 cr; price ≥ ₹20. *Survivorship bias
acknowledged* (today's constituents) — reported as a known limitation, not hidden.

**Signal.** 12-1 momentum = total return from t−252 to t−21 trading days (skip the
last month to avoid short-term reversal). Require ≥ 273 bars of history per name.

**Construction (all fixed, theory-driven, NOT fitted).**
- Rebalance every 21 trading days.
- Rank universe by 12-1 momentum; hold the **top N = 15**, **equal weight** (1/15 of
  equity each), **long only**.
- **Regime gate:** if NIFTY 50 < its 200-EMA on the rebalance date → hold **cash**
  (exit all, take no new entries). Momentum crashes happen in bear regimes; this is
  part of the hypothesis, declared up front.
- Costs: fee 0.03% + slippage 0.05% per side, applied on every rebalance turnover.

**Periods.** Train/sanity: 2012-01-01 → 2018-12-31 (no parameter fitting — used only
to confirm the code behaves). **Held-out verdict period: 2019-01-01 → 2026-06-12**,
not inspected until the rules above are frozen. (Per-symbol history may shorten the
universe in early years; reported.)

**Benchmark.** Equal-weight buy-and-hold of the *same universe* over the held-out
period (isolates whether selection+timing adds value over owning everything). NIFTY 50
and, if retrievable, the Nifty Midcap index reported as secondary references.

**Success criteria — ALL must hold on the HELD-OUT period (no partial credit):**
1. OOS CAGR > equal-weight-hold-all benchmark CAGR (net of costs).
2. OOS Sharpe ≥ 0.75.
3. OOS max drawdown ≤ benchmark max drawdown.
4. ≥ 40 rebalances executed (breadth).

If any one fails → **REJECTED**, logged as disproven, no re-tuning.

**Multiple-testing budget:** this is hypothesis **2 of a hard cap of 3**.

</details>

---

## Hypotheses #2 & #3 — NIFTY200 factor rotation — **STATUS: RUN — BOTH FAIL pre-registered bar** (2026-06-13)

**Result (193/204 symbols, 2017-07 → 2026-06, OOS from 2022-06; `investai factor --years 10 --split 0.6`):**

| Config | OOS CAGR | OOS Sharpe | OOS maxDD | OOS PF | vs eqw-basket CAGR (27.6%) | Verdict |
|---|---|---|---|---|---|---|
| #3 Pure Momentum 12-1 | 35.9% | 1.70 | **−22.9%** | 6.85 | beats (+8.3) | **FAIL** — DD breached −20% |
| #2 LowVol→Momentum | 15.2% | 1.42 | −16.4% | 3.49 | **misses** (−12.4) | **FAIL** — below basket |

**Verdict: FAILURE_NO_EDGE** per the pre-registered bar (each missed exactly one of the
four criteria; no goalpost-moving). BUT note the failure *mode* differs sharply from #1:
momentum showed real risk-adjusted strength (Sharpe 1.7, PF 6.85, beat the basket) and
failed only on drawdown. **This near-miss is almost certainly inflated by the
survivorship + look-ahead bias** (today's NIFTY200 winners applied to 2017-2026 loads the
sample with stocks that *became* winners). The result is therefore NOT trustworthy as
evidence of edge. Pursuing momentum legitimately requires a **point-in-time / survivorship-
free universe + walk-forward** — a data dependency, NOT a loosening of the DD knob (that
would be the data-mining we swore off).

**Cap of 3 hypotheses reached.** Disciplined default: no validated edge → index/cash.
PEAD remains untested (no point-in-time fundamentals).

---

## Data-quality follow-up — survivorship-free re-test of H2/H3 (in progress, 2026-06-13)

Decision: fix the survivorship bias and re-test (not another strategy).
- **EODHD (paid) abandoned:** the subscribed plan returns NO India data (exchange list
  has 72 exchanges, none NSE/BSE; RELIANCE.NSE etc. = "Ticker Not Found"). India is
  gated/licensed separately. → user advised to refund. Built EODHD client is unused.
- **Pivot → free NSE Bhavcopy (`data/bhavcopy.py`):** official daily files list EVERY
  equity that traded (incl. later-delisted) → survivorship-free by construction.
  Handles UDiFF (≥2024-07-08) + legacy formats. Split/bonus adjustment is SELF-CONTAINED
  via `prevclose[t]/close[t-1]` ratios (no separate corporate-actions feed; dividends not
  adjusted — documented limitation). Shared store `data/prices.db` (`pricestore.py`).
- Point-in-time universe = top-N by trailing turnover each rebalance (`univ_top_turnover`).
- Run: `ingest-bhavcopy` → `factor-sf`. 54 tests pass incl. split-adjustment correctness.
### Adjustment bug caught by spot-check (then fixed)
First adjustment used Bhavcopy `prevclose/prior-close` — but **NSE prevclose is NOT
split-adjusted** (=prior close on ex-date), so EVERY split was missed (NESTLEIND 1:10
showed −90% in raw AND "adjusted"). The named-event spot-check caught it before any
verdict. Fixed: detect CAs from the **open gap** (`open[t]/close[t-1]`), gated above
NSE's ~20% circuit band (so genuine moves like Adani −28% are kept), snapped to clean
split/bonus ratios else the raw gap. Verified: NESTLEIND/IRCTC/BAJFINANCE/RELIANCE/
TATAMOTORS/NYKAA all neutralized; only residual >35% drops are 3 REAL crashes
(YESBANK Mar-2020 moratorium, ADANIPOWER Hindenburg, IDEA) — correctly left alone.

### Survivorship-free result (`factor-sf`, 2,340 symbols, 2021→2026, OOS from 2023-11)
| Config | OOS CAGR | Sharpe | maxDD | PF | vs eqw-basket (11.2%) | Verdict |
|---|---|---|---|---|---|---|
| **H2sf LowVol→Mom** | 12.65% | 1.30 | **−14.7%** | 1.57 | beats (+1.5) | **PASS (all 4)** |
| H3sf Pure Mom | 16.37% | 0.80 | **−23.4%** | 1.27 | beats (+5.2) | FAIL — DD breached |

**H2sf (defensive-first momentum) PASSED the pre-registered OOS bar on trustworthy data.**
De-biasing collapsed the benchmark 27.6%→11.2% (the bias was inflating everything); H2
now *beats* its honest benchmark with far lower drawdown. Year-by-year: +20/−3/+29/+16/
+2/−1%; rolling-1y 77% positive, worst −13%.

**STATUS (provisional): EDGE_CANDIDATE** — but flagged as fragile (short 2.5y OOS in a
mid-cap bull, thin +1.5pt edge, taxes not yet modelled). Next: extend to 2016 + model tax.

### FINAL: extended to 2016 + tax-modelled → REJECTED (2026-06-13)
Re-ran H2sf/H3sf UNCHANGED on full 2016-2026 (3,452 symbols, OOS 2022-04 → 2026-06, ~4y),
clean and with India STCG 20% / LTCG 12.5% tax drag, same 0.6 split:

| Config | OOS CAGR (no-tax) | OOS CAGR (tax) | maxDD | PF | eqw-benchmark CAGR | Verdict |
|---|---|---|---|---|---|---|
| H2sf LowVol→Mom | 9.55% | **5.24%** | −17.8% | 2.62 | **12.87%** | FAIL (beats-benchmark) |
| H3sf Pure Mom | 10.2% | **1.35%** | −34.5% | 2.21 | 12.87% | FAIL (DD + benchmark) |

**The earlier "pass" was a short-OOS artifact.** Over the longer, representative window the
equal-weight liquid basket returned **12.87%** while H2sf made only 9.55% (no-tax) — active
selection SUBTRACTED value vs passive. Tax then cut H2sf to 5.24% and H3sf to 1.35%
(monthly rebalancing in India is tax-brutal: ~4-9 pts/yr drag). **Both FAIL the
pre-registered OOS bar, with and without tax. VERDICT: FAILURE_NO_EDGE.**

### Project conclusion
3 hypotheses tested (trend; lowvol-mom; pure-mom) across survivor-biased → survivorship-free
→ tax-modelled. **None clears a trustworthy bar vs passive over a representative window.**
Per the pre-registered rule: **stop. No deployable edge. Low-cost index fund / cash is the
rational choice.** The engine + survivorship-free data pipeline remain for any future
pre-registered hypothesis. This is a SUCCESS for a capital-preservation-first system: it
rigorously searched and refused to deploy a losing/illusory edge.

**Premise.** Price-based factor premia (momentum; low-volatility) are documented in
Indian equities. Test two long-only, monthly-rebalanced rotation strategies.

**Universe (frozen).** Current NIFTY 200 constituents (`backtest/universes.py`,
pulled 2026-06-13 from NSE). **Known bias: survivorship + look-ahead** (today's
membership applied to the past) → results are OPTIMISTIC; a FAIL is therefore strong.

**Configs (fixed — verdict is on these, no grid search).**
- **#2 LowVol→Momentum:** 60 lowest 12m-vol → top-30 by 6m (skip-1) momentum.
- **#3 Pure Momentum:** top-30 by 12-1m momentum.
- Both: equal-weight, monthly (21-day) rebalance, long-only, cash when NIFTF50 < 200-EMA.

**Costs.** fee 0.03% + slippage 0.05% per side, charged on rebalance turnover.
Raw close (ex-dividend) used consistently for strategy AND benchmark.

**Validation.** Single time-split, in-sample 60% / **held-out 40%**. (Walk-forward +
Monte Carlo deferred to a follow-up if a config passes this first gate.)

**Benchmark.** Equal-weight buy-and-hold of the same universe (does selection +
timing beat owning everything?).

**Acceptance (ALL must hold OUT-OF-SAMPLE, else REJECTED):**
1. profit factor ≥ 1.2  2. expectancy > 0  3. max drawdown > −20%  4. CAGR > benchmark CAGR.

**Not tested — PEAD (earnings drift):** requires point-in-time earnings/consensus
data not available in the free feed; deferred until a fundamentals source exists.

**Multiple-testing budget:** these are hypotheses **2 and 3 — the cap of 3 is now
reached.** If both fail, the disciplined conclusion is "no edge found → index/cash."


