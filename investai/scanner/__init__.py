"""Deterministic scanner: features -> numeric score -> ranked candidates."""
from .scanner import Scanner, score_features

__all__ = ["Scanner", "score_features"]
