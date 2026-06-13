"""Configuration loading and environment handling."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .schemas import ExecutionCosts, RiskPolicy


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency). Existing env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path

    # ---- convenience typed accessors -------------------------------------
    @property
    def mode(self) -> str:
        return str(self.raw.get("mode", "PAPER")).upper()

    @property
    def live_mode(self) -> bool:
        return bool(self.raw.get("live_mode", False))

    @property
    def equity(self) -> float:
        return float(self.raw["account"]["equity"])

    @property
    def risk(self) -> RiskPolicy:
        r = self.raw.get("risk", {})
        return RiskPolicy(
            max_risk_per_trade_pct=float(r.get("max_risk_per_trade_pct", 1.0)),
            max_daily_loss_pct=float(r.get("max_daily_loss_pct", 3.0)),
            max_portfolio_risk_pct=float(r.get("max_portfolio_risk_pct", 5.0)),
            min_reward_risk=float(r.get("min_reward_risk", 2.0)),
            max_open_positions=int(r.get("max_open_positions", 8)),
            max_correlated_positions=int(r.get("max_correlated_positions", 3)),
        )

    @property
    def costs(self) -> ExecutionCosts:
        e = self.raw.get("execution", {})
        return ExecutionCosts(
            fee_pct=float(e.get("fee_pct", 0.03)),
            slippage_pct=float(e.get("slippage_pct", 0.05)),
        )

    def path(self, key: str) -> Path:
        """Resolve a path from the `paths:` block relative to the project root."""
        rel = self.raw.get("paths", {}).get(key)
        if rel is None:
            raise KeyError(f"paths.{key} not configured")
        p = self.root / rel
        return p

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    root = Path(path).parent if path else Path(__file__).resolve().parent.parent
    cfg_path = Path(path) if path else root / "config.yaml"
    _load_dotenv(root / ".env")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg = Config(raw=data, root=root)
    # Ensure runtime dirs exist.
    for key in ("db", "instruments_cache", "token_store"):
        cfg.path(key).parent.mkdir(parents=True, exist_ok=True)
    cfg.path("log_dir").mkdir(parents=True, exist_ok=True)
    return cfg
