"""Independent, order-free PAPER portfolio for Strategy Engine V2."""
from __future__ import annotations
import json, math, threading, time
from datetime import datetime
from pathlib import Path

ENGINE_VERSION = "2.1-paper"
MAX_TRADES = 5000

def _num(value, default=0.0):
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default

def _ms(value):
    try: return int(value)
    except (TypeError, ValueError): return None

def candle_exit(position, candle):
    """Stop-first conservative OHLC execution; returns (reason, reference_price)."""
    side=position.get("side","LONG")
    stop=_num(position.get("stop")); target=_num(position.get("target"))
    high=_num(candle.get("high")); low=_num(candle.get("low")); op=_num(candle.get("open"))
    if side=="LONG":
        if low<=stop: return "STOP", (op if 0<op<stop else stop)
        if high>=target: return "TARGET", target
    else:
        if high>=stop: return "STOP", (op if op>stop else stop)
        if low<=target: return "TARGET", target
    return None,None

class V2PaperEngine:
    def __init__(self,state_path,initial_balance=1000.0,trade_size=100.0,
                 max_positions=5,max_gross_exposure=500.0,leverage=1.0,
                 min_score=55.0,on_save=None):
        self.path=Path(state_path); self.initial_balance=float(initial_balance)
        self.trade_size=float(trade_size); self.max_positions=int(max_positions)
        self.max_gross_exposure=float(max_gross_exposure); self.leverage=float(leverage)
        self.min_score=float(min_score); self.on_save=on_save
        self.lock=threading.RLock(); self.state=None

    def _empty(self):
        return {"engine_version":ENGINE_VERSION,"initial_balance":self.initial_balance,
                "realized_pnl":0.0,"positions":[],"trades":[],
                "last_evaluated_candle_by_symbol":{},"updated_at":None}

    def load(self):
        with self.lock:
            if self.state is not None: return self.state
            state=self._empty()
            try:
                if self.path.exists():
                    with self.path.open("r",encoding="utf-8") as f:
                        saved=json.load(f)
                    if isinstance(saved,dict): state.update(saved)
            except Exception as exc: print("[V2 PAPER] load:",str(exc)[:160])
            state["initial_balance"]=_num(state.get("initial_balance"),self.initial_balance)
            state["realized_pnl"]=_num(state.get("realized_pnl"))
            if not isinstance(state.get("positions"),list): state["positions"]=[]
            if not isinstance(state.get("trades"),list): state["trades"]=[]
            if not isinstance(state.get("last_evaluated_candle_by_symbol"),dict):
                state["last_evaluated_candle_by_symbol"]={}
            self.state=state
            return state

    def save(self):
        with self.lock:
            state=self.load(); state["engine_version"]=ENGINE_VERSION
            state["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_suffix(self.path.suffix+".tmp")
            with tmp.open("w",encoding="utf-8") as f: json.dump(state,f,indent=2,ensure_ascii=False)
            tmp.replace(self.path)
        if callable(self.on_save):
            try: self.on_save()
            except Exception as exc: print("[V2 PAPER] backup scheduling:",str(exc)[:120])
        return True

    def _estimate(self,fn,symbol,side,notional,reference):
        result=fn(symbol,side,notional,reference) or {}
        fill=_num(result.get("fill_price"),reference); fee=_num(result.get("fee_rate"),0.00045)
        if fill<=0 or fee<0 or fee>0.02: raise ValueError("Invalid simulated fill estimate")
        return {"fill":fill,"fee":fee,"fee_source":result.get("fee_source","UNKNOWN"),
                "slippage":_num(result.get("slippage_rate")),
                "slippage_source":result.get("slippage_source","UNKNOWN"),
                "book_status":result.get("book_status","UNKNOWN")}

    def _close(self,p,reference,reason,execution_fn,candle_time=None):
        side=p["side"]; order_side="SELL" if side=="LONG" else "BUY"
        qty=_num(p.get("qty")); ex=self._estimate(execution_fn,p["symbol"],order_side,abs(qty*reference),reference)
        direction=1.0 if side=="LONG" else -1.0
        gross=direction*(ex["fill"]-_num(p["entry_price"]))*qty
        exit_fee=abs(qty*ex["fill"])*ex["fee"]; entry_fee=_num(p.get("entry_fee"))
        net=gross-entry_fee-exit_fee
        trade={"date":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "symbol":p["symbol"],"strategy_id":p["strategy_id"],"side":side,
               "entry":p["entry_price"],"exit":round(ex["fill"],10),"qty":qty,
               "notional":p["notional"],"leverage":self.leverage,"stop":p["stop"],
               "target":p["target"],"exit_reason":reason,
               "entry_candle_time":p.get("entry_candle_time"),"exit_candle_time":candle_time,
               "gross_pnl":round(gross,8),"fees":round(entry_fee+exit_fee,8),
               "entry_fee_rate":p.get("entry_fee_rate"),"exit_fee_rate":ex["fee"],
               "entry_slippage_rate":p.get("entry_slippage_rate"),"exit_slippage_rate":ex["slippage"],
               "fee_source":ex["fee_source"],"exit_slippage_source":ex["slippage_source"],
               "exit_book_status":ex["book_status"],"net_pnl":round(net,8),
               "pnl_pct":round(net/_num(p.get("notional"),self.trade_size)*100,5),
               "setup_score":p.get("setup_score"),"risk_reward_gross":p.get("risk_reward_gross"),
               "regime":p.get("regime"),"reason_codes":p.get("reason_codes",[])}
        s=self.load(); s["trades"].append(trade); s["trades"]=s["trades"][-MAX_TRADES:]
        s["realized_pnl"]=_num(s.get("realized_pnl"))+net
        s["positions"]=[x for x in s["positions"] if x.get("position_id")!=p.get("position_id")]
        self.save()
        return trade

    def _manage(self,p,df,current_price,execution_fn):
        if "time" not in df.columns:
            p["last_price"]=_num(current_price,p["entry_price"]); return
        entry_t=_ms(p.get("entry_candle_time")) or 0
        last_t=_ms(p.get("last_processed_candle_time")) or entry_t
        rows=df[df["time"].astype("int64")>max(entry_t,last_t)]
        for _,row in rows.iterrows():
            ct=_ms(row.get("time")); reason,price=candle_exit(p,row)
            p["last_processed_candle_time"]=ct
            if reason:
                self._close(p,price,"V2_PAPER_"+reason,execution_fn,ct)
                return
        p["last_price"]=_num(current_price,p["entry_price"])
        self.save()

    def _open(self,c,ct,execution_fn):
        s=self.load(); symbol=str(c["symbol"]); side=str(c["side"]).upper()
        if side != "LONG": return False
        if any(p.get("symbol")==symbol for p in s["positions"]): return False
        if len(s["positions"])>=self.max_positions: return False
        exposure=sum(_num(p.get("notional")) for p in s["positions"])
        if exposure+self.trade_size>self.max_gross_exposure+1e-9: return False
        cash=_num(s.get("initial_balance"))+_num(s.get("realized_pnl"))
        margin=sum(_num(p.get("margin",p.get("notional"))) for p in s["positions"])
        if cash-margin<self.trade_size: return False
        ref=_num(c.get("entry_reference"))
        if ref<=0: return False
        ex=self._estimate(execution_fn,symbol,"BUY" if side=="LONG" else "SELL",self.trade_size,ref)
        fill=ex["fill"]; stop=_num(c.get("invalidation_price")); target=_num(c.get("target_price"))
        if (side=="LONG" and not stop<fill<target) or (side=="SHORT" and not target<fill<stop): return False
        qty=self.trade_size/fill; fee=abs(qty*fill)*ex["fee"]
        p={"position_id":f"V2-{symbol}-{c.get('strategy_id')}-{ct}","symbol":symbol,
           "strategy_id":c["strategy_id"],"strategy_version":c.get("strategy_version",ENGINE_VERSION),
           "side":side,"entry_time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "entry_candle_time":_ms(ct),"last_processed_candle_time":_ms(ct),
           "entry_price":round(fill,10),"entry_reference_price":ref,"last_price":ref,
           "qty":qty,"remaining_qty":qty,"notional":self.trade_size,
           "margin":self.trade_size/max(self.leverage,1.0),"leverage":self.leverage,
           "entry_fee":fee,"entry_fee_rate":ex["fee"],"entry_fee_source":ex["fee_source"],
           "entry_slippage_rate":ex["slippage"],"entry_slippage_source":ex["slippage_source"],
           "entry_book_status":ex["book_status"],"stop":stop,"target":target,
           "setup_score":c.get("setup_score"),"risk_reward_gross":c.get("risk_reward_gross"),
           "regime":c.get("regime"),"reason_codes":c.get("reason_codes",[])}
        s["positions"].append(p); self.save()
        print(f"[V2 PAPER] OPEN {side} {symbol} {p['strategy_id']} @ {fill:.8f}")
        return True

    def process_symbol(self,symbol,df,candle_time,current_price,evaluator,execution_estimator,timeframe="15m"):
        if df is None or getattr(df,"empty",True): return {"status":"NO_DATA"}
        s=self.load(); symbol=str(symbol)
        had_position=any(p.get("symbol")==symbol for p in s["positions"])
        for p in list(s["positions"]):
            if p.get("symbol")==symbol: self._manage(p,df,current_price,execution_estimator)
        if had_position: return {"status":"POSITION_MANAGED"}
        key=str(_ms(candle_time) if _ms(candle_time) is not None else candle_time)
        if str(s["last_evaluated_candle_by_symbol"].get(symbol))==key:
            return {"status":"CANDLE_ALREADY_EVALUATED"}
        s["last_evaluated_candle_by_symbol"][symbol]=key; self.save()
        result=evaluator(df,symbol,timeframe=timeframe,source_candle_time=candle_time)
        candidates=result.get("ranked_candidates") or result.get("candidates") or []
        valid=[c for c in candidates if str(c.get("side","")).upper()=="LONG" and c.get("risk_valid") and _num(c.get("setup_score"))>=self.min_score]
        if not valid: return {"status":result.get("status","NO_CANDIDATE"),"opened":False}
        chosen=valid[0]; opened=self._open(chosen,candle_time,execution_estimator)
        return {"status":"OPENED" if opened else "ENTRY_REJECTED","opened":opened,
                "strategy_id":chosen.get("strategy_id"),"symbol":symbol}

    def snapshot(self,default_fee_rate=0.00045):
        s=self.load(); trades=list(s["trades"]); positions=[]; unrealized=0.0
        for p in s["positions"]:
            side=p.get("side","LONG"); d=1.0 if side=="LONG" else -1.0
            price=_num(p.get("last_price"),p.get("entry_price")); qty=_num(p.get("remaining_qty",p.get("qty")))
            fee=_num(p.get("entry_fee_rate"),default_fee_rate)
            pnl=d*(price-_num(p.get("entry_price")))*qty-_num(p.get("entry_fee"))-abs(qty*price)*fee
            view=dict(p); view["current_price"]=price; view["unrealized_pnl"]=round(pnl,8)
            view["pnl_pct"]=round(d*(price/_num(p.get("entry_price"),1)-1)*100,5)
            positions.append(view); unrealized+=pnl
        wins=[t for t in trades if _num(t.get("net_pnl"))>0]
        losses=[t for t in trades if _num(t.get("net_pnl"))<0]
        gp=sum(_num(t.get("net_pnl")) for t in wins); gl=abs(sum(_num(t.get("net_pnl")) for t in losses))
        peak=_num(s["initial_balance"]); running=peak; mdd=0.0
        for t in trades:
            running+=_num(t.get("net_pnl")); peak=max(peak,running)
            if peak>0: mdd=max(mdd,(peak-running)/peak*100)
        by_strategy={}
        for strategy in sorted({str(t.get("strategy_id")) for t in trades}):
            group=[t for t in trades if str(t.get("strategy_id"))==strategy]
            gpos=sum(max(0,_num(t.get("net_pnl"))) for t in group)
            gneg=abs(sum(min(0,_num(t.get("net_pnl"))) for t in group))
            by_strategy[strategy]={"trades":len(group),
                "wins":sum(1 for t in group if _num(t.get("net_pnl"))>0),
                "winrate":round(sum(1 for t in group if _num(t.get("net_pnl"))>0)/len(group)*100,2),
                "net_pnl":round(sum(_num(t.get("net_pnl")) for t in group),6),
                "profit_factor":round(gpos/gneg,4) if gneg else (None if gpos else 0)}
        bal=_num(s["initial_balance"])+_num(s["realized_pnl"])
        return {"engine_version":ENGINE_VERSION,"mode":"PAPER_V2","safe":True,
            "order_submission_enabled":False,"initial_balance":round(_num(s["initial_balance"]),4),
            "balance":round(bal,4),"equity":round(bal+unrealized,4),
            "realized_pnl":round(_num(s["realized_pnl"]),6),"unrealized_pnl":round(unrealized,6),
            "closed_trades":len(trades),"wins":len(wins),"losses":len(losses),
            "winrate":round(len(wins)/len(trades)*100,2) if trades else 0,
            "profit_factor":round(gp/gl,4) if gl else (None if gp else 0),
            "max_drawdown_pct":round(mdd,4),"drawdown_basis":"CLOSED_TRADES_ONLY","open_positions":len(positions),
            "max_positions":self.max_positions,"position_notional":self.trade_size,
            "max_gross_exposure":self.max_gross_exposure,"leverage":self.leverage,
            "by_strategy":by_strategy,"positions":positions,
            "recent_trades":list(reversed(trades[-50:])),"updated_at":s.get("updated_at"),
            "funding_simulated":False,"fees_and_slippage":"ESTIMATED_FROM_L2_WITH_FALLBACK"}
