"""InvestAI — disciplined paper-trading / research engine.

Architecture (Claude is the reasoning layer, NOT the data source):

    UpstoxAdapter (data) -> IndicatorEngine -> Scanner/ranker
        -> ReasoningEngine (rule | anthropic) -> PaperLedger -> Tracker

Default mode is PAPER. Live trading is refused unless explicitly enabled
and the validation gates pass.
"""

__version__ = "0.1.0"
