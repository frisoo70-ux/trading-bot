import tempfile, unittest
from pathlib import Path
import pandas as pd
from strategy_engine_range_manipulation_paper import RangeManipulationPaper

def execution(symbol, side, notional, reference):
    assert side == "BUY" or side == "SELL"
    return {"fill_price": reference, "fee_rate": 0.0}

class RangeManipPaperTests(unittest.TestCase):
    def make_engine(self):
        self.tmp=tempfile.TemporaryDirectory()
        return RangeManipulationPaper(Path(self.tmp.name)/"state.json", on_save=None)

    def frame(self, price):
        return pd.DataFrame([{"time":i,"open":price,"high":price+1,"low":price-1,"close":price,"volume":10} for i in range(30)])

    def test_diagnostic_records_no_setup_reasons(self):
        e=self.make_engine()
        df=self.frame(100)
        e.process_symbol("BTC",df,100,1,execution)
        diag=e.load()["diagnostics"].get("BTC")
        self.assertIsNotNone(diag)
        self.assertIn("status",diag)
        self.tmp.cleanup()

    def test_relaxed_sweep_retest_can_open_long(self):
        e=self.make_engine()
        rows=[]
        # Twenty-bar compact base with prior range liquidity above.
        for i in range(20):
            rows.append({"time":i,"open":101,"high":103,"low":99,"close":101,"volume":10})
        # Sweep below range, reclaim and a retest/rejection in subsequent candles.
        rows.extend([
            {"time":20,"open":100,"high":101,"low":98,"close":100,"volume":12},
            {"time":21,"open":100,"high":102,"low":99.2,"close":101,"volume":12},
            {"time":22,"open":100,"high":102,"low":99.5,"close":101,"volume":12},
            {"time":23,"open":100,"high":102,"low":99.5,"close":101,"volume":12},
            {"time":24,"open":100,"high":102,"low":99.5,"close":101,"volume":12},
            {"time":25,"open":100,"high":102,"low":99.5,"close":101,"volume":12},
        ])
        df=pd.DataFrame(rows)
        e.process_symbol("SOL",df,100,25,execution)
        self.assertEqual(len(e.load()["positions"]),1)
        self.assertEqual(e.load()["positions"][0]["side"],"LONG")
        audit=e.load()["positions"][0].get("entry_audit")
        self.assertIsNotNone(audit)
        self.assertTrue(audit["decision"]["setup"])
        self.assertEqual(audit["volume_profile"]["bins"],24)
        self.assertEqual(len(audit["range_window_candles"]),20)
        self.assertEqual(len(audit["confirmation_window_candles"]),5)
        self.tmp.cleanup()

    def test_range_history_coalesces_overlapping_windows_and_starts_new_on_break(self):
        e=self.make_engine();s=e.load()
        first=pd.DataFrame([{"time":i*300000,"high":103.0,"low":99.0} for i in range(20)])
        e.record_range(s,"SOL",first,103.0,99.0,20*300000)
        shifted=pd.DataFrame([{"time":(i+1)*300000,"high":103.2,"low":98.8} for i in range(20)])
        e.record_range(s,"SOL",shifted,103.2,98.8,21*300000)
        self.assertEqual(len(s["range_history"]),1)
        self.assertEqual(s["range_history"][0]["samples"],2)
        separated=pd.DataFrame([{"time":(i+2)*300000,"high":110.0,"low":106.0} for i in range(20)])
        e.record_range(s,"SOL",separated,110.0,106.0,22*300000)
        self.assertEqual(len(s["range_history"]),2)
        self.assertEqual(s["range_history"][0]["status"],"CLOSED")
        self.assertEqual(s["range_history"][1]["status"],"ACTIVE")
        self.tmp.cleanup()

    def test_stop_first_when_closed_candle_touches_stop_and_target(self):
        e=self.make_engine();s=e.load()
        s["positions"]=[{"symbol":"BTC","side":"LONG","entry_price":100,"qty":1,"notional":100,"entry_fee":0,"fee_rate":0,"stop":95,"target":105,"current_price":100}]
        df=pd.DataFrame([{"time":123,"open":100,"high":106,"low":94,"close":101,"volume":10}])
        e.process_symbol("BTC",df,999,123,execution)
        self.assertEqual(len(e.load()["positions"]),0)
        self.assertEqual(e.load()["trades"][-1]["exit_reason"],"STOP")
        self.assertEqual(e.load()["trades"][-1]["exit_price"],95)
        self.tmp.cleanup()

    def test_long_stop(self):
        e=self.make_engine();s=e.load()
        s["positions"]=[{"symbol":"BTC","side":"LONG","entry_price":100,"qty":1,"notional":100,"entry_fee":0,"fee_rate":0,"stop":95,"target":105,"current_price":100}]
        e.process_symbol("BTC",self.frame(94),94,1,execution)
        self.assertEqual(len(e.load()["positions"]),0)
        self.assertEqual(e.load()["trades"][-1]["exit_reason"],"STOP")
        self.tmp.cleanup()

    def test_long_target(self):
        e=self.make_engine();s=e.load()
        s["positions"]=[{"symbol":"BTC","side":"LONG","entry_price":100,"qty":1,"notional":100,"entry_fee":0,"fee_rate":0,"stop":95,"target":105,"current_price":100}]
        e.process_symbol("BTC",self.frame(106),106,1,execution)
        self.assertEqual(len(e.load()["positions"]),0)
        self.assertEqual(e.load()["trades"][-1]["exit_reason"],"TARGET")
        self.tmp.cleanup()

if __name__=="__main__":unittest.main()
