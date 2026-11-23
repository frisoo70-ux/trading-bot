import unittest
import pandas as pd
from strategy_engine_elder_triple_screen import (
    daily_macd_screen, hourly_force_index_screen, evaluate_triple_screen,
)


def make_daily(n=80):
    close = [100 + i * 0.1 + i * i * 0.01 for i in range(n)]
    return pd.DataFrame({
        "time": [i * 86400000 for i in range(n)],
        "open": [x - 0.2 for x in close], "high": [x + 1 for x in close],
        "low": [x - 1 for x in close], "close": close, "volume": [1000.0] * n,
    })


def make_hourly(n=80):
    close = [100 + i * 0.2 for i in range(n)]
    base = close[-4]
    close[-3:] = [base - 2.0, base - 2.5, base - 2.4]
    return pd.DataFrame({
        "time": [i * 3600000 for i in range(n)],
        "open": close, "high": [x + 1 for x in close],
        "low": [x - 2 for x in close], "close": close,
        "volume": [100.0] * n,
    })


def make_minute(n=10):
    start = make_hourly().iloc[-1]["close"]
    close = [start + i * 0.02 for i in range(n)]
    return pd.DataFrame({
        "time": [i * 60000 for i in range(n)],
        "open": close, "high": [x + 0.05 for x in close],
        "low": [x - 0.05 for x in close], "close": close, "volume": [10.0] * n,
    })


class ElderTripleScreenTests(unittest.TestCase):
    def test_daily_screen_needs_rising_macd_histogram(self):
        result = daily_macd_screen(make_daily())
        self.assertTrue(result["ready"])
        self.assertTrue(result["bullish"])

    def test_hourly_force_index_negative_and_rising(self):
        result = hourly_force_index_screen(make_hourly())
        self.assertTrue(result["ready"])
        self.assertTrue(result["pullback"])

    def test_non_pullback_is_rejected(self):
        df = make_hourly()
        close = [100 + i for i in range(len(df))]
        df["open"] = close
        df["close"] = close
        self.assertFalse(hourly_force_index_screen(df)["pullback"])

    def test_full_three_screen_setup(self):
        result = evaluate_triple_screen(make_daily(), make_hourly(), make_minute(), "SOL")
        self.assertEqual(result["status"], "SETUP_READY")
        self.assertEqual(result["candidate"]["side"], "LONG")
        self.assertEqual(result["candidate"]["strategy_id"], "ELDER_TRIPLE_SCREEN_LONG")

    def test_each_timeframe_is_required(self):
        daily = make_daily()
        hourly = make_hourly()
        minute = make_minute()

        self.assertEqual(
            evaluate_triple_screen(None, hourly, minute, "SOL")["status"],
            "INSUFFICIENT_DATA",
        )
        self.assertEqual(
            evaluate_triple_screen(daily, None, minute, "SOL")["status"],
            "INSUFFICIENT_DATA",
        )
        self.assertEqual(
            evaluate_triple_screen(daily, hourly, None, "SOL")["status"],
            "INSUFFICIENT_DATA",
        )

    def test_missing_volume_is_rejected(self):
        df = make_hourly().drop(columns=["volume"])
        self.assertFalse(hourly_force_index_screen(df)["ready"])


if __name__ == "__main__":
    unittest.main()
