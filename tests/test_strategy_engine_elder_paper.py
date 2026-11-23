import unittest
import pandas as pd
from strategy_engine_elder_paper import evaluate_three_candle_setup


def frame(rows):
    return pd.DataFrame(rows)


class Elder3PaperTests(unittest.TestCase):
    def setUp(self):
        self.valid = [
            {"open":100,"high":104,"low":99,"close":103,"atr":2,"ema20":99,"ema50":95},
            {"open":103,"high":103.5,"low":101,"close":101.5,"atr":2,"ema20":99,"ema50":95},
            {"open":101.5,"high":105,"low":101,"close":104.5,"atr":2,"ema20":99,"ema50":95},
        ]

    def test_valid_three_candle_signal(self):
        result=evaluate_three_candle_setup(frame(self.valid),"SOL")
        self.assertEqual(result["status"],"SIGNAL")
        self.assertEqual(result["candidate"]["side"],"LONG")
        self.assertEqual(result["candidate"]["strategy_id"],"ELDER_3_CANDLE_PAPER")

    def test_trigger_failure_rejected(self):
        rows=[dict(x) for x in self.valid]
        rows[2]["close"]=102
        self.assertEqual(evaluate_three_candle_setup(frame(rows),"SOL")["status"],"NO_SETUP")

    def test_pullback_breaks_c1_low_rejected(self):
        rows=[dict(x) for x in self.valid]
        rows[1]["low"]=98
        self.assertEqual(evaluate_three_candle_setup(frame(rows),"SOL")["status"],"NO_SETUP")

    def test_missing_atr_rejected(self):
        rows=[dict(x) for x in self.valid]
        rows[2]["atr"]=None
        self.assertEqual(evaluate_three_candle_setup(frame(rows),"SOL")["status"],"INVALID_DATA")

    def test_too_few_candles(self):
        self.assertEqual(evaluate_three_candle_setup(frame(self.valid[:2]),"SOL")["status"],"INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
