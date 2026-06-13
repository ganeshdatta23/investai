"""The scanner pre-filters the universe with deterministic arithmetic so the
reasoning layer only ever sees a small, ranked candidate set.

The score is intentionally transparent (a weighted sum of confirmations), signed
by direction: positive => long setup, negative => short setup. Candidates are
ranked by |score|. This is NOT a prediction — it is a triage heuristic.
"""
from __future__ import annotations

from ..data.base import DataAdapter
from ..indicators.engine import compute_features
from ..schemas import Candidate, Instrument


def score_features(f: dict) -> tuple[float, str, list[str]]:
    """Return (signed_score, direction, reasons). Score roughly in [-100, 100]."""
    if not f or f.get("bars", 0) < 60:
        return 0.0, "none", ["insufficient_history"]

    rsi = f.get("rsi14")
    adx = f.get("adx14")
    macd_hist = f.get("macd_hist")
    vspike = f.get("vol_spike") or 0.0
    reasons: list[str] = []

    long_score = 0.0
    short_score = 0.0

    # Trend alignment (EMA stack) — the heaviest weight.
    if f.get("uptrend_stack"):
        long_score += 30; reasons.append("ema_stack_up")
    if f.get("downtrend_stack"):
        short_score += 30; reasons.append("ema_stack_down")

    # ADX trend strength gates momentum contribution.
    if adx is not None:
        if adx >= 25:
            long_score += 12; short_score += 12; reasons.append(f"adx_strong_{adx:.0f}")
        elif adx >= 20:
            long_score += 6; short_score += 6; reasons.append(f"adx_ok_{adx:.0f}")
        else:
            reasons.append(f"adx_weak_{adx:.0f}")

    # Directional index bias.
    pdi, mdi = f.get("plus_di"), f.get("minus_di")
    if pdi is not None and mdi is not None:
        if pdi > mdi:
            long_score += 8
        else:
            short_score += 8

    # MACD histogram.
    if macd_hist is not None:
        if macd_hist > 0:
            long_score += 10; reasons.append("macd_pos")
        else:
            short_score += 10; reasons.append("macd_neg")

    # RSI momentum (reward trend-confirming RSI, penalise extremes).
    if rsi is not None:
        if 50 <= rsi <= 68:
            long_score += 10; reasons.append(f"rsi_bull_{rsi:.0f}")
        elif rsi > 75:
            long_score -= 8; reasons.append(f"rsi_overbought_{rsi:.0f}")
        if 32 <= rsi <= 50:
            short_score += 10; reasons.append(f"rsi_bear_{rsi:.0f}")
        elif rsi < 25:
            short_score -= 8; reasons.append(f"rsi_oversold_{rsi:.0f}")

    # VWAP relation.
    if f.get("above_vwap"):
        long_score += 6
    else:
        short_score += 6

    # Volume confirmation.
    if vspike >= 1.5:
        long_score += 8; short_score += 8; reasons.append(f"vol_spike_{vspike:.1f}x")

    # Breakout proximity.
    if f.get("near_20d_high"):
        long_score += 8; reasons.append("near_20d_high")
    if f.get("near_20d_low"):
        short_score += 8; reasons.append("near_20d_low")

    # Over-extension penalty (mean-distance risk).
    dist = f.get("dist_ema20_pct")
    if dist is not None:
        if dist > 10:
            long_score -= 10; reasons.append(f"extended_above_{dist:.0f}pct")
        elif dist < -10:
            short_score -= 10; reasons.append(f"extended_below_{dist:.0f}pct")

    if long_score >= short_score:
        return round(long_score, 1), "long", reasons
    return round(-short_score, 1), "short", reasons


class Scanner:
    def __init__(self, adapter: DataAdapter, cfg):
        self.adapter = adapter
        self.cfg = cfg

    def scan(self, seed: list[str] | None) -> list[Candidate]:
        interval = self.cfg.get("data", "candle_interval", default="day")
        days = int(self.cfg.get("data", "history_days", default=200))
        top_n = int(self.cfg.get("scanner", "top_n", default=20))
        min_vol = float(self.cfg.get("scanner", "min_avg_volume", default=100000))
        min_price = float(self.cfg.get("scanner", "min_price", default=20))
        max_price = float(self.cfg.get("scanner", "max_price", default=1e6))

        instruments = self.adapter.resolve_universe(seed)
        candidates: list[Candidate] = []
        for inst in instruments:
            df = self.adapter.fetch_history(inst, interval, days)
            if df.empty or len(df) < 60:
                continue
            f = compute_features(df)
            price = f.get("price")
            avg_vol = f.get("avg_vol20") or 0.0
            # Liquidity + price filters (execution reality).
            if price is None or not (min_price <= price <= max_price):
                continue
            if avg_vol < min_vol:
                continue
            score, direction, reasons = score_features(f)
            f["direction"] = direction
            f["score_reasons"] = reasons
            candidates.append(Candidate(instrument=inst, last_price=price, features=f, score=score))

        candidates.sort(key=lambda c: abs(c.score), reverse=True)
        return candidates[:top_n]
