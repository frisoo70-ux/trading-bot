import unittest
import tempfile
from pathlib import Path
import pandas as pd
from strategy_engine_elder_video import _bullish_direction, _setup, _break_and_order_block, ElderVideoPaperEngine, STRATEGY_ID


def candles(closes, lows=None, opens=None, highs=None, step=60000):
    n=len(closes); lows=lows or [x-1 for x in closes]; opens=opens or list(closes)
    highs=highs or [x+1 for x in closes]
    return pd.DataFrame({"time":[i*step for i in range(n)],"open":opens,"high":highs,
                         "low":lows,"close":closes,"volume":[100.0]*n})


class ElderVideoStrategyTests(unittest.TestCase):
    def test_pdl_sweep_reclaim_and_4h_bias(self):
        daily=candles([100,101,100,100],lows=[98,99,98,97],opens=[99,100,99,98],highs=[102,103,102,102],step=86400000)
        four=candles([100+i*.2 for i in range(30)],step=14400000)
        result=_bullish_direction(daily,four)
        self.assertTrue(result["ready"])
        self.assertTrue(result["daily_sweep"])
        self.assertTrue(result["four_hour_bullish"])
        self.assertTrue(result["bullish"])

    def test_no_pdl_reclaim_blocks_direction(self):
        daily=candles([100,101,100,97],lows=[98,99,98,96],opens=[99,100,99,98],highs=[102,103,102,100],step=86400000)
        four=candles([100+i*.2 for i in range(30)],step=14400000)
        self.assertFalse(_bullish_direction(daily,four)["bullish"])

    def test_setup_requires_both_1h_and_30m_reclaims(self):
        def swept(step):
            return candles([10,10,10,10,10.5],lows=[9.5,9.4,9.3,9.2,8.8],opens=[9.6,9.7,9.8,9.9,9.0],highs=[10,10.2,10.1,10.3,10.8],step=step)
        result=_setup(swept(3600000),swept(1800000))
        self.assertTrue(result["valid"])
        other=candles([10,10,10,10,10.5],lows=[9.5,9.4,9.3,9.2,9.2],opens=[9.6,9.7,9.8,9.9,9.0],highs=[10,10.2,10.1,10.3,10.8],step=1800000)
        self.assertFalse(_setup(swept(3600000),other)["valid"])

    def test_structure_break_and_last_bearish_order_block(self):
        close=[10,10.1,10.0,10.2,10.1,10.0,10.2,11.0]
        op=[9.9,10,10.2,10.0,10.2,10.2,10.4,10.3]
        high=[10.2,10.3,10.4,10.3,10.4,10.3,10.5,11.2]
        low=[9.8,9.9,9.9,9.8,9.9,9.7,10.0,10.2]
        df=candles(close,lows=low,opens=op,highs=high,step=900000)
        result=_break_and_order_block(df)
        self.assertTrue(result["valid"])
        self.assertEqual(result["ob_time"],6*900000)

    def test_no_break_rejected(self):
        df=candles([10,10.1,10,10.2,10.1,10,10.1,10.2],step=900000)
        self.assertFalse(_break_and_order_block(df)["valid"])



    def _engine_with_video_position(self, tmpdir):
        engine = ElderVideoPaperEngine(Path(tmpdir) / "state.json")
        state = engine.load()
        state["positions"] = [{
            "position_id": "test-video-position", "symbol": "SOL", "side": "LONG",
            "strategy_id": STRATEGY_ID, "strategy_version": "test",
            "entry_candle_time": 1000, "last_processed_candle_time": 1000,
            "entry_price": 100.0, "qty": 1.0, "notional": 100.0,
            "entry_fee": 0.0, "entry_fee_rate": 0.0, "fee_rate": 0.0,
            "entry_fee_source": "TEST", "entry_slippage_rate": 0.0,
            "entry_slippage_source": "TEST", "entry_book_status": "OK",
            "stop": 95.0, "tp1": 110.0, "target": 120.0,
            "tp1_reached": False, "setup_score": 100, "risk_reward_gross": 2.0,
            "regime": "TEST", "reason_codes": []
        }]
        return engine

    def _run_position(self, engine, bars):
        df = pd.DataFrame(bars)
        return engine.process_symbol("SOL", None, None, None, None, None, df,
                                    df.iloc[-1]["time"], float(df.iloc[-1]["close"]),
                                    lambda symbol, side, notional, reference: {
                                        "fill_price": reference, "fee_rate": 0.0,
                                        "fee_source": "TEST", "slippage_rate": 0.0,
                                        "slippage_source": "TEST", "book_status": "OK"
                                    })

    def test_stop_before_tp1_cannot_be_retroactively_moved_to_breakeven(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine_with_video_position(tmp)
            bars = [
                {"time": 1000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10},
                {"time": 2000, "open": 99, "high": 111, "low": 94, "close": 105, "volume": 10},
            ]
            self._run_position(engine, bars)
            trade = engine.load()["trades"][-1]
            self.assertEqual(trade["exit_reason"], "ELDER_VIDEO_STOP")
            self.assertAlmostEqual(trade["exit"], 95.0)

    def test_tp1_then_breakeven_stop_is_processed_chronologically(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine_with_video_position(tmp)
            bars = [
                {"time": 1000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10},
                {"time": 2000, "open": 101, "high": 111, "low": 101, "close": 109, "volume": 10},
                {"time": 3000, "open": 105, "high": 109, "low": 99, "close": 101, "volume": 10},
            ]
            self._run_position(engine, bars)
            trade = engine.load()["trades"][-1]
            self.assertEqual(trade["exit_reason"], "ELDER_VIDEO_BREAKEVEN_STOP")
            self.assertAlmostEqual(trade["exit"], 100.0)

    def test_tp2_after_tp1_closes_at_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine_with_video_position(tmp)
            bars = [
                {"time": 1000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10},
                {"time": 2000, "open": 101, "high": 111, "low": 101, "close": 109, "volume": 10},
                {"time": 3000, "open": 110, "high": 121, "low": 110, "close": 120, "volume": 10},
            ]
            self._run_position(engine, bars)
            trade = engine.load()["trades"][-1]
            self.assertEqual(trade["exit_reason"], "ELDER_VIDEO_TARGET")
            self.assertAlmostEqual(trade["exit"], 120.0)

    def test_earlier_stop_bar_prevents_later_tp1_lookahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine_with_video_position(tmp)
            bars = [
                {"time": 1000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10},
                {"time": 2000, "open": 99, "high": 105, "low": 94, "close": 96, "volume": 10},
                {"time": 3000, "open": 101, "high": 111, "low": 101, "close": 110, "volume": 10},
            ]
            self._run_position(engine, bars)
            trade = engine.load()["trades"][-1]
            self.assertEqual(trade["exit_reason"], "ELDER_VIDEO_STOP")
            self.assertAlmostEqual(trade["exit"], 95.0)

if __name__ == "__main__":
    unittest.main()
