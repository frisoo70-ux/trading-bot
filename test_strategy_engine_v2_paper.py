import tempfile
import unittest
from pathlib import Path
from strategy_engine_v2_paper import V2PaperEngine, candle_exit

class V2PaperEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.engine=V2PaperEngine(Path(self.tmp.name)/"v2.json",trade_size=100,
            max_positions=2,max_gross_exposure=200,min_score=50)
    def tearDown(self): self.tmp.cleanup()
    def execution(self,symbol,side,notional,price):
        return {"fill_price":price,"fee_rate":0.001,"slippage_rate":0.0}
    def candidate(self,symbol="SOL",side="LONG"):
        return {"symbol":symbol,"strategy_id":"LIQUIDITY_SWEEP_RECLAIM","side":side,
            "entry_reference":100,"invalidation_price":95 if side=="LONG" else 105,
            "target_price":110 if side=="LONG" else 90,"setup_score":80,
            "risk_reward_gross":2.0,"risk_valid":True}
    def test_open_persists_order_free_position(self):
        self.assertTrue(self.engine._open(self.candidate(),1000,self.execution))
        snap=self.engine.snapshot()
        self.assertEqual(snap["open_positions"],1)
        self.assertFalse(snap["order_submission_enabled"])
        self.assertTrue(self.engine.path.exists())
    def test_one_position_per_symbol(self):
        self.engine._open(self.candidate(),1000,self.execution)
        self.assertFalse(self.engine._open(self.candidate(),2000,self.execution))
    def test_stop_first_when_both_levels_touch(self):
        self.assertEqual(candle_exit({"side":"LONG","stop":95,"target":110},
            {"open":100,"high":112,"low":94}),("STOP",95))
    def test_short_directional_exits(self):
        p={"side":"SHORT","stop":105,"target":90}
        self.assertEqual(candle_exit(p,{"open":100,"high":106,"low":92}),("STOP",105))
        self.assertEqual(candle_exit(p,{"open":100,"high":103,"low":89}),("TARGET",90))
    def test_close_records_net_after_both_fees(self):
        self.engine._open(self.candidate(),1000,self.execution)
        position=self.engine.load()["positions"][0]
        trade=self.engine._close(position,110,"V2_PAPER_TARGET",self.execution,2000)
        self.assertGreater(trade["gross_pnl"],trade["net_pnl"])
        self.assertEqual(len(self.engine.load()["positions"]),0)
        self.assertGreater(self.engine.load()["realized_pnl"],0)
    def test_independent_initial_balance(self):
        self.assertEqual(self.engine.snapshot()["initial_balance"],1000)
if __name__=="__main__": unittest.main()
