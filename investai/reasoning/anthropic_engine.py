"""EXPERIMENTAL — UNVERIFIED. Claude-as-reasoner, behind the ReasoningEngine
interface, so it can replace RuleEngine without touching the pipeline.

This path is NOT exercised by the test suite (it needs a network call + an API
key). Keep reasoning.engine = "rule" until you have validated this against the
rule engine on a sample of candidates. The rule engine remains the safety net:
build_reasoner() falls back to it if this import or the API call fails.

The contract: Claude judges only the small pre-ranked candidate set (never the
full universe), and its numeric plan is still clamped by the same risk policy.
"""
from __future__ import annotations

import json
import os

from ..schemas import Candidate, Decision
from .base import PortfolioContext, ReasoningEngine
from .rule_engine import RuleEngine

_SYSTEM = (
    "You are a disciplined trade-decision engine. Capital preservation first. "
    "Apply the given risk policy strictly. Never fabricate data. Prefer HOLD over "
    "a weak BUY/SELL. Return ONLY a JSON object matching the provided schema."
)


class AnthropicEngine(ReasoningEngine):
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = str(cfg.get("reasoning", "anthropic", "model", default="claude-opus-4-8"))
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        # Imported lazily so the dependency is optional.
        import anthropic  # type: ignore
        self.client = anthropic.Anthropic(api_key=api_key)
        # Rule engine provides the deterministic plan (entry/stop/targets/size) and
        # acts as a fallback; Claude is asked to confirm/justify or downgrade it.
        self._rules = RuleEngine(cfg)

    def decide(self, candidate: Candidate, portfolio: PortfolioContext) -> Decision:
        baseline = self._rules.decide(candidate, portfolio)
        prompt = self._build_prompt(candidate, portfolio, baseline)
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            data = json.loads(_extract_json(text))
            merged = baseline.to_dict()
            merged.update({k: v for k, v in data.items() if k in merged})
            return Decision(**merged)
        except Exception as e:  # noqa: BLE001 - never let the LLM break the loop
            baseline.red_flags.append(f"anthropic_fallback:{type(e).__name__}")
            return baseline

    def _build_prompt(self, c: Candidate, p: PortfolioContext, baseline: Decision) -> str:
        return (
            f"Risk policy: {self.cfg.risk}\n"
            f"Portfolio: open={p.open_positions} open_risk_pct={p.open_risk_pct} "
            f"daily_loss_pct={p.daily_realized_loss_pct}\n"
            f"Symbol {c.symbol} features: {json.dumps(c.features, default=str)}\n"
            f"Deterministic baseline plan: {json.dumps(baseline.to_dict(), default=str)}\n"
            "Confirm, downgrade, or reject. Return the decision JSON only."
        )


def _extract_json(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return text[start:end + 1]
