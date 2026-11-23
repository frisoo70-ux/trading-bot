"""Crypto adaptation of Alexander Elder's Triple Screen, PAPER only.

Screens:
1D: rising MACD histogram (12, 26, 9) = long-only dominant trend.
1H: 2-period EMA of Force Index is below zero and rising = pullback losing force.
1m: trailing buy-stop above the previous CLOSED minute candle high.
All frames must contain CLOSED OHLCV candles. No exchange orders are sent.
"""
from __future__ import annotations

import math
from strategy_engine_v2_paper import V2PaperEngine, _num, _ms

ENGINE_VERSION = "elder-triple-screen-crypto-1.0"
ENTRY_BUFFER_BPS = 1.0
STOP_ATR_BUFFER = 0.10
MAX_STOP_DISTANCE_PCT = 5.0


def _ema(values, span):
    return values.ewm(span=span, adjust=False).mean()


def _valid_ohlcv(df, minimum):
    if df is None or getattr(df, "empty", True) or len(df) < minimum:
        return False
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        return False
    try:
        return all(math.isfinite(float(df.iloc[-i][k])) for i in range(1, minimum + 1)
                   for k in ("open", "high", "low", "close", "volume"))
    except (TypeError, ValueError, KeyError, IndexError):
        return False


def daily_macd_screen(daily_df):
    """Bullish when the last CLOSED daily MACD histogram bar is rising."""
    if not _valid_ohlcv(daily_df, 35):
        return {"ready": False, "bullish": False, "reason": "DAILY_DATA_INSUFFICIENT"}
    close = daily_df["close"].astype(float)
    macd = _ema(close, 12) - _ema(close, 26)
    histogram = macd - _ema(macd, 9)
    current, previous = float(histogram.iloc[-1]), float(histogram.iloc[-2])
    return {
        "ready": True, "bullish": current > previous,
        "histogram": current, "previous_histogram": previous,
        "reason": "DAILY_MACD_HISTOGRAM_RISING" if current > previous else "DAILY_MACD_HISTOGRAM_NOT_RISING",
        "candle_time": _ms(daily_df.iloc[-1]["time"]),
    }


def hourly_force_index_screen(hourly_df):
    """Bullish pullback setup: Force Index EMA(2) below zero but rising."""
    if not _valid_ohlcv(hourly_df, 4):
        return {"ready": False, "pullback": False, "reason": "HOURLY_DATA_INSUFFICIENT"}
    close = hourly_df["close"].astype(float)
    volume = hourly_df["volume"].astype(float)
    force = close.diff() * volume
    fi2 = _ema(force.fillna(0.0), 2)
    current, previous = float(fi2.iloc[-1]), float(fi2.iloc[-2])
    pullback = current < 0 and current > previous
    tr = hourly_df["high"].astype(float) - hourly_df["low"].astype(float)
    prev_close = close.shift(1)
    tr = __import__("pandas").concat(
        [tr, (hourly_df["high"].astype(float) - prev_close).abs(),
         (hourly_df["low"].astype(float) - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float("nan")
    lows = hourly_df["low"].astype(float).tail(3)
    stop = float(lows.min()) - (STOP_ATR_BUFFER * atr if math.isfinite(atr) and atr > 0 else 0.0)
    return {
        "ready": True, "pullback": pullback, "force_index_ema2": current,
        "previous_force_index_ema2": previous,
        "reason": "FORCE_INDEX_PULLBACK_TURNING_UP" if pullback else "FORCE_INDEX_PULLBACK_NOT_READY",
        "candle_time": _ms(hourly_df.iloc[-1]["time"]),
        "pullback_low": float(lows.min()), "atr": atr if math.isfinite(atr) else None,
        "invalidation_price": stop,
    }


def evaluate_triple_screen(daily_df, hourly_df, minute_df, symbol):
    daily = daily_macd_screen(daily_df)
    hourly = hourly_force_index_screen(hourly_df)
    minute_ready = _valid_ohlcv(minute_df, 2)
    result = {
        "engine_version": ENGINE_VERSION, "symbol": str(symbol),
        "status": "NO_SETUP", "daily": daily, "hourly": hourly,
        "minute_ready": minute_ready, "candidate": None,
    }
    if not daily["ready"] or not hourly["ready"] or not minute_ready:
        result["status"] = "INSUFFICIENT_DATA"
        return result
    if not daily["bullish"]:
        result["status"] = "DAILY_TREND_BLOCK"
        return result
    if not hourly["pullback"]:
        result["status"] = "HOURLY_PULLBACK_NOT_READY"
        return result
    last = minute_df.iloc[-1]
    high = float(last["high"])
    trigger = high * (1.0 + ENTRY_BUFFER_BPS / 10000.0)
    stop = float(hourly["invalidation_price"])
    if not (0 < stop < trigger):
        result["status"] = "INVALID_STOP"
        return result
    distance_pct = (trigger - stop) / trigger * 100.0
    if distance_pct > MAX_STOP_DISTANCE_PCT:
        result["status"] = "STOP_TOO_WIDE"
        return result
    result.update({
        "status": "SETUP_READY",
        "candidate": {
            "strategy_id": "ELDER_TRIPLE_SCREEN_LONG",
            "side": "LONG",
            "entry_trigger": trigger,
            "invalidation_price": stop,
            "risk_pct": distance_pct,
            "minute_candle_time": _ms(last["time"]),
            "daily_candle_time": daily["candle_time"],
            "hourly_candle_time": hourly["candle_time"],
            "reason_codes": [
                "SCREEN1_DAILY_MACD_HISTOGRAM_RISING",
                "SCREEN2_HOURLY_FORCE_INDEX_EMA2_NEGATIVE_RISING",
                "SCREEN3_ONE_MINUTE_PREVIOUS_HIGH_BUY_STOP",
                "CLOSED_CANDLES_ONLY",
            ],
        },
    })
    return result


class ElderTripleScreenPaperEngine(V2PaperEngine):
    """Independent virtual portfolio; reuses V2 accounting, never order submission."""

    def load(self):
        state = super().load()
        if not isinstance(state.get("pending_entries"), dict):
            state["pending_entries"] = {}
        return state

    def _open_elder(self, candidate, candle_time, execution_fn):
        state = self.load()
        symbol = str(candidate["symbol"])
        if any(p.get("symbol") == symbol for p in state["positions"]):
            return False
        if len(state["positions"]) >= self.max_positions:
            return False
        exposure = sum(_num(p.get("notional")) for p in state["positions"])
        if exposure + self.trade_size > self.max_gross_exposure + 1e-9:
            return False
        cash = _num(state.get("initial_balance")) + _num(state.get("realized_pnl"))
        margin = sum(_num(p.get("margin", p.get("notional"))) for p in state["positions"])
        if cash - margin < self.trade_size:
            return False
        reference = _num(candidate.get("entry_reference"))
        stop = _num(candidate.get("invalidation_price"))
        if reference <= 0 or not 0 < stop < reference:
            return False
        execution = self._estimate(execution_fn, symbol, "BUY", self.trade_size, reference)
        fill = execution["fill"]
        if not stop < fill:
            return False
        qty = self.trade_size / fill
        fee = abs(qty * fill) * execution["fee"]
        position = {
            "position_id": f"ELDER-{symbol}-{candle_time}",
            "symbol": symbol, "strategy_id": "ELDER_TRIPLE_SCREEN_LONG",
            "strategy_version": ENGINE_VERSION, "side": "LONG",
            "entry_time": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entry_candle_time": _ms(candle_time),
            "last_processed_candle_time": _ms(candle_time),
            "entry_price": round(fill, 10), "entry_reference_price": reference,
            "last_price": reference, "qty": qty, "remaining_qty": qty,
            "notional": self.trade_size, "margin": self.trade_size / max(self.leverage, 1.0),
            "leverage": self.leverage, "entry_fee": fee,
            "entry_fee_rate": execution["fee"], "entry_fee_source": execution["fee_source"],
            "entry_slippage_rate": execution["slippage"], "entry_slippage_source": execution["slippage_source"],
            "entry_book_status": execution["book_status"], "stop": stop, "target": None,
            "setup_score": 100.0, "risk_reward_gross": None,
            "reason_codes": candidate.get("reason_codes", []),
            "daily_candle_time": candidate.get("daily_candle_time"),
            "hourly_candle_time": candidate.get("hourly_candle_time"),
        }
        state["positions"].append(position)
        self.save()
        print(f"[ELDER PAPER] OPEN LONG {symbol} @ {fill:.8f}")
        return True

    def _manage_elder(self, position, minute_df, current_price, execution_fn, daily_df, candle_time):
        if "time" in minute_df.columns:
            entry_time = _ms(position.get("entry_candle_time")) or 0
            last_time = _ms(position.get("last_processed_candle_time")) or entry_time
            rows = minute_df[minute_df["time"].astype("int64") > max(entry_time, last_time)]
            for _, row in rows.iterrows():
                ct = _ms(row.get("time"))
                stop = _num(position.get("stop"))
                low, op = _num(row.get("low")), _num(row.get("open"))
                position["last_processed_candle_time"] = ct
                if low <= stop:
                    reference = op if 0 < op < stop else stop
                    self._close(position, reference, "ELDER_PAPER_STOP", execution_fn, ct)
                    return
            position["last_price"] = _num(current_price, position["entry_price"])
            self.save()
        trend = daily_macd_screen(daily_df)
        if trend.get("ready") and not trend.get("bullish"):
            self._close(position, current_price, "ELDER_DAILY_TREND_REVERSAL", execution_fn, candle_time)

    def process_symbol(self, symbol, daily_df, hourly_df, minute_df, candle_time,
                       current_price, execution_estimator):
        if minute_df is None or getattr(minute_df, "empty", True):
            return {"status": "NO_MINUTE_DATA"}
        state = self.load()
        symbol = str(symbol)
        position = next((p for p in list(state["positions"]) if p.get("symbol") == symbol), None)
        if position:
            self._manage_elder(position, minute_df, current_price, execution_estimator, daily_df, candle_time)
            return {"status": "POSITION_MANAGED"}

        minute_time = _ms(candle_time)
        if minute_time is None:
            return {"status": "INVALID_MINUTE_TIME"}
        last_eval = _ms(state["last_evaluated_candle_by_symbol"].get(symbol))
        if last_eval == minute_time:
            return {"status": "CANDLE_ALREADY_EVALUATED"}
        state["last_evaluated_candle_by_symbol"][symbol] = minute_time
        self.save()

        setup = evaluate_triple_screen(daily_df, hourly_df, minute_df, symbol)
        if setup["status"] != "SETUP_READY":
            state["pending_entries"].pop(symbol, None)
            self.save()
            return {"status": setup["status"], "opened": False}

        signal = setup["candidate"]
        last_minute = minute_df.iloc[-1]
        pending = state["pending_entries"].get(symbol)
        hourly_time = signal["hourly_candle_time"]
        if not pending or pending.get("hourly_candle_time") != hourly_time:
            pending = {
                "trigger_price": signal["entry_trigger"],
                "stop": signal["invalidation_price"],
                "hourly_candle_time": hourly_time,
                "created_at_minute": minute_time,
            }
            state["pending_entries"][symbol] = pending
            self.save()
            return {"status": "BUY_STOP_ARMED", "opened": False, "trigger_price": pending["trigger_price"]}

        old_trigger = _num(pending.get("trigger_price"))
        bar_high, bar_low = _num(last_minute.get("high")), _num(last_minute.get("low"))
        stop = _num(pending.get("stop"))
        if bar_high >= old_trigger:
            if bar_low <= stop:
                state["pending_entries"].pop(symbol, None)
                self.save()
                return {"status": "AMBIGUOUS_ENTRY_BAR_REJECTED", "opened": False}
            candidate = {
                "symbol": symbol, "side": "LONG", "strategy_id": "ELDER_TRIPLE_SCREEN_LONG",
                "entry_reference": old_trigger, "invalidation_price": stop,
                "reason_codes": signal["reason_codes"],
                "daily_candle_time": signal["daily_candle_time"],
                "hourly_candle_time": hourly_time,
            }
            opened = self._open_elder(candidate, minute_time, execution_estimator)
            state["pending_entries"].pop(symbol, None)
            self.save()
            return {"status": "OPENED" if opened else "ENTRY_REJECTED", "opened": opened}
        # Elder's trailing entry stop only follows price down; it never moves higher.
        next_trigger = min(old_trigger, bar_high * (1.0 + ENTRY_BUFFER_BPS / 10000.0))
        pending["trigger_price"] = next_trigger
        pending["stop"] = max(stop, _num(signal["invalidation_price"], stop))
        self.save()
        return {"status": "BUY_STOP_WAITING", "opened": False, "trigger_price": next_trigger}

    def snapshot(self, default_fee_rate=0.00045):
        result = super().snapshot(default_fee_rate)
        result["engine_version"] = ENGINE_VERSION
        result["pending_entries"] = dict(self.load().get("pending_entries", {}))
        result["strategy"] = "Triple Screen 1D / 1H / 1m — LONG only"
        result["order_submission_enabled"] = False
        return result
