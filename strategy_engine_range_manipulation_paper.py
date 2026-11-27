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
        return {"engine_version":VERSION,"initial_balance":self.initial,"realized_pnl":0.0,"positions":[],"trades":[],"diagnostics":{},"last_evaluated_candle_by_symbol":{},"range_history":[],"active_range_by_symbol":{},"setup_by_symbol":{},"updated_at":None}
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
                if not isinstance(s.get("range_history"),list): s["range_history"]=[]
                if not isinstance(s.get("active_range_by_symbol"),dict): s["active_range_by_symbol"]={}
                if not isinstance(s.get("setup_by_symbol"),dict): s["setup_by_symbol"]={}
                self.state=s
            return self.state
    def save(self):
        s=self.load(); s["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.path.parent.mkdir(parents=True,exist_ok=True); tmp=self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s,indent=2,ensure_ascii=False)); tmp.replace(self.path)
        if callable(self.on_save):
            try:self.on_save()
            except Exception:pass
    def close_active_range(self,s,symbol):
        active=s.setdefault("active_range_by_symbol",{})
        range_id=active.pop(symbol,None)
        if range_id:
            for item in s.setdefault("range_history",[]):
                if item.get("id")==range_id:
                    item["status"]="CLOSED"
                    break
    def record_range(self,s,symbol,base,hi,lo,ct):
        """Persist a consolidated range episode for chart audit only; never affects entries."""
        try:
            start_ms=int(float(base.iloc[0]["time"]))
            end_ms=int(float(base.iloc[-1]["time"]))+300000
            eval_ms=int(float(ct))
        except (TypeError,ValueError,KeyError,IndexError):
            return
        history=s.setdefault("range_history",[])
        active=s.setdefault("active_range_by_symbol",{})
        current_id=active.get(symbol)
        current=next((x for x in history if x.get("id")==current_id),None) if current_id else None
        if current:
            previous_eval=int(n(current.get("last_seen_eval")))
            previous_high=n(current.get("high")); previous_low=n(current.get("low"))
            overlap=max(0.0,min(previous_high,hi)-max(previous_low,lo))
            denominator=max(1e-12,min(previous_high-previous_low,hi-lo))
            overlap_ratio=overlap/denominator
            if eval_ms-previous_eval<=600000 and eval_ms>=previous_eval and overlap_ratio>=0.55:
                current["high"]=max(previous_high,hi)
                current["low"]=min(previous_low,lo)
                current["end"]=max(int(current.get("end",end_ms)),end_ms)
                current["last_seen_eval"]=eval_ms
                current["samples"]=int(current.get("samples",1))+1
                return
            current["status"]="CLOSED"
        range_id=f"{symbol}:{start_ms}:{eval_ms}"
        history.append({"id":range_id,"symbol":symbol,"start":start_ms,"end":end_ms,
                        "high":float(hi),"low":float(lo),"last_seen_eval":eval_ms,
                        "samples":1,"status":"ACTIVE","validated":True})
        active[symbol]=range_id
        if len(history)>2000:
            s["range_history"]=history[-2000:]
    def process_symbol(self,symbol,df,current_price,ct,execution):
        if df is None or len(df)<25 or not all(c in df.columns for c in ("time","open","high","low","close","volume")):return
        s=self.load(); symbol=str(symbol); price=n(current_price)
        last=df.iloc[-1]; closed_price=n(last.get("close"),price); key=str(last.get("time",ct))
        pos=next((p for p in s["positions"] if p["symbol"]==symbol),None)
        if pos:
            pos["current_price"]=closed_price
            candle_low=n(last.get("low"),closed_price); candle_high=n(last.get("high"),closed_price)
            if candle_low<=n(pos.get("stop")): self.close(pos,pos["stop"],"STOP",execution,key)
            elif candle_high>=n(pos.get("target")): self.close(pos,pos["target"],"TARGET",execution,key)
            else:
                pos["unrealized_pnl"]=(closed_price-pos["entry_price"])*pos["qty"]-pos["entry_fee"]-closed_price*pos["qty"]*pos["fee_rate"]; self.save()
            return
        if s["last_evaluated_candle_by_symbol"].get(symbol)==key:return
        s["last_evaluated_candle_by_symbol"][symbol]=key
        rows=df.tail(26).copy()
        if len(rows)<26:return
        base=rows.iloc[-26:-6]; recent=rows.iloc[-6:-1]; last=rows.iloc[-1]
        hi=n(base.high.max()); lo=n(base.low.min()); width=(hi-lo)/max((hi+lo)/2,1e-12)
        diag={"symbol":symbol,"range_high":hi,"range_low":lo,"range_pct":width*100,"status":"WAIT","last_candle":key,"rejection_reasons":[]}
        closes_base=[n(x) for x in base.close.tolist()]; highs_base=[n(x) for x in base.high.tolist()]; lows_base=[n(x) for x in base.low.tolist()]
        span=max(hi-lo,1e-12); xvals=list(range(len(closes_base))); xmean=sum(xvals)/len(xvals); ymean=sum(closes_base)/len(closes_base)
        denom=sum((x-xmean)**2 for x in xvals) or 1.0
        slope=sum((x-xmean)*(y-ymean) for x,y in zip(xvals,closes_base))/denom
        drift_pct_range=abs(slope*(len(closes_base)-1))/span
        overlap_scores=[]
        for i in range(1,len(base)):
            prev=base.iloc[i-1]; cur=base.iloc[i]
            inter=max(0.0,min(n(prev.high),n(cur.high))-max(n(prev.low),n(cur.low)))
            union=max(n(prev.high),n(cur.high))-min(n(prev.low),n(cur.low))
            overlap_scores.append(inter/max(union,1e-12))
        overlap_mean=sum(overlap_scores)/max(len(overlap_scores),1); touch_band=span*0.20
        upper_touches=sum(1 for h in highs_base if h>=hi-touch_band); lower_touches=sum(1 for l in lows_base if l<=lo+touch_band)
        quality={"normalized_close_drift":drift_pct_range,"mean_candle_overlap":overlap_mean,"upper_boundary_touches":upper_touches,"lower_boundary_touches":lower_touches,"base_candles":len(base),"quality_pass":False}
        diag["range_quality"]=quality; quality_reasons=[]
        if drift_pct_range>0.40:quality_reasons.append("RANGE_DIRECTIONAL_DRIFT_TOO_HIGH")
        if overlap_mean<0.35:quality_reasons.append("RANGE_CANDLE_OVERLAP_TOO_LOW")
        if upper_touches<2:quality_reasons.append("INSUFFICIENT_UPPER_BOUNDARY_REACTIONS")
        if lower_touches<2:quality_reasons.append("INSUFFICIENT_LOWER_BOUNDARY_REACTIONS")
        quality["quality_pass"]=not quality_reasons;diag["range_quality_reasons"]=quality_reasons
        if width>0.05 or width<0.0015:
            self.close_active_range(s,symbol);s.setdefault("setup_by_symbol",{}).pop(symbol,None)
            diag["status"]="NO_SETUP";diag["rejection_reasons"]=["RANGE_OUTSIDE_0_15_TO_5_PERCENT"];s["diagnostics"][symbol]=diag;self.save();return
        if quality_reasons:
            self.close_active_range(s,symbol);s.setdefault("setup_by_symbol",{}).pop(symbol,None)
            diag["status"]="NO_SETUP";diag["rejection_reasons"]=quality_reasons;s["diagnostics"][symbol]=diag;self.save();return
        self.record_range(s,symbol,base,hi,lo,key)
        bins=24;step=(hi-lo)/bins;vp=[0.0]*bins
        for _,row in base.iterrows():
            typ=(n(row.high)+n(row.low)+n(row.close))/3; ix=max(0,min(bins-1,int((typ-lo)/max(step,1e-12))))
            vp[ix]+=n(row.volume)
        level=lo+(vp.index(max(vp))+0.5)*step if step>0 else (hi+lo)/2
        # A setup is persistent and advances only on later closed candles.
        setups=s.setdefault("setup_by_symbol",{}); setup=setups.get(symbol)
        if setup and (setup.get("range_high")!=hi or setup.get("range_low")!=lo):
            # Preserve the frozen POC/range during the setup; do not silently re-anchor.
            hi=n(setup.get("range_high"),hi);lo=n(setup.get("range_low"),lo);level=n(setup.get("poc"),level)
        if not setup:
            sweep_rows=[]
            for _,r in recent.iterrows():
                if n(r.low)<lo and n(r.close)>lo:sweep_rows.append(r)
            if n(last.low)<lo and n(last.close)>lo:sweep_rows.append(last)
            if sweep_rows:
                sw=min(sweep_rows,key=lambda r:n(r.low))
                setup={"stage":"MANIPULATION","range_high":hi,"range_low":lo,"poc":level,
                       "sweep_low":n(sw.low),"sweep_time":str(sw.time),"created_at":key}
                setups[symbol]=setup
        tolerance=max(step*1.25,price*0.0015)
        stage=setup.get("stage") if setup else "NO_MANIPULATION"
        # Stages are only advanced by candles occurring strictly after the sweep candle.
        if setup and key>str(setup.get("sweep_time")):
            if stage=="MANIPULATION" and n(last.close)>level:
                setup["stage"]="ABOVE_POC";setup["above_poc_time"]=key;stage="ABOVE_POC"
            elif stage=="ABOVE_POC" and n(last.low)<=level+tolerance and n(last.high)>=level-tolerance and n(last.close)>=level-tolerance:
                setup["stage"]="POC_RETEST";setup["retest_time"]=key;stage="POC_RETEST"
            elif stage=="POC_RETEST" and key>str(setup.get("retest_time")) and n(last.close)>n(last.open) and n(last.close)>level:
                setup["stage"]="CONFIRMED";setup["confirmation_time"]=key;stage="CONFIRMED"
        diag.update({"volume_level":level,"poc_frozen":bool(setup),"setup_stage":stage,
          "low_sweep_reclaim":bool(setup),"sweep_low":setup.get("sweep_low") if setup else None,
          "poc_crossed":stage in ("ABOVE_POC","POC_RETEST","CONFIRMED"),
          "poc_retested":stage in ("POC_RETEST","CONFIRMED"),"confirmation":stage=="CONFIRMED",
          "retest_tolerance":tolerance,"confirmation_close":n(last.close)})
        if not setup:diag["rejection_reasons"].append("NO_SWEEP_RECLAIM")
        elif stage=="MANIPULATION":diag["rejection_reasons"].append("WAITING_FOR_CLOSE_ABOVE_POC")
        elif stage=="ABOVE_POC":diag["rejection_reasons"].append("WAITING_FOR_POC_RETEST")
        elif stage=="POC_RETEST":diag["rejection_reasons"].append("WAITING_FOR_POST_RETEST_BULLISH_CONFIRMATION")
        diag["status"]="SETUP" if stage=="CONFIRMED" else "WAIT";s["diagnostics"][symbol]=diag
        if stage!="CONFIRMED":self.save();return
        def candle_record(row):
            return {k:(str(row[k]) if k=="time" else n(row[k])) for k in ("time","open","high","low","close","volume")}
        audit={"audit_version":2,"strategy_version":VERSION,"symbol":symbol,"evaluated_at":datetime.now().isoformat(),
          "evaluation_candle":candle_record(last),"range_window_candles":[candle_record(r) for _,r in base.iterrows()],
          "confirmation_window_candles":[candle_record(r) for _,r in recent.iterrows()],
          "range":{"high":hi,"low":lo,"width_pct":width*100,"quality":dict(quality)},
          "volume_profile":{"method":"24 bins; typical_price=(high+low+close)/3; frozen at sweep","bins":bins,"bin_step":step,"level":level,"max_bin_volume":max(vp) if vp else 0.0,"bin_volumes":vp},
          "sweep":{"detected":True,"sweep_low":setup["sweep_low"],"range_low":lo,"sweep_time":setup["sweep_time"]},
          "sequence":{"stage":"CONFIRMED","above_poc_time":setup.get("above_poc_time"),"retest_time":setup.get("retest_time"),"confirmation_time":setup.get("confirmation_time"),"poc":level},
          "confirmation":{"bullish_body":True,"close":n(last.close),"confirmation":True},
          "decision":{"setup":True,"rejection_reasons":[]}}
        diag["entry_audit_candidate"]=audit
        entryref=n(last.close);manipulation_low=n(setup["sweep_low"]);manipulation_high=n(last.high)
        stop=manipulation_low*(1.0-0.001);risk=entryref-stop
        if risk<=0 or risk/entryref>0.025:
            diag["status"]="SKIP_RISK";diag["risk_pct"]=risk/entryref*100 if entryref else None;diag["rejection_reasons"]=["INVALID_OR_OVER_2_5_PERCENT_RISK"];self.save();return
        target=hi;reward=target-entryref
        if reward<=0 or reward/risk<1.0 or target<=entryref:
            diag["status"]="SKIP_LOW_RR";diag["risk_pct"]=risk/entryref*100;diag["target_rr"]=reward/risk if risk else None;diag["rejection_reasons"]=["TARGET_OR_RR_INVALID"];self.save();return
        if len(s["positions"])>=self.max_positions or sum(p["notional"] for p in s["positions"])+self.size>self.max_exposure:
            diag["status"]="SKIP_CAPACITY";diag["rejection_reasons"]=["POSITION_OR_EXPOSURE_LIMIT"];self.save();return
        ex=execution(symbol,"BUY",self.size,entryref) or {};fill=n(ex.get("fill_price"),entryref);fee=n(ex.get("fee_rate"),0.00045);qty=self.size/fill
        p={"symbol":symbol,"side":"LONG","entry_price":fill,"entry_reference":entryref,"qty":qty,"notional":self.size,
          "entry_fee":qty*fill*fee,"fee_rate":fee,"stop":stop,"target":target,"manipulation_low":manipulation_low,
          "manipulation_high":manipulation_high,"target_basis":"OPPOSITE_RANGE_EXTREME","current_price":price,
          "entry_time":datetime.now().isoformat(),"entry_candle_time":key,"exit_reason":None,"entry_audit":audit}
        s["positions"].append(p);setups.pop(symbol,None);diag["status"]="OPEN";diag["side"]="LONG";self.save()
    def close(self,p,reference,reason,execution,exit_candle_time=None):
        s=self.load(); ex=execution(p["symbol"],"SELL",p["notional"],reference) or {}
        fill=n(ex.get("fill_price"),reference); fee=n(ex.get("fee_rate"),p["fee_rate"]); direction=1
        gross=direction*(fill-p["entry_price"])*p["qty"]; net=gross-p["entry_fee"]-abs(fill*p["qty"])*fee
        trade={**p,"exit_price":fill,"exit_fee":abs(fill*p["qty"])*fee,"gross_pnl":gross,"net_pnl":net,"fees":p["entry_fee"]+abs(fill*p["qty"])*fee,"exit_reason":reason,"closed_at":datetime.now().isoformat(),"exit_candle_time":str(exit_candle_time) if exit_candle_time is not None else None}
        s["trades"].append(trade);s["trades"]=s["trades"][-5000:];s["realized_pnl"]=n(s.get("realized_pnl"))+net
        s["positions"]=[x for x in s["positions"] if x["symbol"]!=p["symbol"]];self.save()
    def snapshot(self,default_fee_rate=0.00045):
        s=self.load();tr=s["trades"];pos=[];unreal=0
        for p in s["positions"]:
            q=dict(p);price=n(q.get("current_price"),q["entry_price"]);d=1
            u=d*(price-q["entry_price"])*q["qty"]-q["entry_fee"]-price*q["qty"]*q["fee_rate"]
            q["unrealized_pnl"]=u;pos.append(q);unreal+=u
        wins=[t for t in tr if n(t.get("net_pnl"))>0]; losses=[t for t in tr if n(t.get("net_pnl"))<0]
        grosswin=sum(n(t.get("net_pnl")) for t in wins);grossloss=abs(sum(n(t.get("net_pnl")) for t in losses))
        return {"engine_version":VERSION,"mode":"PAPER","safe":True,"order_submission_enabled":False,"initial_balance":s["initial_balance"],"balance":s["initial_balance"]+s["realized_pnl"],"equity":s["initial_balance"]+s["realized_pnl"]+unreal,"realized_pnl":s["realized_pnl"],"unrealized_pnl":unreal,"trades":len(tr),"closed_trades":len(tr),"wins":len(wins),"losses":len(losses),"winrate":100*len(wins)/len(tr) if tr else 0,"profit_factor":grosswin/grossloss if grossloss else (None if not grosswin else float("inf")),"positions":pos,"open_positions":len(pos),"max_positions":self.max_positions,"open_notional":sum(n(p.get("notional")) for p in pos),"max_gross_exposure":self.max_exposure,"leverage":1.0,"recent_trades":tr[-30:][::-1],"diagnostics":s["diagnostics"],"updated_at":s.get("updated_at")}
