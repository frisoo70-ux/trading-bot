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

    def test_directional_base_is_rejected_as_not_a_consolidation(self):
        e=self.make_engine()
        rows=[]
        # Directional drift across a broad envelope, despite acceptable envelope width.
        for i in range(20):
            close=100+i*0.10
            rows.append({"time":i,"open":close,"high":close+0.8,"low":close-0.8,"close":close,"volume":10})
        rows.extend([{"time":i,"open":102,"high":103,"low":101,"close":102,"volume":10} for i in range(20,26)])
        e.process_symbol("XRP",pd.DataFrame(rows),102,25,execution)
        diag=e.load()["diagnostics"].get("XRP")
        self.assertIsNotNone(diag)
        self.assertFalse(diag["range_quality"]["quality_pass"])
        self.assertIn("RANGE_DIRECTIONAL_DRIFT_TOO_HIGH",diag["range_quality_reasons"])
        self.assertEqual(diag["status"],"NO_SETUP")
        self.tmp.cleanup()

    def test_diagnostic_records_no_setup_reasons(self):
        e=self.make_engine()
        df=self.frame(100)
        e.process_symbol("BTC",df,100,1,execution)
        diag=e.load()["diagnostics"].get("BTC")
        self.assertIsNotNone(diag)
        self.assertIn("status",diag)
        self.tmp.cleanup()

    def test_requires_sequential_poc_cross_retest_confirmation(self):
        e=self.make_engine()
        rows=[]
        for i in range(20):
            rows.append({"time":i*300000,"open":101,"high":104,"low":99,"close":101,"volume":10})
        rows.extend([
            {"time":6000000,"open":101,"high":102,"low":98.6,"close":100,"volume":12},
            {"time":6300000,"open":100,"high":102,"low":99.2,"close":101,"volume":12},
            {"time":6600000,"open":100,"high":102,"low":99.5,"close":101,"volume":12},
            {"time":6900000,"open":100,"high":102,"low":99.5,"close":101,"volume":12},
            {"time":7200000,"open":100,"high":102,"low":99.5,"close":101,"volume":12},
            {"time":7500000,"open":100,"high":102,"low":99.5,"close":101,"volume":12},
        ])
        e.process_symbol("SOL",pd.DataFrame(rows),101,7500000,execution)
        self.assertEqual(len(e.load()["positions"]),0,"must not buy without sequential POC cross, retest and later confirmation")
        diag=e.load()["diagnostics"].get("SOL")
        self.assertIsNotNone(diag)
        self.assertNotEqual(diag.get("setup_stage"),"CONFIRMED")
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
        df=self.frame(100)
        df.loc[df.index[-1],["time","open","high","low","close","volume"]]=[123,100,106,94,101,10]
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
