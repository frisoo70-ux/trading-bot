import unittest

import pandas as pd

from strategy_engine_v2 import evaluate_strategy_candidates


class StrategyEngineV2Tests(unittest.TestCase):
    def make_frame(self, sweep=False):
        rows = []
        for index in range(70):
            rows.append({
                "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0,
                "volume": 100.0, "ema20": 100.5 + index * 0.005, "ema50": 100.0,
                "rsi": 58.0, "atr": 1.0, "obv": 1200.0 + index, "obv_ma5": 1100.0 + index,
            })
        if sweep:
            rows[-1].update({"open": 98.0, "high": 100.0, "low": 96.8, "close": 98.5,
                             "ema20": 100.7, "ema50": 100.0, "rsi": 52.0})
        return pd.DataFrame(rows)

    def test_insufficient_data_is_non_executable(self):
        result = evaluate_strategy_candidates(self.make_frame().head(20), "SOL")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["candidates"], [])

    def test_sweep_candidate_is_normalized_and_shadow_only(self):
        result = evaluate_strategy_candidates(self.make_frame(sweep=True), "SOL", "15m", "2026-11-18T10:00:00")
        self.assertEqual(result["status"], "OK")
        self.assertFalse(result["execution_allowed"])
        sweep = [item for item in result["candidates"] if item["strategy_id"] == "LIQUIDITY_SWEEP_RECLAIM" and item["side"] == "LONG"]
        self.assertEqual(len(sweep), 1)
        self.assertIn("risk_reward_gross", sweep[0])
        self.assertIn("reason_codes", sweep[0])
        self.assertEqual(sweep[0]["data_freshness"], "CLOSED_CANDLE_ONLY")

    def test_missing_required_columns_fails_closed(self):
        result = evaluate_strategy_candidates(pd.DataFrame({"close": [1.0] * 70}), "SOL")
        self.assertEqual(result["status"], "INVALID_DATA")
        self.assertFalse(result["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
