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
        return {"engine_version":VERSION,"initial_balance":self.initial,"realized_pnl":0.0,"positions":[],"trades":[],"diagnostics":{},"last_evaluated_candle_by_symbol":{},"range_history":[],"active_range_by_symbol":{},"updated_at":None}
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
                        "samples":1,"status":"ACTIVE"})
        active[symbol]=range_id
        if len(history)>2000:
            s["range_history"]=history[-2000:]
    def process_symbol(self,symbol,df,current_price,ct,execution):
        if df is None or len(df)<25 or not all(c in df.columns for c in ("time","open","high","low","close","volume")):return
        s=self.load(); symbol=str(symbol); price=n(current_price)
        # Use the last CLOSED 5m candle exclusively for PAPER decisions.
        last_closed=df.iloc[-1]
        closed_price=n(last_closed.get("close"),price)
        closed_time=str(last_closed.get("time",ct))
        pos=next((p for p in s["positions"] if p["symbol"]==symbol),None)
        if pos:
            pos["current_price"]=closed_price
            candle_low=n(last_closed.get("low"),closed_price)
            candle_high=n(last_closed.get("high"),closed_price)
            # Conservative OHLC handling: if both levels are touched, stop wins.
            if candle_low<=n(pos.get("stop")):
                self.close(pos,pos["stop"],"STOP",execution,closed_time)
            elif candle_high>=n(pos.get("target")):
                self.close(pos,pos["target"],"TARGET",execution,closed_time)
            else:
                pos["unrealized_pnl"]=(closed_price-pos["entry_price"])*pos["qty"]-pos["entry_fee"]-closed_price*pos["qty"]*pos["fee_rate"]
                self.save()
            return
        price=closed_price
        ct=closed_time
        key=str(ct)
        if s["last_evaluated_candle_by_symbol"].get(symbol)==key:return
        s["last_evaluated_candle_by_symbol"][symbol]=key
        rows=df.tail(26).copy()
        if len(rows)<26:return
        base=rows.iloc[-26:-6]; recent=rows.iloc[-6:-1]; last=rows.iloc[-1]
        hi=n(base.high.max()); lo=n(base.low.min()); width=(hi-lo)/max((hi+lo)/2,1e-12)
        diag={"symbol":symbol,"range_high":hi,"range_low":lo,"range_pct":width*100,"status":"WAIT","last_candle":key,"rejection_reasons":[]}
        # Consolidation quality: envelope size alone does not establish a sideways range.
        closes_base=[n(x) for x in base.close.tolist()]
        highs_base=[n(x) for x in base.high.tolist()]
        lows_base=[n(x) for x in base.low.tolist()]
        span=max(hi-lo,1e-12)
        xvals=list(range(len(closes_base))); xmean=sum(xvals)/len(xvals); ymean=sum(closes_base)/len(closes_base)
        denom=sum((x-xmean)**2 for x in xvals) or 1.0
        slope=sum((x-xmean)*(y-ymean) for x,y in zip(xvals,closes_base))/denom
        drift_pct_range=abs(slope*(len(closes_base)-1))/span
        overlap_scores=[]
        for i in range(1,len(base)):
            prev=base.iloc[i-1]; cur=base.iloc[i]
            inter=max(0.0,min(n(prev.high),n(cur.high))-max(n(prev.low),n(cur.low)))
            union=max(n(prev.high),n(cur.high))-min(n(prev.low),n(cur.low))
            overlap_scores.append(inter/max(union,1e-12))
        overlap_mean=sum(overlap_scores)/max(len(overlap_scores),1)
        touch_band=span*0.20
        upper_touches=sum(1 for h in highs_base if h>=hi-touch_band)
        lower_touches=sum(1 for l in lows_base if l<=lo+touch_band)
        range_quality={"normalized_close_drift":drift_pct_range,"mean_candle_overlap":overlap_mean,
                       "upper_boundary_touches":upper_touches,"lower_boundary_touches":lower_touches,
                       "base_candles":len(base),"quality_pass":False}
        diag["range_quality"]=range_quality
        quality_reasons=[]
        if drift_pct_range>0.40: quality_reasons.append("RANGE_DIRECTIONAL_DRIFT_TOO_HIGH")
        if overlap_mean<0.35: quality_reasons.append("RANGE_CANDLE_OVERLAP_TOO_LOW")
        if upper_touches<2: quality_reasons.append("INSUFFICIENT_UPPER_BOUNDARY_REACTIONS")
        if lower_touches<2: quality_reasons.append("INSUFFICIENT_LOWER_BOUNDARY_REACTIONS")
        range_quality["quality_pass"]=not quality_reasons
        diag["range_quality_reasons"]=quality_reasons
        # Envelope bounds remain unchanged; quality checks now gate entry setups.
        if width>0.05 or width<0.0015:
            self.close_active_range(s,symbol)
            diag["status"]="NO_SETUP";diag["rejection_reasons"]=["RANGE_OUTSIDE_0_15_TO_5_PERCENT"];s["diagnostics"][symbol]=diag;self.save();return
        if quality_reasons:
            # Rejected candidates must never appear on the dashboard as validated ranges.
            self.close_active_range(s,symbol)
            diag["status"]="NO_SETUP";diag["rejection_reasons"]=quality_reasons
            s["diagnostics"][symbol]=diag;self.save();return
        # Record only structurally validated ranges, after quality filters pass.
        self.record_range(s,symbol,base,hi,lo,ct)
        lows=recent.low.astype(float); closes=recent.close.astype(float)
        sweep_idx=[i for i in range(len(recent)) if n(recent.iloc[i].low)<lo and n(recent.iloc[i].close)>lo]
        # Allow the sweep/reclaim to occur on any of the last five bars; entry confirmation may follow it.
        sweep=bool(sweep_idx) or (n(last.low)<lo and n(last.close)>lo)
        sweep_low=min([n(recent.iloc[i].low) for i in sweep_idx]+([n(last.low)] if n(last.low)<lo and n(last.close)>lo else [])) if sweep else n(recent.low.min())
        bins=24; step=(hi-lo)/bins; vp=[0.0]*bins
        for _,row in base.iterrows():
            typ=(n(row.high)+n(row.low)+n(row.close))/3
            ix=max(0,min(bins-1,int((typ-lo)/max(step,1e-12))))
            vp[ix]+=n(row.volume)
        level=lo+(vp.index(max(vp))+0.5)*step if step>0 else (hi+lo)/2
        tolerance=max(step*2.5,price*0.003)
        # Flexible retest: VP zone OR reclaimed range-low zone. A subsequent bullish close,
        # higher close, or wick rejection can confirm rather than demanding one candle shape.
        vp_retest=abs(n(last.close)-level)<=tolerance or abs(n(last.low)-level)<=tolerance
        range_retest=n(last.low)<=lo+tolerance and n(last.close)>lo
        retest=vp_retest or range_retest
        bullish_body=n(last.close)>n(last.open)
        higher_close=n(last.close)>n(recent.iloc[-2].close)
        rejection_wick=(n(last.close)-n(last.low))/max(n(last.high)-n(last.low),1e-12)>=0.55
        confirmation=bullish_body or higher_close or rejection_wick
        bullish=sweep and n(last.close)>lo and retest and confirmation
        diag.update({"volume_level":level,"vp_bin_step":step,"vp_max_bin_volume":max(vp) if vp else 0.0,
          "low_sweep_reclaim":sweep,"sweep_low":sweep_low,"retest":retest,"vp_retest":vp_retest,
          "range_low_retest":range_retest,"retest_distance":abs(n(last.close)-level),"retest_tolerance":tolerance,
          "bullish_candle":bullish_body,"higher_close":higher_close,"wick_rejection":rejection_wick,
          "confirmation":confirmation,"confirmation_close":n(last.close)})
        if not sweep:diag["rejection_reasons"].append("NO_SWEEP_RECLAIM")
        if not retest:diag["rejection_reasons"].append("NO_VP_OR_RANGE_LOW_RETEST")
        if not confirmation:diag["rejection_reasons"].append("NO_BULLISH_CONFIRMATION")
        diag["status"]="SETUP" if bullish else "WAIT";s["diagnostics"][symbol]=diag
        if not bullish:self.save();return
        # Frozen audit record for this entry decision (stored with the position and trade).
        def candle_record(row):
            return {k: (str(row[k]) if k == "time" else n(row[k])) for k in ("time","open","high","low","close","volume")}
        audit={
          "audit_version":1,"strategy_version":VERSION,"symbol":symbol,"evaluated_at":datetime.now().isoformat(),
          "evaluation_candle":candle_record(last),"range_window_candles":[candle_record(r) for _,r in base.iterrows()],
          "confirmation_window_candles":[candle_record(r) for _,r in recent.iterrows()],
          "range":{"high":hi,"low":lo,"width_pct":width*100,"quality":dict(range_quality)},
          "volume_profile":{"method":"24 bins; typical_price=(high+low+close)/3; volume summed over base window","bins":bins,"bin_step":step,"level":level,"max_bin_volume":max(vp) if vp else 0.0,"bin_volumes":vp},
          "sweep":{"detected":sweep,"sweep_low":sweep_low,"range_low":lo,"candidate_sweep_candles":[candle_record(recent.iloc[i]) for i in sweep_idx]},
          "retest":{"vp_retest":vp_retest,"range_low_retest":range_retest,"any_retest":retest,"distance_close_to_vp":abs(n(last.close)-level),"tolerance":tolerance},
          "confirmation":{"bullish_body":bullish_body,"higher_close":higher_close,"wick_rejection":rejection_wick,"confirmation":confirmation,"close":n(last.close)},
          "decision":{"setup":bullish,"rejection_reasons":list(diag["rejection_reasons"])}
        }
        diag["entry_audit_candidate"]=audit
        entryref=price; manipulation_low=sweep_low; manipulation_high=n(recent.high.max())
        # Place the stop a small buffer below the sweep extreme to avoid an exact-low stop.
        stop=manipulation_low*(1.0-0.001); risk=entryref-stop
        if risk<=0 or risk/entryref>0.025:
            diag["status"]="SKIP_RISK";diag["risk_pct"]=risk/entryref*100 if entryref else None;diag["rejection_reasons"]=["INVALID_OR_OVER_2_5_PERCENT_RISK"];self.save();return
        target=hi; reward=target-entryref
        if reward<=0 or reward/risk<1.0 or target<=entryref:
            diag["status"]="SKIP_LOW_RR";diag["risk_pct"]=risk/entryref*100;diag["target_rr"]=reward/risk if risk else None;diag["rejection_reasons"]=["TARGET_OR_RR_INVALID"];self.save();return
        if len(s["positions"])>=self.max_positions or sum(p["notional"] for p in s["positions"])+self.size>self.max_exposure:
            diag["status"]="SKIP_CAPACITY";diag["rejection_reasons"]=["POSITION_OR_EXPOSURE_LIMIT"];self.save();return
        ex=execution(symbol,"BUY",self.size,entryref) or {}
        fill=n(ex.get("fill_price"),entryref);fee=n(ex.get("fee_rate"),0.00045);qty=self.size/fill
        p={"symbol":symbol,"side":"LONG","entry_price":fill,"entry_reference":entryref,"qty":qty,"notional":self.size,
          "entry_fee":qty*fill*fee,"fee_rate":fee,"stop":stop,"target":target,"manipulation_low":manipulation_low,
          "manipulation_high":manipulation_high,"target_basis":"OPPOSITE_RANGE_EXTREME","current_price":price,
          "entry_time":datetime.now().isoformat(),"entry_candle_time":key,"exit_reason":None,"entry_audit":audit}
        s["positions"].append(p);diag["status"]="OPEN";diag["side"]="LONG";self.save()
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
