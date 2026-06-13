"""Diagnostics helper: write to stderr so stdout stays pure JSON for piping."""
from __future__ import annotations

import sys


def log(*args, **kwargs) -> None:
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)
