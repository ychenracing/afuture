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
from datetime import date
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
    "cost_mult":2.0, "cost_ticks":5.0, "min_oi":5000.0, "min_bar_volume":1000.0,
}
EXPECTED_PRIOR_PRODUCTS = ("A",)
EXPECTED_CURRENT_PRODUCTS = ("M","OI")
SAMPLE_START_MINUTE = 22 * 60 + 55
SAMPLE_END_MINUTE = 23 * 60
DAY_SESSION_START_MINUTE = 8 * 60
DAY_SESSION_END_MINUTE = 20 * 60
NIGHT_SESSION_START_MINUTE = 20 * 60
MIN_DAYS_TO_EXPIRY = 20
MAX_CONTRACTS_PER_PRODUCT = 3
# DCE/CZCE key-month contracts usually cease trading around mid delivery month.
# The public 60m feed does not expose official expiry metadata, so research uses a
# conservative fixed 15th calendar-day proxy and documents the approximation.
DELIVERY_DAY_PROXY = 15


def _contract_key(symbol: str) -> int:
    match = re.search(r"(\d+)$", symbol)
    return int(match.group(1)) if match else 0


def _delivery_date(symbol: str) -> pd.Timestamp | None:
    match = re.search(r"(\d{4})$", str(symbol))
    if not match:
        return None
    digits = match.group(1)
    year = 2000 + int(digits[:2])
    month = int(digits[2:])
    try:
        return pd.Timestamp(date(year, month, DELIVERY_DAY_PROXY))
    except ValueError:
        return None


def _prepare_intraday(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize 60-minute rows and map calendar timestamps to futures trading days."""
    frame=raw.copy()
    frame["datetime"]=pd.to_datetime(frame["datetime"], errors="coerce")
    for column in ("close","volume","hold"):
        frame[column]=pd.to_numeric(frame[column],errors="coerce")
    frame=frame.dropna(subset=["datetime","close","volume","hold","symbol","product"])
    frame=frame.drop_duplicates(["symbol","datetime"],keep="last")
    frame=frame.sort_values(["datetime","symbol"]).reset_index(drop=True)
    if frame.empty:
        frame["calendar_date"]=pd.Series(dtype="datetime64[ns]")
        frame["trading_day"]=pd.Series(dtype="datetime64[ns]")
        frame["visible_volume"]=pd.Series(dtype=float)
        return frame

    frame["calendar_date"]=frame["datetime"].dt.normalize()
    minute=frame["datetime"].dt.hour*60+frame["datetime"].dt.minute
    day_mask=(minute>=DAY_SESSION_START_MINUTE)&(minute<DAY_SESSION_END_MINUTE)
    day_session_dates=pd.DatetimeIndex(
        sorted(pd.to_datetime(frame.loc[day_mask,"calendar_date"].dropna().unique()))
    )

    trading_days=[]
    for timestamp,natural_day in zip(frame["datetime"],frame["calendar_date"]):
        current_minute=timestamp.hour*60+timestamp.minute
        if current_minute>=NIGHT_SESSION_START_MINUTE:
            index=day_session_dates.searchsorted(natural_day,side="right")
            trading_days.append(
                day_session_dates[index] if index<len(day_session_dates) else pd.NaT
            )
        else:
            trading_days.append(natural_day)
    frame["trading_day"]=pd.to_datetime(trading_days)
    frame=frame.dropna(subset=["trading_day"]).sort_values(["datetime","symbol"]).reset_index(drop=True)
    frame["visible_volume"]=(
        frame.groupby(["product","symbol","trading_day"],sort=False)["volume"].cumsum()
    )
    return frame


def build_pair_frames(prior: pd.DataFrame, current: pd.DataFrame) -> dict[tuple[str,str,str],pd.DataFrame]:
    """Build point-in-time front-three pair observations matching production Auto.

    Each futures trading day first removes contracts inside the 20-day delivery
    blackout, then keeps only the front three eligible contracts and creates adjacent
    pairs. Both legs must have the same exact 22:55-23:00 60-minute timestamp.
    """
    combined=_prepare_intraday(pd.concat([prior,current],ignore_index=True))
    if combined.empty:
        return {}
    minute=combined["datetime"].dt.hour*60+combined["datetime"].dt.minute
    sample=combined[(minute>=SAMPLE_START_MINUTE)&(minute<=SAMPLE_END_MINUTE)].copy()
    if sample.empty:
        return {}
    sample=(
        sample.sort_values("datetime")
        .groupby(["product","symbol","trading_day"],as_index=False)
        .tail(1)
    )

    rows_by_pair: dict[tuple[str,str,str], list[dict]] = {}
    for (product,trading_day), day_rows in sample.groupby(["product","trading_day"]):
        day_ts=pd.Timestamp(trading_day)
        eligible=[]
        for _, row in day_rows.iterrows():
            delivery=_delivery_date(str(row["symbol"]))
            if delivery is None or (delivery-day_ts).days < MIN_DAYS_TO_EXPIRY:
                continue
            eligible.append((delivery,str(row["symbol"]),row))
        eligible.sort(key=lambda item:(item[0],_contract_key(item[1]),item[1]))
        eligible=eligible[:MAX_CONTRACTS_PER_PRODUCT]
        for (_,near_symbol,near_row),(_,far_symbol,far_row) in zip(eligible,eligible[1:]):
            near_time=pd.Timestamp(near_row["datetime"])
            far_time=pd.Timestamp(far_row["datetime"])
            if near_time != far_time:
                continue
            key=(str(product),near_symbol,far_symbol)
            rows_by_pair.setdefault(key,[]).append({
                "trading_day":day_ts,
                "sample_timestamp":near_time,
                "near":float(near_row["close"]),
                "near_vol":float(near_row["visible_volume"]),
                "near_hold":float(near_row["hold"]),
                "far":float(far_row["close"]),
                "far_vol":float(far_row["visible_volume"]),
                "far_hold":float(far_row["hold"]),
            })

    result={}
    for key, rows in rows_by_pair.items():
        pair=pd.DataFrame(rows).sort_values("sample_timestamp").reset_index(drop=True)
        pair["datetime"]=pair["trading_day"]
        pair["raw"]=pair["near"]-pair["far"]
        pair["logratio"]=np.log(pair["near"]/pair["far"])
        result[key]=pair
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
            if trend_window>1 and i>=trend_window-1 and np.all(np.isfinite(z[i-trend_window+1:i+1])):
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
        prepared=_prepare_intraday(frame)
        coverage[label]={
            str(product):int(group.trading_day.nunique())
            for product,group in prepared.groupby("product")
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
        "sample_reference":"last exact common 60-minute timestamp within 22:55-23:00 China-time production window; no day-session fallback",
        "trading_day_mapping":"night bars >=20:00 map to the next observed day-session date; unmatched tail nights are dropped fail-closed",
        "pair_alignment":"near/far price and open interest share the same 60-minute timestamp; volume is cumulative only through that sample within the mapped futures trading day",
        "universe_alignment":"per futures trading day: 20-day delivery blackout using delivery-month 15th proxy, then front three contracts and adjacent pairs only",
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
