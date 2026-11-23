"""Independent order-free PAPER portfolio for the Elder-inspired 3-candle setup."""
from strategy_engine_v2_paper import V2PaperEngine, _num, _ms, candle_exit
from strategy_engine_elder_paper import evaluate_three_candle_setup, ENGINE_VERSION
from datetime import datetime

class ElderPaperEngine(V2PaperEngine):
    def process_symbol(self,symbol,df,candle_time,current_price,execution_estimator,timeframe="15m"):
        if df is None or getattr(df,"empty",True): return {"status":"NO_DATA"}
        s=self.load(); symbol=str(symbol)
        had=any(p.get("symbol")==symbol for p in s["positions"])
        for p in list(s["positions"]):
            if p.get("symbol")==symbol: self._manage(p,df,current_price,execution_estimator)
        if had: return {"status":"POSITION_MANAGED"}
        key=str(_ms(candle_time) if _ms(candle_time) is not None else candle_time)
        if str(s["last_evaluated_candle_by_symbol"].get(symbol))==key:
            return {"status":"CANDLE_ALREADY_EVALUATED"}
        s["last_evaluated_candle_by_symbol"][symbol]=key; self.save()
        result=evaluate_three_candle_setup(df,symbol,timeframe=timeframe,source_candle_time=candle_time)
        c=result.get("candidate")
        if not c: return {"status":result.get("status","NO_SETUP"),"opened":False}
        c.update({"symbol":symbol,"strategy_id":"ELDER_3_CANDLE_PAPER","strategy_version":ENGINE_VERSION,
                  "setup_score":100.0,"risk_reward_gross":c.get("risk_reward"),
                  "risk_valid":True})
        opened=self._open(c,candle_time,execution_estimator)
        return {"status":"OPENED" if opened else "ENTRY_REJECTED","opened":opened,"symbol":symbol}
