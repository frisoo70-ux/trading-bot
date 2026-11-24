import unittest
import pandas as pd
from strategy_engine_elder_video import _bullish_direction, _setup, _break_and_order_block


def candles(closes, lows=None, opens=None, highs=None, step=60000):
    n=len(closes); lows=lows or [x-1 for x in closes]; opens=opens or list(closes)
    highs=highs or [x+1 for x in closes]
    return pd.DataFrame({"time":[i*step for i in range(n)],"open":opens,"high":highs,
                         "low":lows,"close":closes,"volume":[100.0]*n})


class ElderVideoStrategyTests(unittest.TestCase):
    def test_pdl_sweep_reclaim_and_4h_bias(self):
        daily=candles([100,101,100],lows=[98,99,97],opens=[99,100,98],highs=[102,103,102],step=86400000)
        four=candles([100+i*.2 for i in range(30)],step=14400000)
        result=_bullish_direction(daily,four)
        self.assertTrue(result["ready"])
        self.assertTrue(result["daily_sweep"])
        self.assertTrue(result["four_hour_bullish"])
        self.assertTrue(result["bullish"])

    def test_no_pdl_reclaim_blocks_direction(self):
        daily=candles([100,101,99],lows=[98,99,96],opens=[99,100,98],highs=[102,103,100],step=86400000)
        four=candles([100+i*.2 for i in range(30)],step=14400000)
        self.assertFalse(_bullish_direction(daily,four)["bullish"])

    def test_setup_requires_both_1h_and_30m_reclaims(self):
        def swept(step):
            return candles([10,10,10,10.5],lows=[9,9.5,9.4,8.8],opens=[9.5,9.6,9.7,9.0],highs=[10,10.2,10.1,10.8],step=step)
        result=_setup(swept(3600000),swept(1800000))
        self.assertTrue(result["valid"])
        other=candles([10,10,10,10.5],lows=[9,9.5,9.4,9.2],opens=[9.5,9.6,9.7,9.0],highs=[10,10.2,10.1,10.8],step=1800000)
        self.assertFalse(_setup(swept(3600000),other)["valid"])

    def test_structure_break_and_last_bearish_order_block(self):
        close=[10,10.1,10.0,10.2,10.1,10.0,11.0]
        op=[9.9,10,10.2,10.0,10.2,10.2,10.3]
        high=[10.2,10.3,10.4,10.3,10.4,10.3,11.2]
        low=[9.8,9.9,9.9,9.8,9.9,9.7,10.2]
        df=candles(close,lows=low,opens=op,highs=high,step=900000)
        result=_break_and_order_block(df)
        self.assertTrue(result["valid"])
        self.assertEqual(result["ob_time"],5*900000)

    def test_no_break_rejected(self):
        df=candles([10,10.1,10,10.2,10.1,10,10.2],step=900000)
        self.assertFalse(_break_and_order_block(df)["valid"])


if __name__ == "__main__":
    unittest.main()
