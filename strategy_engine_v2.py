"""Strategy Architecture V2 — shadow-only candidate evaluator.

This module is deliberately side-effect free. It never places orders, reads
credentials, writes files, or changes the legacy strategy decision.
Only closed-candle rows supplied by the caller are evaluated.
"""
from __future__ import annotations

import math


ENGINE_VERSION = "2.0-shadow"
MIN_ROWS = 60
MIN_RR = 1.25


def _number(row, key, default=None):
    try:
        value = float(row.get(key))
        return value if math.isfinite(value) else default
    except (AttributeError, TypeError, ValueError):
        return default


def _rows(df, start, end):
    try:
        return [df.iloc[index] for index in range(start, end)]
    except Exception:
        return []


def _extreme(rows, key, operation):
    values = [_number(row, key) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return max(values) if operation == "max" else min(values)


def _candidate(strategy_id, symbol, side, entry, stop, target, score,
               regime, candle_time, reasons, extra=None):
    entry = float(entry)
    stop = float(stop)
    target = float(target)
    if side == "LONG":
        direction_valid = stop < entry < target
    else:
        direction_valid = target < entry < stop
    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr = reward / risk if risk > 0 else 0.0
    risk_valid = bool(direction_valid and rr >= MIN_RR)
    result = {
        "strategy_id": strategy_id,
        "strategy_version": ENGINE_VERSION,
        "symbol": str(symbol),
        "side": side,
        "timeframe": None,
        "source_candle_time": str(candle_time) if candle_time is not None else None,
        "data_freshness": "CLOSED_CANDLE_ONLY",
        "entry_reference": round(entry, 10),
        "invalidation_price": round(stop, 10),
        "target_price": round(target, 10),
        "setup_score": round(max(0.0, min(100.0, float(score))), 2),
        "regime": regime,
        "risk_reward_gross": round(rr, 3),
        "risk_valid": risk_valid,
        "reason_codes": list(reasons),
        "rejection_reasons": [] if risk_valid else (
            ["INVALID_PRICE_DIRECTION"] if not direction_valid else ["RR_BELOW_1_25"]
        ),
    }
    if extra:
        result.update(extra)
    return result


def evaluate_strategy_candidates(df, symbol, timeframe="15m", source_candle_time=None):
    """Return independent V2 shadow candidates; never return an execution order."""
    empty = {
        "engine_version": ENGINE_VERSION,
        "status": "INSUFFICIENT_DATA",
        "symbol": str(symbol),
        "timeframe": str(timeframe),
        "source_candle_time": str(source_candle_time) if source_candle_time is not None else None,
        "regime": "UNKNOWN",
        "candidates": [],
        "ranked_candidates": [],
        "top_candidate": None,
        "execution_allowed": False,
    }
    try:
        if df is None or len(df) < MIN_ROWS:
            return empty
        required = ("open", "high", "low", "close", "volume", "ema20", "ema50",
                    "rsi", "atr", "obv", "obv_ma5")
        last = df.iloc[-1]
        values = {key: _number(last, key) for key in required}
        if any(value is None for value in values.values()):
            empty["status"] = "INVALID_DATA"
            return empty

        close = values["close"]
        open_price = values["open"]
        high = values["high"]
        low = values["low"]
        ema20 = values["ema20"]
        ema50 = values["ema50"]
        rsi = values["rsi"]
        atr = values["atr"]
        obv = values["obv"]
        obv_ma5 = values["obv_ma5"]
        if close <= 0 or atr <= 0 or ema20 <= 0 or ema50 <= 0:
            empty["status"] = "INVALID_DATA"
            return empty

        previous = _rows(df, max(0, len(df) - 31), len(df) - 1)
        recent = _rows(df, max(0, len(df) - 5), len(df) - 1)
        if len(previous) < 20:
            return empty

        prior_high = _extreme(previous[-20:], "high", "max")
        prior_low = _extreme(previous[-30:], "low", "min")
        ema20_five_bars_ago = _number(df.iloc[-6], "ema20")
        ema20_slope_pct = (
            (ema20 / ema20_five_bars_ago - 1.0)
            if ema20_five_bars_ago and ema20_five_bars_ago > 0 else 0.0
        )
        trend_up = ema20 > ema50 and ema20_slope_pct > 0
        trend_down = ema20 < ema50 and ema20_slope_pct < 0
        if trend_up:
            regime = "TREND_UP"
        elif trend_down:
            regime = "TREND_DOWN"
        elif abs(close / ema20 - 1.0) <= 0.005:
            regime = "RANGE"
        else:
            regime = "MIXED"

        volume_values = [_number(row, "volume") for row in _rows(df, len(df) - 21, len(df) - 1)]
        volume_values = [value for value in volume_values if value is not None and value >= 0]
        average_volume = sum(volume_values) / len(volume_values) if volume_values else 0.0
        volume_ratio = values["volume"] / average_volume if average_volume > 0 else 0.0
        candle_range = max(0.0, high - low)
        body_ratio = abs(close - open_price) / candle_range if candle_range > 0 else 0.0
        green = close > open_price
        obv_rising = obv > obv_ma5
        candidates = []
        candle_time = source_candle_time

        # Family 1: trend breakout + immediate retest/reclaim.
        if prior_high and trend_up and close > prior_high and close > ema20 and green and obv_rising and 48 <= rsi <= 75:
            retest_ok = low <= prior_high + 0.25 * atr
            if retest_ok:
                stop = min(low, prior_high - 0.50 * atr)
                target = close + 2.0 * atr
                score = 45 + 15 + min(volume_ratio, 2.0) * 7 + min(body_ratio, 1.0) * 8
                score += max(0.0, 10.0 - abs(rsi - 60.0) * 0.5)
                candidates.append(_candidate(
                    "TREND_BREAKOUT_RETEST", symbol, "LONG", close, stop, target,
                    score, regime, candle_time,
                    ["HTF_TREND_UP", "CLOSE_ABOVE_PRIOR_20_HIGH", "RETEST_RECLAIM",
                     "OBV_CONFIRMATION", "CLOSED_CANDLE"],
                    {"breakout_level": round(prior_high, 10), "volume_ratio": round(volume_ratio, 3),
                     "body_ratio": round(body_ratio, 3)}
                ))

        # Family 2: EMA pullback in an established trend.
        ema_touch = any(
            (_number(row, "low") is not None and _number(row, "high") is not None
             and _number(row, "low") <= ema20 + 0.35 * atr
             and _number(row, "high") >= ema20 - 0.35 * atr)
            for row in recent
        )
        if trend_up and ema_touch and close > ema20 and green and close > _number(df.iloc[-2], "close", close) and obv_rising and 45 <= rsi <= 72:
            swing_low = _extreme(recent, "low", "min")
            stop = min(swing_low if swing_low is not None else low, ema20) - 0.20 * atr
            target = prior_high if prior_high and prior_high > close else close + 2.0 * atr
            score = 48 + 14 + min(volume_ratio, 2.0) * 5 + min(body_ratio, 1.0) * 7
            score += max(0.0, 10.0 - abs(rsi - 58.0) * 0.4)
            candidates.append(_candidate(
                "EMA_PULLBACK", symbol, "LONG", close, stop, target, score,
                regime, candle_time,
                ["HTF_TREND_UP", "EMA20_PULLBACK_TOUCHED", "CLOSE_RECLAIM_EMA20",
                 "OBV_CONFIRMATION", "CLOSED_CANDLE"],
                {"ema20": round(ema20, 10), "volume_ratio": round(volume_ratio, 3)}
            ))

        # Family 3: liquidity sweep + reclaim, evaluated symmetrically.
        # Levels use only the 30 completed candles preceding the signal candle.
        if prior_low is not None and low < prior_low - 0.05 * atr and close > prior_low:
            stop = low - 0.15 * atr
            target = prior_high if prior_high and prior_high > close else close + 2.0 * atr
            score = 52 + min(volume_ratio, 2.0) * 6 + min(body_ratio, 1.0) * 8
            score += 8 if green else 0
            candidates.append(_candidate(
                "LIQUIDITY_SWEEP_RECLAIM", symbol, "LONG", close, stop, target,
                score, regime, candle_time,
                ["PRIOR_30_LOW_SWEPT", "CLOSE_RECLAIMED_PRIOR_LOW", "CLOSED_CANDLE"],
                {"swept_level": round(prior_low, 10), "sweep_extreme": round(low, 10)}
            ))

        if prior_high is not None and high > prior_high + 0.05 * atr and close < prior_high:
            stop = high + 0.15 * atr
            target = prior_low if prior_low and prior_low < close else close - 2.0 * atr
            score = 52 + min(volume_ratio, 2.0) * 6 + min(body_ratio, 1.0) * 8
            score += 8 if close < open_price else 0
            candidates.append(_candidate(
                "LIQUIDITY_SWEEP_RECLAIM", symbol, "SHORT", close, stop, target,
                score, regime, candle_time,
                ["PRIOR_30_HIGH_SWEPT", "CLOSE_RECLAIMED_PRIOR_HIGH", "CLOSED_CANDLE"],
                {"swept_level": round(prior_high, 10), "sweep_extreme": round(high, 10)}
            ))

        for candidate in candidates:
            candidate["timeframe"] = str(timeframe)
        ranked = sorted(candidates, key=lambda item: (item["risk_valid"], item["setup_score"]), reverse=True)
        return {
            "engine_version": ENGINE_VERSION,
            "status": "OK",
            "symbol": str(symbol),
            "timeframe": str(timeframe),
            "source_candle_time": str(candle_time) if candle_time is not None else None,
            "regime": regime,
            "candidates": candidates,
            "ranked_candidates": ranked,
            "top_candidate": ranked[0] if ranked else None,
            "execution_allowed": False,
            "notes": ["SHADOW_ONLY", "NO_ORDERS", "GROSS_RR_ONLY_COSTS_NOT_MODELED"],
        }
    except Exception as exc:
        result = dict(empty)
        result["status"] = "ERROR"
        result["error"] = str(exc)[:180]
        return result
