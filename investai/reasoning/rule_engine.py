"""Deterministic decision engine. Applies the full decision framework + every
risk gate, and emits a schema-compliant Decision.

Design choices, stated plainly:
- Stops are ATR-based (1.5x ATR). Targets are placed at 2.5R / 4.0R.
- The reward:risk FILTER uses the COST-ADJUSTED reward:risk (fees + slippage),
  so thin-volatility setups whose edge is eaten by costs are rejected.
- "Prefer HOLD over weak BUY/SELL": a BUY/SELL needs >= MIN_CONFIRMATIONS
  confirmations AND a clean trend AND a positive net edge AND room in the risk
  budget. Anything mixed becomes HOLD. Poor/illiquid setups become AVOID.
- The engine never fabricates: if there is no valid stop basis it returns
  WAIT_FOR_CONFIRMATION rather than inventing one.
"""
from __future__ import annotations

from ..schemas import Action, Candidate, Decision
from ..sizing import net_reward_risk, position_size, reward_risk
from .base import PortfolioContext, ReasoningEngine

ATR_STOP_MULT = 1.5
T1_R = 2.5
T2_R = 4.0
MIN_CONFIRMATIONS = 4
RSI_EXTREME_HI = 78.0
RSI_EXTREME_LO = 22.0
EXTENDED_PCT = 10.0
CHOPPY_ADX = 18.0
HIGH_VOL_ATR_PCT = 6.0


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


class RuleEngine(ReasoningEngine):
    def __init__(self, cfg):
        self.cfg = cfg
        self.risk = cfg.risk
        self.costs = cfg.costs
        self.equity = cfg.equity
        self.timeframe = str(cfg.get("data", "candle_interval", default="day"))
        self.min_avg_volume = float(cfg.get("scanner", "min_avg_volume", default=100000))

    def decide(self, candidate: Candidate, portfolio: PortfolioContext) -> Decision:
        f = candidate.features
        d = Decision(symbol=candidate.symbol, timeframe=self.timeframe)
        price = candidate.last_price
        atr = f.get("atr14")
        adx = f.get("adx14")
        rsi = f.get("rsi14")
        avg_vol = f.get("avg_vol20") or 0.0
        direction = f.get("direction", "none")

        d.trend_state = self._trend_state(f)
        d.market_regime = self._regime(f)
        d.key_factors = list(f.get("score_reasons", []))[:8]

        # --- Hard gates: missing core inputs / no stop basis -> WAIT ----------
        if price is None:
            d.action = Action.WAIT.value
            d.red_flags.append("missing_current_price")
            d.next_action = "Provide current price / valid quote."
            return d
        if atr is None or f.get("bars", 0) < 60:
            d.action = Action.WAIT.value
            d.red_flags.append("insufficient_history_no_stop_basis")
            d.decision_rationale = "No ATR/stop basis: not enough bars to define risk."
            d.next_action = "Fetch >= 60 bars of history, then re-evaluate."
            return d
        if direction == "none":
            d.action = Action.HOLD.value
            d.decision_rationale = "No directional structure (flat EMA stack)."
            d.next_action = "Wait for trend to develop."
            return d

        # --- Liquidity gate (execution reality) ------------------------------
        if avg_vol < self.min_avg_volume:
            d.action = Action.AVOID.value
            d.red_flags.append(f"low_liquidity_avgvol_{avg_vol:.0f}")
            d.decision_rationale = "20-day average volume below liquidity floor."
            d.next_action = "Skip — insufficient liquidity for clean execution."
            return d

        # --- Build the trade plan (ATR-based) --------------------------------
        rps = ATR_STOP_MULT * atr
        if direction == "long":
            entry, stop = price, price - rps
            t1, t2 = entry + T1_R * rps, entry + T2_R * rps
        else:
            entry, stop = price, price + rps
            t1, t2 = entry - T1_R * rps, entry - T2_R * rps

        plan_rr = reward_risk(entry, stop, t1, direction)
        net_rr = net_reward_risk(entry, stop, t1, self.costs, direction)
        sizing = position_size(self.equity, self.risk.max_risk_per_trade_pct, entry, stop)

        d.entry_price = _r(entry)
        d.stop_loss = _r(stop)
        d.target_1 = _r(t1)
        d.target_2 = _r(t2)
        d.reward_risk = _r(net_rr)
        d.invalidations = [
            f"Close beyond stop {_r(stop)} invalidates the setup",
            f"Loss of trend ({d.trend_state}) / EMA stack flip",
        ]

        # --- Confirmations ----------------------------------------------------
        n_conf, conf_factors = self._confirmations(f, direction)
        d.key_factors = (conf_factors + d.key_factors)[:10]

        # --- Quality red flags ------------------------------------------------
        atr_pct = f.get("atr_pct")
        dist = f.get("dist_ema20_pct")
        extended = False
        if rsi is not None and ((direction == "long" and rsi > RSI_EXTREME_HI)
                                or (direction == "short" and rsi < RSI_EXTREME_LO)):
            d.red_flags.append(f"rsi_extreme_{rsi:.0f}"); extended = True
        if dist is not None and abs(dist) > EXTENDED_PCT:
            d.red_flags.append(f"extended_from_ema20_{dist:.0f}pct"); extended = True
        if adx is not None and adx < CHOPPY_ADX:
            d.red_flags.append(f"choppy_adx_{adx:.0f}")
        if atr_pct is not None and atr_pct > HIGH_VOL_ATR_PCT:
            d.red_flags.append(f"high_volatility_atr_{atr_pct:.1f}pct")

        # --- Decision logic ---------------------------------------------------
        d.position_size = None
        d.risk_per_trade_percent = None

        # 0. Daily loss circuit-breaker (portfolio-level).
        if portfolio.daily_realized_loss_pct >= self.risk.max_daily_loss_pct:
            d.action = Action.HOLD.value
            d.confidence = 0
            d.setup_quality = self._grade(n_conf, net_rr, adx)
            d.red_flags.append("daily_loss_limit_hit")
            d.decision_rationale = (
                f"Daily loss limit ({self.risk.max_daily_loss_pct}%) reached; "
                "no new risk today regardless of setup quality.")
            d.next_action = "Stand down until next session."
            return d

        # 1. No stop basis / zero size -> cannot risk-size this trade.
        if sizing.quantity <= 0:
            d.action = Action.HOLD.value
            d.confidence = self._confidence(candidate, n_conf, downgrade=True)
            d.setup_quality = self._grade(n_conf, net_rr, adx)
            d.red_flags.append("position_size_zero_risk_too_large_per_share")
            d.decision_rationale = (
                "Per-share risk too large for the 1% risk budget at this equity; "
                "position rounds down to zero.")
            d.next_action = "Skip or wait for a tighter stop / more equity."
            return d

        # 2. Edge gate: cost-adjusted RR below policy minimum.
        if net_rr is None or net_rr < self.risk.min_reward_risk:
            d.action = Action.HOLD.value
            d.confidence = self._confidence(candidate, n_conf, downgrade=True)
            d.setup_quality = self._grade(n_conf, net_rr, adx)
            d.red_flags.append(f"net_rr_below_min_{_r(net_rr)}")
            d.decision_rationale = (
                f"Net reward:risk {_r(net_rr)} < required {self.risk.min_reward_risk} "
                "after fees/slippage. No edge.")
            d.next_action = "Reject — costs erode the edge."
            return d

        # 3. Choppy / extended -> prefer HOLD.
        if (adx is not None and adx < CHOPPY_ADX) or extended:
            d.action = Action.HOLD.value
            d.confidence = self._confidence(candidate, n_conf, downgrade=True)
            d.setup_quality = self._grade(n_conf, net_rr, adx)
            d.decision_rationale = (
                "Trend ambiguous (low ADX) or price over-extended from mean; "
                "waiting avoids a poor entry.")
            d.next_action = "Wait for pullback / trend confirmation."
            return d

        # 4. Portfolio fit gates (only block an otherwise-actionable trade).
        block = self._portfolio_block(candidate, sizing.risk_pct_used, portfolio)
        if block:
            d.action = Action.HOLD.value
            d.confidence = self._confidence(candidate, n_conf, downgrade=True)
            d.setup_quality = self._grade(n_conf, net_rr, adx)
            d.red_flags.append(block)
            d.decision_rationale = f"Setup valid but blocked by portfolio rule: {block}."
            d.next_action = "Free up risk budget or skip to preserve diversification."
            return d

        # 5. Strong, risk-compliant setup -> BUY / SELL.
        if n_conf >= MIN_CONFIRMATIONS:
            d.action = Action.BUY.value if direction == "long" else Action.SELL.value
            d.position_size = sizing.quantity
            d.risk_per_trade_percent = _r(sizing.risk_pct_used)
            d.confidence = self._confidence(candidate, n_conf)
            d.setup_quality = self._grade(n_conf, net_rr, adx)
            d.decision_rationale = (
                f"{direction.title()} setup: {n_conf} confirmations, net RR {_r(net_rr)}, "
                f"ADX {adx:.0f}. Risk {_r(sizing.risk_amount)} "
                f"({_r(sizing.risk_pct_used)}% of equity) on {sizing.quantity} shares.")
            d.next_action = (
                f"PAPER {d.action} {sizing.quantity} @ ~{_r(entry)}, stop {_r(stop)}, "
                f"T1 {_r(t1)}. Human approval required for any live order.")
            return d

        # 6. Otherwise mixed -> HOLD.
        d.action = Action.HOLD.value
        d.confidence = self._confidence(candidate, n_conf, downgrade=True)
        d.setup_quality = self._grade(n_conf, net_rr, adx)
        d.decision_rationale = (
            f"Only {n_conf}/{MIN_CONFIRMATIONS} confirmations; edge present but not "
            "strong enough to act. Prefer HOLD over a weak entry.")
        d.next_action = "Add to watchlist; act on one more confirmation."
        return d

    # ------------------------------------------------------------------ #
    def _confirmations(self, f: dict, direction: str) -> tuple[int, list[str]]:
        rsi = f.get("rsi14")
        adx = f.get("adx14")
        vspike = f.get("vol_spike") or 0.0
        checks: list[tuple[str, bool]] = []
        if direction == "long":
            checks = [
                ("trend_stack_up", bool(f.get("uptrend_stack"))),
                ("macd_positive", (f.get("macd_hist") or 0) > 0),
                ("above_vwap", bool(f.get("above_vwap"))),
                ("adx_ge_22", adx is not None and adx >= 22),
                ("volume_confirm", vspike >= 1.3),
                ("near_breakout", bool(f.get("near_20d_high"))),
                ("rsi_constructive", rsi is not None and 50 <= rsi <= 70),
            ]
        else:
            checks = [
                ("trend_stack_down", bool(f.get("downtrend_stack"))),
                ("macd_negative", (f.get("macd_hist") or 0) < 0),
                ("below_vwap", not f.get("above_vwap", True)),
                ("adx_ge_22", adx is not None and adx >= 22),
                ("volume_confirm", vspike >= 1.3),
                ("near_breakdown", bool(f.get("near_20d_low"))),
                ("rsi_constructive", rsi is not None and 30 <= rsi <= 50),
            ]
        factors = [name for name, ok in checks if ok]
        return len(factors), factors

    def _portfolio_block(self, cand: Candidate, risk_pct_used: float,
                         p: PortfolioContext) -> str | None:
        if cand.symbol in p.open_symbols:
            return "already_open_no_pyramiding"
        if p.open_positions >= self.risk.max_open_positions:
            return f"max_open_positions_{self.risk.max_open_positions}"
        if p.open_risk_pct + risk_pct_used > self.risk.max_portfolio_risk_pct + 1e-9:
            return (f"portfolio_risk_budget_{_r(p.open_risk_pct)}+"
                    f"{_r(risk_pct_used)}>{self.risk.max_portfolio_risk_pct}")
        sector = cand.instrument.sector
        if sector and p.sector_counts.get(sector, 0) >= self.risk.max_correlated_positions:
            return f"sector_concentration_{sector}"
        return None

    @staticmethod
    def _trend_state(f: dict) -> str:
        if f.get("uptrend_stack"):
            return "uptrend"
        if f.get("downtrend_stack"):
            return "downtrend"
        return "range/transition"

    @staticmethod
    def _regime(f: dict) -> str:
        adx = f.get("adx14")
        atr_pct = f.get("atr_pct")
        trend = "trending" if (adx or 0) >= 25 else "developing" if (adx or 0) >= 20 else "choppy"
        volp = "high-vol" if (atr_pct or 0) > HIGH_VOL_ATR_PCT else "normal-vol"
        return f"{trend}/{volp}"

    @staticmethod
    def _grade(n_conf: int, net_rr: float | None, adx: float | None) -> str:
        rr = net_rr or 0.0
        a = adx or 0.0
        if n_conf >= 6 and rr >= 3.0 and a >= 25:
            return "A"
        if n_conf >= 5 and rr >= 2.5:
            return "B"
        if n_conf >= 4 and rr >= 2.0:
            return "C"
        if n_conf >= 2:
            return "D"
        return "F"

    def _confidence(self, cand: Candidate, n_conf: int, downgrade: bool = False) -> int:
        base = 30 + n_conf * 9 + max(abs(cand.score) - 30, 0) * 0.4
        if downgrade:
            base *= 0.6
        return int(max(0, min(95, round(base))))
