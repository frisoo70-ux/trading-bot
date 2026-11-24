"""Video-derived liquidity sweep -> structure break -> order-block retest PAPER engine.

Rules are explicit experimental interpretations where the source video is visual
rather than numerically prescriptive. Closed candles only; no exchange orders.
"""
from __future__ import annotations
import math
from strategy_engine_v2_paper import V2PaperEngine, _num, _ms

ENGINE_VERSION = "elder-video-paper-1.0"
STRATEGY_ID = "ELDER_VIDEO_LIQUIDITY_OB_RETEST"
MAX_STOP_DISTANCE_PCT = 5.0
MIN_RR_TP2 = 2.0


def _valid(df, minimum=3):
    required = {"time", "open", "high", "low", "close", "volume"}
    if df is None or getattr(df, "empty", True) or len(df) < minimum or not required.issubset(df.columns):
        return False
    try:
        return all(math.isfinite(float(df.iloc[-i][k])) for i in range(1, minimum + 1)
                   for k in ("time", "open", "high", "low", "close", "volume"))
    except (TypeError, ValueError, KeyError, IndexError):
        return False


def _bullish_direction(daily, four_hour):
    if not _valid(daily, 2) or not _valid(four_hour, 22):
        return {"ready": False, "bullish": False, "reason": "DIRECTION_DATA_INSUFFICIENT"}
    # PDL liquidity sweep and reclaim: closed daily candle trades below prior
    # day's low and closes back above it.
    prev_day_low = float(daily.iloc[-2]["low"])
    d = daily.iloc[-1]
    sweep = float(d["low"]) < prev_day_low and float(d["close"]) > prev_day_low
    closes = four_hour["close"].astype(float)
    ema20 = closes.ewm(span=20, adjust=False).mean()
    bullish_4h = float(closes.iloc[-1]) > float(ema20.iloc[-1]) and float(ema20.iloc[-1]) > float(ema20.iloc[-2])
    return {"ready": True, "bullish": bool(sweep and bullish_4h),
            "pdl": prev_day_low, "daily_sweep": bool(sweep), "four_hour_bullish": bool(bullish_4h),
            "reason": "PDL_SWEEP_RECLAIM_AND_4H_BULLISH" if sweep and bullish_4h else "DIRECTION_NOT_CONFIRMED",
            "daily_time": _ms(d["time"]), "four_hour_time": _ms(four_hour.iloc[-1]["time"])}


def _setup(hourly, half_hour):
    if not _valid(hourly, 5) or not _valid(half_hour, 5):
        return {"ready": False, "valid": False, "reason": "SETUP_DATA_INSUFFICIENT"}
    # On both setup frames, sweep the preceding 3-candle low and reclaim it.
    def sweep_reclaim(df):
        prior_low = float(df.iloc[-4:-1]["low"].astype(float).min())
        last = df.iloc[-1]
        return float(last["low"]) < prior_low and float(last["close"]) > prior_low and float(last["close"]) > float(last["open"]), prior_low
    h_ok, h_level = sweep_reclaim(hourly)
    m_ok, m_level = sweep_reclaim(half_hour)
    return {"ready": True, "valid": bool(h_ok and m_ok), "hourly_sweep": h_ok,
            "half_hour_sweep": m_ok, "hourly_level": h_level, "half_hour_level": m_level,
            "reason": "1H_30M_SWEEP_RECLAIM" if h_ok and m_ok else "SETUP_NOT_CONFIRMED",
            "hourly_time": _ms(hourly.iloc[-1]["time"]), "half_hour_time": _ms(half_hour.iloc[-1]["time"])}


def _break_and_order_block(fifteen):
    if not _valid(fifteen, 8):
        return {"ready": False, "valid": False, "reason": "ENTRY_DATA_INSUFFICIENT"}
    prior = fifteen.iloc[-7:-1]
    last = fifteen.iloc[-1]
    swing_high = float(prior["high"].astype(float).max())
    broken = float(last["close"]) > swing_high and float(last["close"]) > float(last["open"])
    if not broken:
        return {"ready": True, "valid": False, "reason": "15M_BULLISH_STRUCTURE_BREAK_MISSING"}
    # Last bearish candle before the displacement/break, searched backward.
    bearish = prior[prior["close"].astype(float) < prior["open"].astype(float)]
    if bearish.empty:
        return {"ready": True, "valid": False, "reason": "ORDER_BLOCK_NOT_FOUND"}
    ob = bearish.iloc[-1]
    low, high = float(ob["low"]), float(max(ob["open"], ob["close"]))
    if low <= 0 or high <= low:
        return {"ready": True, "valid": False, "reason": "ORDER_BLOCK_INVALID"}
    return {"ready": True, "valid": True, "reason": "15M_BREAK_AND_BULLISH_ORDER_BLOCK",
            "swing_high": swing_high, "ob_low": low, "ob_high": high,
            "fifteen_time": _ms(last["time"]), "ob_time": _ms(ob["time"])}


class ElderVideoPaperEngine(V2PaperEngine):
    """Independent virtual portfolio implementing the video sequence."""

    def load(self):
        s = super().load()
        if not isinstance(s.get("video_setups"), dict): s["video_setups"] = {}
        if not isinstance(s.get("last_video_diagnostics"), dict): s["last_video_diagnostics"] = {}
        return s

    def process_symbol(self, symbol, daily_df, four_hour_df, hourly_df, half_hour_df,
                       fifteen_df, minute_df, candle_time, current_price, execution_estimator):
        symbol = str(symbol)
        if not _valid(minute_df, 2) or candle_time is None:
            return {"status": "ENTRY_DATA_INSUFFICIENT"}
        s = self.load()
        pos = next((p for p in list(s["positions"]) if p.get("symbol") == symbol), None)
        if pos:
            # Migrate/manage pre-existing Triple Screen positions safely: they
            # have no TP field, so only their original protective stop applies.
            if pos.get("strategy_id") != STRATEGY_ID:
                entry_t = _ms(pos.get("entry_candle_time")) or 0
                last_t = _ms(pos.get("last_processed_candle_time")) or entry_t
                rows = minute_df[minute_df["time"].astype("int64") > max(entry_t, last_t)]
                for _, bar in rows.iterrows():
                    stop = _num(pos.get("stop"))
                    op, low = float(bar["open"]), float(bar["low"])
                    pos["last_processed_candle_time"] = _ms(bar["time"])
                    if low <= stop:
                        self._close(pos, op if 0 < op < stop else stop, "ELDER_LEGACY_STOP", execution_estimator, _ms(bar["time"]))
                        break
                else:
                    pos["last_price"] = _num(current_price, pos.get("entry_price"))
                    self.save()
                return {"status": "LEGACY_POSITION_MANAGED"}
            # TP1 is a first protection checkpoint. The existing portfolio
            # ledger supports full closes only: TP1 moves the stop to breakeven;
            # TP2 remains the final full-exit target.
            if not pos.get("tp1_reached"):
                for _, bar in minute_df[minute_df["time"].astype("int64") > (_ms(pos.get("entry_candle_time")) or 0)].iterrows():
                    if float(bar["high"]) >= _num(pos.get("tp1"), float("inf")):
                        pos["tp1_reached"] = True
                        pos["stop"] = max(_num(pos.get("stop")), _num(pos.get("entry_price")))
                        self.save()
                        break
            self._manage(pos, minute_df, current_price, execution_estimator)
            return {"status": "POSITION_MANAGED"}

        ct = _ms(candle_time)
        if ct is None: return {"status": "INVALID_CANDLE_TIME"}
        if _ms(s["last_evaluated_candle_by_symbol"].get(symbol)) == ct:
            return {"status": "CANDLE_ALREADY_EVALUATED"}
        s["last_evaluated_candle_by_symbol"][symbol] = ct
        direction = _bullish_direction(daily_df, four_hour_df)
        setup = _setup(hourly_df, half_hour_df)
        entry = _break_and_order_block(fifteen_df)
        diag = {"direction": direction, "setup": setup, "entry": entry, "updated_at": ct}
        s["last_video_diagnostics"][symbol] = diag
        if not (direction.get("bullish") and setup.get("valid") and entry.get("valid")):
            s["video_setups"].pop(symbol, None)
            self.save()
            return {"status": "DIRECTION_BLOCK" if not direction.get("bullish") else
                    ("SETUP_BLOCK" if not setup.get("valid") else entry.get("reason")), "opened": False}

        ob_low, ob_high = entry["ob_low"], entry["ob_high"]
        last = minute_df.iloc[-1]
        # Retest into OB followed by a bullish rejection close above its midpoint.
        retest = float(last["low"]) <= ob_high and float(last["high"]) >= ob_low
        rejection = float(last["close"]) > float(last["open"]) and float(last["close"]) >= (ob_low + ob_high) / 2
        if not (retest and rejection):
            self.save()
            return {"status": "WAITING_ORDER_BLOCK_RETEST", "opened": False}
        reference = float(last["close"])
        stop = ob_low * 0.999
        risk = reference - stop
        if risk <= 0 or (risk / reference * 100) > MAX_STOP_DISTANCE_PCT:
            self.save()
            return {"status": "STOP_INVALID_OR_TOO_WIDE", "opened": False}
        # TP1 = 1R; TP2 = 2R. Experimental defaults because video gives no
        # numeric target formula; these are clearly tagged as model assumptions.
        tp1, tp2 = reference + risk, reference + MIN_RR_TP2 * risk
        candidate = {"symbol": symbol, "side": "LONG", "strategy_id": STRATEGY_ID,
                     "strategy_version": ENGINE_VERSION, "entry_reference": reference,
                     "invalidation_price": stop, "target_price": tp2, "tp1": tp1,
                     "risk_reward_gross": MIN_RR_TP2, "setup_score": 100,
                     "reason_codes": ["PDL_SWEEP_RECLAIM", "4H_BULLISH", "1H_30M_SWEEP_RECLAIM",
                                      "15M_STRUCTURE_BREAK", "LAST_BEARISH_CANDLE_ORDER_BLOCK",
                                      "1M_ORDER_BLOCK_RETEST_REJECTION", "TP1_1R_ASSUMPTION", "TP2_2R_ASSUMPTION"]}
        opened = self._open(candidate, ct, execution_estimator)
        if opened:
            p = next((p for p in self.load()["positions"] if p.get("symbol") == symbol), None)
            if p:
                p["tp1"] = tp1
                p["order_block_low"] = ob_low
                p["order_block_high"] = ob_high
                p["daily_candle_time"] = direction.get("daily_time")
                p["four_hour_candle_time"] = direction.get("four_hour_time")
                p["hourly_candle_time"] = setup.get("hourly_time")
                p["half_hour_candle_time"] = setup.get("half_hour_time")
                p["fifteen_candle_time"] = entry.get("fifteen_time")
                self.save()
        return {"status": "OPENED" if opened else "ENTRY_REJECTED", "opened": opened,
                "strategy_id": STRATEGY_ID}

    def snapshot(self, default_fee_rate=0.00045):
        out = super().snapshot(default_fee_rate)
        s = self.load()
        out.update({"engine_version": ENGINE_VERSION, "strategy": "PDL sweep -> 1H/30m setup -> 15m break -> OB retest (1m)",
                    "order_submission_enabled": False,
                    "last_video_diagnostics": dict(s.get("last_video_diagnostics", {}))})
        return out
