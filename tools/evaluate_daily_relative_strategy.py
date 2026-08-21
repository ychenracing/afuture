"""Reproduce the two-window real-data acceptance gate for the live strategy.

This research tool uses only specific-contract 60-minute history fetched by the
research workflow. Parameters and product eligibility are chosen before the
following window is evaluated; the final OOS period never participates in
selection.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

PRODUCTS = ("A","C","EG","FG","I","M","MA","OI","P","PP","RB","RM","SA","TA","Y")
SPECS = {
    "A": (10.0,1.0), "C":(10.0,1.0), "EG":(10.0,1.0), "FG":(20.0,1.0),
    "I":(100.0,0.5), "M":(10.0,1.0), "MA":(10.0,1.0), "OI":(10.0,1.0),
    "P":(10.0,2.0), "PP":(5.0,1.0), "RB":(10.0,1.0), "RM":(10.0,1.0),
    "SA":(20.0,1.0), "TA":(5.0,2.0), "Y":(10.0,2.0),
}
WINDOWS = {
    "prior1": ("2022-08-22","2023-08-20"),
    "prior2": ("2023-08-21","2024-08-20"),
    "train": ("2024-08-21","2025-08-20"),
    "validation": ("2025-08-21","2026-02-20"),
    "oos": ("2026-02-21","2026-08-20"),
    "full_recent": ("2024-08-21","2026-08-20"),
}
PROFILE = {
    "lookback":25, "mode":"logratio", "confirm":True, "arm":2.5,
    "confirm_delta":0.3, "min_entry":1.75, "exit_z":0.75, "stop":4.0,
    "maxhold":20, "trend_window":6, "max_z_slope":0.75,
    "min_stationarity":0.01, "max_half_life":60.0,
    "cost_mult":2.0, "cost_ticks":5.0, "min_oi":5000.0, "min_bar_volume":1.0,
}
EXPECTED_PRIOR_PRODUCTS = ("A",)
EXPECTED_CURRENT_PRODUCTS = ("M","OI")


def _contract_key(symbol: str) -> int:
    match = re.search(r"(\d+)$", symbol)
    return int(match.group(1)) if match else 0


def _daily(raw: pd.DataFrame) -> pd.DataFrame:
    frame=raw.copy()
    frame["datetime"]=pd.to_datetime(frame["datetime"], errors="coerce")
    frame=frame.dropna(subset=["datetime","close","volume","hold"])
    frame["date"]=frame["datetime"].dt.normalize()
    return (
        frame.sort_values("datetime")
        .groupby(["product","symbol","date"], as_index=False)
        .agg(close=("close","last"), volume=("volume","sum"), hold=("hold","last"))
    )


def build_pair_frames(prior: pd.DataFrame, current: pd.DataFrame) -> dict[tuple[str,str,str],pd.DataFrame]:
    combined=pd.concat([prior,current],ignore_index=True)
    combined=combined.drop_duplicates(["symbol","datetime"], keep="last")
    daily=_daily(combined)
    result={}
    for product, group in daily.groupby("product"):
        symbols=sorted(group["symbol"].astype(str).unique(), key=_contract_key)
        for near_symbol, far_symbol in zip(symbols, symbols[1:]):
            near=group[group.symbol==near_symbol][["date","close","volume","hold"]].rename(
                columns={"close":"near","volume":"near_vol","hold":"near_hold"})
            far=group[group.symbol==far_symbol][["date","close","volume","hold"]].rename(
                columns={"close":"far","volume":"far_vol","hold":"far_hold"})
            pair=near.merge(far,on="date")
            if pair.empty:
                continue
            pair=pair.rename(columns={"date":"datetime"}).sort_values("datetime").reset_index(drop=True)
            pair["raw"]=pair["near"]-pair["far"]
            pair["logratio"]=np.log(pair["near"]/pair["far"])
            result[(str(product),near_symbol,far_symbol)]=pair
    return result


def rolling_z(frame:pd.DataFrame, lookback:int):
    values=frame["logratio"].to_numpy(float)
    raw=frame["raw"].to_numpy(float)
    series=pd.Series(values)
    mean=series.rolling(lookback,min_periods=lookback).mean().shift(1).to_numpy()
    std=series.rolling(lookback,min_periods=lookback).std(ddof=0).shift(1).to_numpy()
    z=(values-mean)/std
    raw_std=pd.Series(raw).rolling(lookback,min_periods=lookback).std(ddof=0).shift(1).to_numpy()
    return z,raw_std


def rolling_mr_stats(values:np.ndarray,lookback:int):
    n=len(values); half=np.full(n,np.nan); stationarity=np.full(n,np.nan)
    for i in range(lookback,n):
        v=np.asarray(values[i-lookback:i],float)
        levels=v[:-1]; changes=np.diff(v)
        level_mean=levels.mean(); change_mean=changes.mean()
        denominator=((levels-level_mean)**2).sum()
        if denominator<=1e-12:
            half[i]=999.0;stationarity[i]=0.0;continue
        beta=((levels-level_mean)*(changes-change_mean)).sum()/denominator
        if beta>=0:
            half[i]=999.0;stationarity[i]=0.0
        else:
            half[i]=max(0.1,-math.log(2.0)/beta)
            stationarity[i]=min(1.0,max(0.0,-beta))
    return half,stationarity


def generate_trades(frame:pd.DataFrame, product:str, params:dict) -> pd.DataFrame:
    lookback=int(params["lookback"])
    z,raw_std=rolling_z(frame,lookback)
    half,stationarity=rolling_mr_stats(frame["logratio"].to_numpy(float),lookback)
    raw=frame["raw"].to_numpy(float); dts=frame["datetime"].to_numpy()
    near_hold=frame["near_hold"].to_numpy(float); far_hold=frame["far_hold"].to_numpy(float)
    near_vol=frame["near_vol"].to_numpy(float); far_vol=frame["far_vol"].to_numpy(float)
    multiplier,tick=SPECS[product]
    cost_points=float(params["cost_ticks"])*tick*float(params["cost_mult"])
    rows=[]; position=0; entry_index=None; armed=0; armed_extreme=None
    for i in range(lookback,len(frame)):
        zi=z[i]
        if not np.isfinite(zi):
            continue
        liquid=(
            min(near_hold[i],far_hold[i])>=params["min_oi"]
            and min(near_vol[i],far_vol[i])>=params["min_bar_volume"]
        )
        if position==0:
            if not liquid or stationarity[i]<params["min_stationarity"] or half[i]>params["max_half_life"]:
                armed=0;armed_extreme=None;continue
            slope=0.0
            trend_window=int(params["trend_window"])
            if (
                trend_window>1 and i>=trend_window-1
                and np.all(np.isfinite(z[i-trend_window+1:i+1]))
            ):
                slope=float(np.polyfit(np.arange(trend_window),z[i-trend_window+1:i+1],1)[0])
            if armed==0:
                if zi>=params["arm"]:
                    armed=-1;armed_extreme=zi
                elif zi<=-params["arm"]:
                    armed=1;armed_extreme=zi
                continue
            if armed==-1:
                armed_extreme=max(float(armed_extreme),zi)
                reverted=zi<=armed_extreme-params["confirm_delta"] and zi>=params["min_entry"]
            else:
                armed_extreme=min(float(armed_extreme),zi)
                reverted=zi>=armed_extreme+params["confirm_delta"] and zi<=-params["min_entry"]
            if reverted and abs(slope)<=params["max_z_slope"]:
                position=armed;entry_index=i;entry_raw=raw[i];entry_z=zi
                entry_std=raw_std[i]; entry_half=half[i];entry_stationarity=stationarity[i]
                armed=0;armed_extreme=None
            elif (armed==-1 and zi<params["min_entry"]) or (armed==1 and zi>-params["min_entry"]):
                armed=0;armed_extreme=None
            continue

        hold=i-int(entry_index)
        normal=(position==1 and zi>=-params["exit_z"]) or (position==-1 and zi<=params["exit_z"])
        stop=(position==1 and zi<=-params["stop"]) or (position==-1 and zi>=params["stop"])
        timeout=params["maxhold"]>0 and hold>=params["maxhold"]
        last=i==len(frame)-1
        if normal or stop or timeout or last:
            reason="exit" if normal else ("stop" if stop else ("time" if timeout else "end"))
            pnl_points=position*(raw[i]-entry_raw)-cost_points
            pnl_cash=pnl_points*multiplier
            risk_cash=max(entry_std if np.isfinite(entry_std) and entry_std>0 else tick,tick)*multiplier*2.5
            rows.append({
                "entry_time":pd.Timestamp(dts[entry_index]), "exit_time":pd.Timestamp(dts[i]),
                "pnl_cash":float(pnl_cash), "pnl_points":float(pnl_points), "R":float(pnl_cash/risk_cash),
                "entry_z":float(entry_z), "exit_z":float(zi), "hold":int(hold), "reason":reason,
                "entry_std":float(entry_std), "half_life":float(entry_half),
                "stationarity":float(entry_stationarity),
            })
            position=0;entry_index=None
    return pd.DataFrame(rows)


def product_trades(frames,product,start,end,params):
    all_rows=[]
    for (root,near,far),frame in frames.items():
        if root!=product:
            continue
        sub=frame[(frame.datetime>=pd.Timestamp(start))&(frame.datetime<=pd.Timestamp(end))].reset_index(drop=True)
        if len(sub)<params["lookback"]+10:
            continue
        trades=generate_trades(sub,product,params)
        if not trades.empty:
            trades=trades.copy(); trades["near"]=near;trades["far"]=far
            all_rows.append(trades)
    if not all_rows:
        return pd.DataFrame()
    candidates=pd.concat(all_rows,ignore_index=True).sort_values("entry_time")
    accepted=[]; last_exit=pd.Timestamp.min
    for _,row in candidates.iterrows():
        if row.entry_time>last_exit:
            accepted.append(row)
            last_exit=row.exit_time
    return pd.DataFrame(accepted)


def portfolio(frames, roots, window_name, params):
    start,end=WINDOWS[window_name]; rows=[]
    for product in roots:
        trades=product_trades(frames,product,start,end,params)
        if not trades.empty:
            trades=trades.copy();trades["product"]=product;rows.append(trades)
    if not rows:
        return pd.DataFrame(columns=["R","pnl_cash"])
    return pd.concat(rows,ignore_index=True).sort_values(["entry_time","product"])


def metrics(trades):
    if trades is None or trades.empty:
        return {"trades":0,"pnl":0.0,"R":0.0,"win":None,"mdd_R":0.0,"pf":None,"avgR":0.0}
    rs=trades["R"].to_numpy(float)
    curve=np.cumsum(rs)
    peak=np.maximum.accumulate(np.r_[0.0,curve])
    dd=np.r_[0.0,curve]-peak
    gains=rs[rs>0].sum();losses=-rs[rs<0].sum()
    return {
        "trades":int(len(trades)), "pnl":float(trades.pnl_cash.sum()), "R":float(rs.sum()),
        "win":float((rs>0).mean()), "mdd_R":float(dd.min()),
        "pf":float(gains/losses) if losses>0 else None, "avgR":float(rs.mean()),
    }


def qualify(frames,params,left,right):
    result=[]
    for product in PRODUCTS:
        lm=metrics(portfolio(frames,[product],left,params))
        rm=metrics(portfolio(frames,[product],right,params))
        if lm["trades"]>=1 and rm["trades"]>=1 and lm["R"]>0 and rm["R"]>0:
            result.append(product)
    return result


def capital_proxy(trades,risk_fraction):
    equity=1.0;peak=1.0;mdd=0.0
    for value in trades["R"].to_numpy(float) if not trades.empty else []:
        equity*=max(0.0,1.0+risk_fraction*value)
        peak=max(peak,equity);mdd=min(mdd,equity/peak-1.0)
    return {"return":float(equity-1.0),"max_drawdown":float(mdd)}


def neighbor_profiles():
    variants=[]
    for name,values in (
        ("lookback",(20,30)), ("arm",(2.25,2.75)), ("confirm_delta",(0.2,0.4)),
        ("min_entry",(1.5,2.0)), ("exit_z",(0.5,1.0)), ("stop",(3.5,4.5)),
        ("maxhold",(15,25)), ("max_z_slope",(0.5,1.0)),
    ):
        for value in values:
            row=dict(PROFILE);row[name]=value
            variants.append((f"{name}={value}",row))
    return variants


def main():
    runtime=Path("runtime")
    current_path=runtime/"two_year_broad_60m.csv"
    prior_path=runtime/"prior_two_year_broad_60m.csv"
    if not current_path.exists() or not prior_path.exists():
        raise SystemExit("real-data files are missing; run the fetch tools first")
    current=pd.read_csv(current_path)
    prior=pd.read_csv(prior_path)
    frames=build_pair_frames(prior,current)

    coverage={}
    for label,frame in (("prior",prior),("current",current)):
        frame=frame.copy();frame["datetime"]=pd.to_datetime(frame["datetime"])
        coverage[label]={
            str(product):int(group.datetime.dt.normalize().nunique())
            for product,group in frame.groupby("product")
        }

    qualified_prior=qualify(frames,PROFILE,"prior1","prior2")
    qualified_current=qualify(frames,PROFILE,"train","validation")
    prior_forward=portfolio(frames,qualified_prior,"train",PROFILE)
    final_oos=portfolio(frames,qualified_current,"oos",PROFILE)
    full_recent=portfolio(frames,qualified_current,"full_recent",PROFILE)

    neighbors=[]
    for label,params in neighbor_profiles():
        old_products=qualify(frames,params,"prior1","prior2")
        new_products=qualify(frames,params,"train","validation")
        old_forward=metrics(portfolio(frames,old_products,"train",params))
        new_forward=metrics(portfolio(frames,new_products,"oos",params))
        passed=bool(
            old_products and new_products
            and old_forward["trades"]>=2 and new_forward["trades"]>=2
            and old_forward["R"]>0 and new_forward["R"]>0
        )
        neighbors.append({
            "variant":label,"prior_products":old_products,"current_products":new_products,
            "prior_forward":old_forward,"oos":new_forward,"passed":passed,
        })
    pass_count=sum(item["passed"] for item in neighbors)

    report={
        "source":"AKShare Sina specific-contract 60-minute bars",
        "sample_reference":"calendar-day final 60-minute bar, normally 23:00 China time; live profile samples 22:55-23:00",
        "windows":WINDOWS,
        "profile":PROFILE,
        "cost_stress":"2x conservative round-trip ticks",
        "coverage_trading_days":coverage,
        "qualified_prior":qualified_prior,
        "qualified_current":qualified_current,
        "prior_forward":metrics(prior_forward),
        "final_oos":metrics(final_oos),
        "full_recent":metrics(full_recent),
        "capital_proxy":{
            "1pct_risk":capital_proxy(full_recent,0.01),
            "1_5pct_risk":capital_proxy(full_recent,0.015),
            "2pct_risk":capital_proxy(full_recent,0.02),
        },
        "neighbor_pass_count":pass_count,
        "neighbor_total":len(neighbors),
        "neighbor_pass_ratio":pass_count/len(neighbors),
        "neighbors":neighbors,
    }

    # Hard evidence gates. Product eligibility is frozen before the following
    # forward window; OOS is never used to select a product or parameter.
    assert all(coverage["current"].get(p)==484 for p in PRODUCTS)
    assert all(coverage["prior"].get(p)>=484 for p in PRODUCTS)
    assert tuple(qualified_prior)==EXPECTED_PRIOR_PRODUCTS, qualified_prior
    assert tuple(qualified_current)==EXPECTED_CURRENT_PRODUCTS, qualified_current
    assert report["prior_forward"]["trades"]>=2 and report["prior_forward"]["R"]>0
    assert report["final_oos"]["trades"]>=3 and report["final_oos"]["R"]>0
    assert report["final_oos"]["mdd_R"]>-0.5
    assert report["full_recent"]["trades"]>=10 and report["full_recent"]["R"]>2.0
    assert report["full_recent"]["mdd_R"]>-0.5
    assert report["neighbor_pass_ratio"]>=0.5

    (runtime/"two_year_strategy_report.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps({
        "qualified_prior":qualified_prior,
        "qualified_current":qualified_current,
        "prior_forward":report["prior_forward"],
        "final_oos":report["final_oos"],
        "full_recent":report["full_recent"],
        "neighbor_pass_ratio":report["neighbor_pass_ratio"],
        "capital_proxy":report["capital_proxy"],
    },ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
