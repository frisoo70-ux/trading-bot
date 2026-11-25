"""Isolated PAPER test engine for range/sweep/volume-profile retest strategy."""
from __future__ import annotations
import json, math, threading
from pathlib import Path
from datetime import datetime
import pandas as pd

VERSION="range_manipulation_v1"
def n(x,d=0.0):
    try: v=float(x); return v if math.isfinite(v) else d
    except Exception: return d

class RangeManipulationPaper:
    def __init__(self,path,initial_balance=1000,trade_size=100,max_positions=5,max_exposure=500,on_save=None):
        self.path=Path(path); self.initial=float(initial_balance); self.size=float(trade_size)
        self.max_positions=int(max_positions); self.max_exposure=float(max_exposure); self.on_save=on_save
        self.lock=threading.RLock(); self.state=None
    def empty(self):
        return {"engine_version":VERSION,"initial_balance":self.initial,"realized_pnl":0.0,"positions":[],"trades":[],"diagnostics":{},"last_evaluated_candle_by_symbol":{},"updated_at":None}
    def load(self):
        with self.lock:
            if self.state is None:
                s=self.empty()
                try:
                    if self.path.exists(): s.update(json.loads(self.path.read_text()))
                except Exception: pass
                if not isinstance(s.get("positions"),list): s["positions"]=[]
                if not isinstance(s.get("trades"),list): s["trades"]=[]
                if not isinstance(s.get("diagnostics"),dict): s["diagnostics"]={}
                if not isinstance(s.get("last_evaluated_candle_by_symbol"),dict): s["last_evaluated_candle_by_symbol"]={}
                self.state=s
            return self.state
    def save(self):
        s=self.load(); s["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.path.parent.mkdir(parents=True,exist_ok=True); tmp=self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s,indent=2,ensure_ascii=False)); tmp.replace(self.path)
        if callable(self.on_save):
            try:self.on_save()
            except Exception:pass
    def process_symbol(self,symbol,df,current_price,ct,execution):
        if df is None or len(df)<25 or not all(c in df.columns for c in ("time","open","high","low","close","volume")):return
        s=self.load(); symbol=str(symbol); price=n(current_price)
        pos=next((p for p in s["positions"] if p["symbol"]==symbol),None)
        if pos:
            pos["current_price"]=price
            if price<=pos["stop"]:
                self.close(pos,pos["stop"],"STOP",execution)
            elif price>=pos["target"]:
                self.close(pos,pos["target"],"TARGET",execution)
            else:
                pos["unrealized_pnl"]=(price-pos["entry_price"])*pos["qty"]-pos["entry_fee"]-price*pos["qty"]*pos["fee_rate"]
                self.save()
            return
        key=str(ct)
        if s["last_evaluated_candle_by_symbol"].get(symbol)==key:return
        s["last_evaluated_candle_by_symbol"][symbol]=key
        # Use closed bars, range from 20 bars preceding the manipulation candidate.
        rows=df.tail(26).copy()
        if len(rows)<26:return
        base=rows.iloc[-26:-6]; recent=rows.iloc[-6:-1]; last=rows.iloc[-1]
        hi=n(base.high.max()); lo=n(base.low.min()); width=(hi-lo)/max((hi+lo)/2,1e-12)
        diag={"symbol":symbol,"range_high":hi,"range_low":lo,"range_pct":width*100,"status":"NO_SETUP","last_candle":key}
        if width>0.035 or width<0.002: s["diagnostics"][symbol]=diag;self.save();return
        # Sweep + reclaim; require a recent excursion beyond range and closed back inside.
        bearish_sweep= n(recent.low.min())<lo and n(last.close)>lo and n(last.close)<hi
        bullish_sweep= n(recent.high.max())>hi and n(last.close)<hi and n(last.close)>lo
        # VP approximation: bin range into 24 bins, assign candle volume to typical-price bins.
        typical=(base.high+base.low+base.close)/3
        bins=24; step=(hi-lo)/bins; vp=[0.0]*bins
        for _,row in base.iterrows():
            ix=max(0,min(bins-1,int((n(row.high)+n(row.low)+n(row.close))/3-lo/max(step,1e-12))))
            vp[ix]+=n(row.volume)
        level=lo+(vp.index(max(vp))+0.5)*step if step>0 else (hi+lo)/2
        # Retest and rejection: closes near the volume concentration with candle directional body.
        retest=abs(n(last.close)-level)<=max(step*1.5,price*0.0015)
        bullish= bearish_sweep and retest and n(last.close)>n(last.open)
        bearish= bullish_sweep and retest and n(last.close)<n(last.open)
        diag.update({"volume_level":level,"bearish_sweep":bearish_sweep,"bullish_sweep":bullish_sweep,"retest":retest,"status":"SETUP" if bullish or bearish else "WAIT"})
        s["diagnostics"][symbol]=diag
        if not (bullish or bearish):self.save();return
        side="LONG" if bullish else "SHORT"; entryref=price
        stop=lo if bullish else hi
        risk=abs(entryref-stop)
        if risk<=0 or risk/entryref>0.025:self.save();return
        target=entryref+2*risk if bullish else entryref-2*risk
        if len(s["positions"])>=self.max_positions or sum(p["notional"] for p in s["positions"])+self.size>self.max_exposure:self.save();return
        if any(p["symbol"]==symbol for p in s["positions"]):self.save();return
        ex=execution(symbol,"BUY" if bullish else "SELL",self.size,entryref) or {}
        fill=n(ex.get("fill_price"),entryref); fee=n(ex.get("fee_rate"),0.00045); qty=self.size/fill
        p={"symbol":symbol,"side":side,"entry_price":fill,"entry_reference":entryref,"qty":qty,"notional":self.size,"entry_fee":qty*fill*fee,"fee_rate":fee,"stop":stop,"target":target,"current_price":price,"entry_time":datetime.now().isoformat(),"entry_candle_time":key,"exit_reason":None}
        s["positions"].append(p);diag["status"]="OPEN";diag["side"]=side;self.save()
    def close(self,p,reference,reason,execution):
        s=self.load(); ex=execution(p["symbol"],"SELL" if p["side"]=="LONG" else "BUY",p["notional"],reference) or {}
        fill=n(ex.get("fill_price"),reference); fee=n(ex.get("fee_rate"),p["fee_rate"]); direction=1 if p["side"]=="LONG" else -1
        gross=direction*(fill-p["entry_price"])*p["qty"]; net=gross-p["entry_fee"]-abs(fill*p["qty"])*fee
        trade={**p,"exit_price":fill,"exit_fee":abs(fill*p["qty"])*fee,"gross_pnl":gross,"net_pnl":net,"fees":p["entry_fee"]+abs(fill*p["qty"])*fee,"exit_reason":reason,"closed_at":datetime.now().isoformat()}
        s["trades"].append(trade);s["trades"]=s["trades"][-5000:];s["realized_pnl"]=n(s.get("realized_pnl"))+net
        s["positions"]=[x for x in s["positions"] if x["symbol"]!=p["symbol"]];self.save()
    def snapshot(self,default_fee_rate=0.00045):
        s=self.load();tr=s["trades"];pos=[];unreal=0
        for p in s["positions"]:
            q=dict(p);price=n(q.get("current_price"),q["entry_price"]);d=1 if q.get("side")=="LONG" else -1
            u=d*(price-q["entry_price"])*q["qty"]-q["entry_fee"]-price*q["qty"]*q["fee_rate"]
            q["unrealized_pnl"]=u;pos.append(q);unreal+=u
        wins=[t for t in tr if n(t.get("net_pnl"))>0]; losses=[t for t in tr if n(t.get("net_pnl"))<0]
        grosswin=sum(n(t.get("net_pnl")) for t in wins);grossloss=abs(sum(n(t.get("net_pnl")) for t in losses))
        return {"engine_version":VERSION,"mode":"PAPER","safe":True,"order_submission_enabled":False,"initial_balance":s["initial_balance"],"balance":s["initial_balance"]+s["realized_pnl"],"equity":s["initial_balance"]+s["realized_pnl"]+unreal,"realized_pnl":s["realized_pnl"],"unrealized_pnl":unreal,"trades":len(tr),"closed_trades":len(tr),"wins":len(wins),"losses":len(losses),"winrate":100*len(wins)/len(tr) if tr else 0,"profit_factor":grosswin/grossloss if grossloss else (None if not grosswin else float("inf")),"positions":pos,"open_positions":len(pos),"max_positions":self.max_positions,"open_notional":sum(n(p.get("notional")) for p in pos),"max_gross_exposure":self.max_exposure,"leverage":1.0,"recent_trades":tr[-30:][::-1],"diagnostics":s["diagnostics"],"updated_at":s.get("updated_at")}
