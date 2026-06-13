"""Reasoning layer: turns a ranked Candidate into a risk-checked Decision.

`rule` is a deterministic, fully-testable engine. An `anthropic` engine can be
dropped in behind the same ReasoningEngine interface without touching the
pipeline.
"""
from .base import PortfolioContext, ReasoningEngine
from .rule_engine import RuleEngine

__all__ = ["PortfolioContext", "ReasoningEngine", "RuleEngine"]
