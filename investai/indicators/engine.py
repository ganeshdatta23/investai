"""Technical indicators, implemented from definitions in pure pandas/numpy.

No TA-Lib / pandas-ta dependency (avoids native-build and version-pin pain).
Wilder-smoothed indicators (RSI, ATR, ADX) use an EWM with alpha=1/period and
adjust=False, which is the standard RMA equivalent of Wilder's smoothing.

Every function returns NaN where there is insufficient history rather than a
guessed value — the engine never fabricates numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV = ("open", "high", "low", "close", "volume")


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, min_periods=span, adjust=False).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss == 0 over the window, RSI is defined as 100.
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, min_periods=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "macd_signal": signal_line, "macd_hist": hist})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    atr_ = _wilder(true_range(df), period)
    plus_di = 100.0 * _wilder(plus_dm, period) / atr_
    minus_di = 100.0 * _wilder(minus_dm, period) / atr_
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_ = _wilder(dx, period)
    return pd.DataFrame({"adx": adx_, "plus_di": plus_di, "minus_di": minus_di})


def rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (typical * df["volume"]).rolling(window).sum()
    vol = df["volume"].rolling(window).sum()
    return pv / vol.replace(0.0, np.nan)


def volume_spike(volume: pd.Series, window: int = 20) -> pd.Series:
    avg = volume.rolling(window).mean()
    return volume / avg.replace(0.0, np.nan)


def _last(series: pd.Series) -> float | None:
    if series is None or series.empty:
        return None
    v = series.iloc[-1]
    if pd.isna(v):
        return None
    return float(v)


class IndicatorEngine:
    """Computes a feature snapshot for the most recent bar of an OHLCV frame."""

    def __init__(self, rsi_period: int = 14, atr_period: int = 14, adx_period: int = 14):
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.adx_period = adx_period

    def features(self, df: pd.DataFrame) -> dict[str, float | bool | None]:
        return compute_features(df, self.rsi_period, self.atr_period, self.adx_period)


def compute_features(
    df: pd.DataFrame, rsi_period: int = 14, atr_period: int = 14, adx_period: int = 14
) -> dict[str, float | bool | None]:
    """Return a flat dict of the latest indicator values + market-structure flags.

    Requires columns open/high/low/close/volume. Insufficient history yields None
    for the affected fields (never a fabricated value).
    """
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")

    df = df.dropna(subset=["close"]).copy()
    n = len(df)
    if n == 0:
        return {"bars": 0}

    close = df["close"]
    price = float(close.iloc[-1])

    ema20 = _last(ema(close, 20))
    ema50 = _last(ema(close, 50))
    ema200 = _last(ema(close, 200))
    rsi14 = _last(rsi(close, rsi_period))
    macd_df = macd(close)
    macd_v = _last(macd_df["macd"])
    macd_sig = _last(macd_df["macd_signal"])
    macd_hist = _last(macd_df["macd_hist"])
    atr14 = _last(atr(df, atr_period))
    adx_df = adx(df, adx_period)
    adx14 = _last(adx_df["adx"])
    plus_di = _last(adx_df["plus_di"])
    minus_di = _last(adx_df["minus_di"])
    vwap20 = _last(rolling_vwap(df, 20))
    vspike = _last(volume_spike(df["volume"], 20))
    avg_vol20 = _last(df["volume"].rolling(20).mean())

    high_20 = float(df["high"].iloc[-20:].max()) if n >= 20 else None
    low_20 = float(df["low"].iloc[-20:].min()) if n >= 20 else None
    high_55 = float(df["high"].iloc[-55:].max()) if n >= 55 else None

    def pct(a: float | None, b: float | None) -> float | None:
        if a is None or b is None or b == 0:
            return None
        return (a - b) / b * 100.0

    # Trend alignment / structure
    uptrend_stack = (
        ema20 is not None and ema50 is not None and ema200 is not None
        and ema20 > ema50 > ema200 and price > ema20
    )
    downtrend_stack = (
        ema20 is not None and ema50 is not None and ema200 is not None
        and ema20 < ema50 < ema200 and price < ema20
    )
    ret_20 = pct(price, float(close.iloc[-21])) if n >= 21 else None
    ret_50 = pct(price, float(close.iloc[-51])) if n >= 51 else None

    return {
        "bars": n,
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi14": rsi14,
        "macd": macd_v,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "atr14": atr14,
        "atr_pct": pct(atr14, price) if atr14 is not None else None,
        "adx14": adx14,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "vwap20": vwap20,
        "vol_spike": vspike,
        "avg_vol20": avg_vol20,
        "high_20": high_20,
        "low_20": low_20,
        "high_55": high_55,
        "dist_ema20_pct": pct(price, ema20),
        "dist_vwap_pct": pct(price, vwap20),
        "ret_20": ret_20,
        "ret_50": ret_50,
        "uptrend_stack": bool(uptrend_stack),
        "downtrend_stack": bool(downtrend_stack),
        "above_vwap": (vwap20 is not None and price > vwap20),
        "near_20d_high": (high_20 is not None and price >= 0.99 * high_20),
        "near_20d_low": (low_20 is not None and price <= 1.01 * low_20),
    }
