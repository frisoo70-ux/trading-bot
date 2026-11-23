"""Isolated Elder-inspired 3-candle PAPER signal prototype.

This is an original, explicit 3-candle ruleset inspired by trend/pullback/
confirmation concepts, NOT a claim to reproduce Alexander Elder's exact method.
It is side-effect free and emits signal candidates only; no exchange orders.
"""
from __future__ import annotations

import math

ENGINE_VERSION = "elder3-paper-prototype-1.0"
MIN_RR = 1.5


def _n(row, key):
    try:
        x = float(row[key])
        return x if math.isfinite(x) else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def evaluate_three_candle_setup(df, symbol, timeframe="15m", source_candle_time=None):
    """Evaluate the last three CLOSED rows; rows may contain EMA20/EMA50/ATR.

    Candle 1: bullish impulse and close above EMA20 (if EMA fields exist).
    Candle 2: bearish/neutral pullback, remains above EMA50 (if available).
    Candle 3: bullish confirmation closing above candle 2 high.
    Stop: below the lowest low of the three candles minus 0.15 ATR.
    Target: 1.5R minimum reward, fixed at 2R.
    """
    base = {
        "engine_version": ENGINE_VERSION, "symbol": str(symbol),
        "timeframe": str(timeframe), "status": "NO_SETUP",
        "candidate": None, "execution_allowed": False,
        "source_candle_time": str(source_candle_time) if source_candle_time is not None else None,
    }
    try:
        if df is None or len(df) < 3:
            base["status"] = "INSUFFICIENT_DATA"
            return base
        rows = [df.iloc[-3], df.iloc[-2], df.iloc[-1]]
        keys = ("open", "high", "low", "close")
        candles = [{k: _n(row, k) for k in keys} for row in rows]
        if any(any(c[k] is None for k in keys) for c in candles):
            base["status"] = "INVALID_DATA"
            return base
        c1, c2, c3 = candles
        atr = _n(rows[-1], "atr")
        if atr is None or atr <= 0:
            base["status"] = "INVALID_DATA"
            return base
        ema20 = _n(rows[-1], "ema20")
        ema50 = _n(rows[-1], "ema50")
        # Candle 1 bullish impulse with body >= 45% of range.
        r1 = c1["high"] - c1["low"]
        impulse = c1["close"] > c1["open"] and r1 > 0 and (c1["close"]-c1["open"])/r1 >= 0.45
        if ema20 is not None:
            impulse = impulse and c1["close"] > ema20
        # Candle 2 is a pullback, not a bearish close breaking structure.
        pullback = c2["close"] <= c2["open"] and c2["low"] > c1["low"]
        if ema50 is not None:
            pullback = pullback and c2["low"] >= ema50
        # Candle 3 is the trigger: bullish close through candle 2 high.
        trigger = c3["close"] > c3["open"] and c3["close"] > c2["high"]
        if ema20 is not None:
            trigger = trigger and c3["close"] > ema20
        if not (impulse and pullback and trigger):
            base["status"] = "NO_SETUP"
            base["rejection_reasons"] = [
                reason for ok, reason in ((impulse, "C1_IMPULSE_FAIL"),
                                         (pullback, "C2_PULLBACK_FAIL"),
                                         (trigger, "C3_TRIGGER_FAIL")) if not ok
            ]
            return base
        entry = c3["close"]
        stop = min(c["low"] for c in candles) - 0.15 * atr
        risk = entry - stop
        if risk <= 0:
            base["status"] = "INVALID_RISK"
            return base
        target = entry + 2.0 * risk
        rr = (target-entry)/risk
        if rr < MIN_RR:
            base["status"] = "RR_REJECTED"
            return base
        candle_time = source_candle_time
        if candle_time is None:
            try: candle_time = rows[-1]["time"]
            except Exception: pass
        base.update({
            "status": "SIGNAL",
            "candidate": {
                "strategy_id": "ELDER_3_CANDLE_PAPER",
                "side": "LONG", "entry_reference": round(entry, 10),
                "invalidation_price": round(stop, 10), "target_price": round(target, 10),
                "risk_reward": round(rr, 3), "source_candle_time": str(candle_time) if candle_time is not None else None,
                "reason_codes": ["C1_BULLISH_IMPULSE", "C2_PULLBACK", "C3_CLOSE_ABOVE_C2_HIGH", "CLOSED_CANDLES_ONLY"],
            },
        })
        return base
    except Exception as exc:
        base["status"] = "ERROR"
        base["error"] = str(exc)[:160]
        return base
