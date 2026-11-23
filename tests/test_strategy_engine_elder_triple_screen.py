import unittest
import pandas as pd
from strategy_engine_elder_triple_screen import (
    daily_macd_screen, hourly_force_index_screen, evaluate_triple_screen,
)


def make_daily(n=80):
    close = [100 + i * 0.8 for i in range(n)]
    return pd.DataFrame({
        "time": [i * 86400000 for i in range(n)],
        "open": [x - 0.2 for x in close], "high": [x + 1 for x in close],
        "low": [x - 1 for x in close], "close": close, "volume": [1000.0] * n,
    })


def make_hourly(n=80):
    close = [100 + i * 0.2 for i in range(n)]
    # Latest bars create a negative but rising Force Index EMA(2).
    close[-3:] = [close[-4] - 2, close[-4] - 1, close[-4] - 0.5]
    return pd.DataFrame({
        "time": [i * 3600000 for i in range(n)],
        "open": close, "high": [x + 1 for x in close],
        "low": [x - 2 for x in close], "close": close,
        "volume": [100.0] * n,
    })


def make_minute(n=10):
    close = [100 + i * 0.05 for i in range(n)]
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

    def test_hourly_screen_rejects_non_pullback(self):
        df = make_hourly()
        df["close"] = [100 + i for i in range(len(df))]
        result = hourly_force_index_screen(df)
        self.assertFalse(result["pullback"])

    def test_three_timeframes_are_required(self):
        result = evaluate_triple_screen(make_daily(), make_hourly(), make_minute(), "SOL")
        self.assertIn(result["status"], {"SETUP_READY", "HOURLY_PULLBACK_NOT_READY", "INVALID_STOP", "STOP_TOO_WIDE"})

    def test_missing_volume_is_rejected(self):
        df = make_hourly().drop(columns=["volume"])
        self.assertFalse(hourly_force_index_screen(df)["ready"])


if __name__ == "__main__":
    unittest.main()
