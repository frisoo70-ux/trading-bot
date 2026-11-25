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
