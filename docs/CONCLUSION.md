# InvestAI — Investigation Conclusion

**Question asked:** Is there a systematic, rules-based equity strategy on Indian (NSE)
markets that this engine could deploy with real capital?

**Answer:** **No edge found that survives honest testing. The disciplined, evidence-based
outcome is to hold a low-cost broad index fund (or cash) — not to trade actively.**

This document is the permanent record. The full per-step audit trail is in
[`RESEARCH_LOG.md`](RESEARCH_LOG.md).

---

## What was built

A complete, paper-trading-first research engine (Python, 56 passing tests):

- **Data layer (vendor-independent):** live scan via Upstox→YFinance fallback; and a
  **free, survivorship-free NSE price history** assembled from official daily Bhavcopy
  (3,452 symbols incl. delisted, 2016–2026, 4.4M bars), with self-contained
  split/bonus adjustment.
- **Deterministic core:** indicators, scanner, ATR risk/sizing, rule engine, paper ledger.
- **Backtesters:** event-driven (no-lookahead) + cross-sectional factor rotation, with
  in/out-of-sample split, walk-forward stability, fee/slippage sensitivity, and an
  India capital-gains **tax-drag** model.
- **Discipline scaffolding:** pre-registration, a 3-hypothesis cap, and a running log.

---

## What was tested, and what happened

| # | Hypothesis | Best honest result | Verdict |
|---|---|---|---|
| 1 | Trend/momentum, NIFTY large-caps (long/short, then long-only A–E matrix) | 3y PF **0.79**, −27.5%; matrix all PF < 1.0 | ❌ No edge — NIFTY buy-hold (+25.9%) beat it |
| 2 | Low-Vol → Momentum ("defensive-first momentum") | See below | ❌ Rejected after full testing |
| 3 | Pure 12-1 cross-sectional momentum | See below | ❌ Rejected (drawdown + benchmark) |

**The H2/H3 escalation (the rigorous part):**

| Stage | H2 result | Why it didn't count |
|---|---|---|
| Survivor-biased NIFTY200 (2017–26) | Failed bar | Today's index applied to the past = inflated |
| Survivorship-free (2020–26, OOS 2.5y) | **Passed** (CAGR 12.7% vs 11.2%) | Short OOS in a mid-cap **bull** |
| Survivorship-free (2016–26, OOS 4y) | CAGR **9.55%** vs benchmark **12.87%** | Active **subtracted** value vs passive |
| + India tax (STCG 20% / LTCG 12.5%) | CAGR **5.24%** | Monthly rebalancing taxed brutally |

**Final: both H2 and H3 FAIL the pre-registered out-of-sample bar, with and without tax.**

---

## Five lessons worth keeping

1. **Survivorship + look-ahead bias inflates everything.** Removing it collapsed the
   benchmark from a fantasy 27.6%/yr to an honest ~12.9%/yr. Most "amazing backtests"
   on current index constituents are this illusion.
2. **A short out-of-sample window lies.** H2 *passed* on a 2.5-year bull OOS and *failed*
   on the 4-year window that included a correction. One lucky regime is not evidence.
3. **Data adjustment is where silent corruption hides.** NSE's `prevclose` is **not**
   split-adjusted; the first method missed every split (a 1:10 showed as −90%). A
   named-event **spot-check** caught it before it poisoned the verdict. Always spot-check.
4. **Taxes are a first-order cost.** Monthly rebalancing in India bled ~4–9 percentage
   points/year to short-term capital gains tax. Frequency is expensive.
5. **Active selection lost to passive exposure.** Broad mid/large-cap *exposure* did well
   (~12.9%/yr); *timing and selection* destroyed value. That is an argument **for** a cheap
   index, held.

---

## Recommendation

- **Do not deploy active trading. Do not fund a broker. Do not go live.**
- For the capital: a **low-cost broad-market index fund / ETF**, held — the evidence says
  it beat every active variant tested, after costs and taxes, with less effort and risk.
- **Refund the EODHD subscription** — it does not cover India (verified against the key).
- **Stop searching for now.** We hit the 3-hypothesis cap; chasing a "pass" beyond it is
  data-mining noise. The rig is ready if a *genuinely new, pre-registered* hypothesis
  (different asset class, or a real fundamental signal with point-in-time data) ever
  appears.

---

## Why this is a success, not a failure

The engine's first duty is **capital preservation**. It did exactly that: it searched
rigorously and **refused to deploy an illusory edge**, catching — before a single rupee
moved — a short-window false pass, a catastrophic split-adjustment bug, survivorship bias,
and tax drag. A system that says "don't trade this" when the evidence says so is working
as designed.

*Engine, survivorship-free data pipeline, backtesters, and audit log retained for future
use. 56 tests passing.*
