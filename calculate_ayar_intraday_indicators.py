import json
import csv
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

TEHRAN=ZoneInfo("Asia/Tehran")
DATA=Path("data")
CACHE=DATA/"intraday"
OUT=DATA/"ayar_intraday_indicators.json"

def ema(v,p):
    if len(v)<p: return [None]*len(v)
    a=2/(p+1); out=[None]*(p-1); x=sum(v[:p])/p; out.append(x)
    for n in v[p:]:
        x=(n-x)*a+x; out.append(x)
    return out

def rsi(v,p=14):
    if len(v)<=p: return [None]*len(v)
    d=[v[i]-v[i-1] for i in range(1,len(v))]
    g=[max(x,0) for x in d]; l=[max(-x,0) for x in d]
    ag=sum(g[:p])/p; al=sum(l[:p])/p
    def val(a,b): return (100.0 if a>0 else 50.0) if b==0 else 100-100/(1+a/b)
    out=[None]*p+[val(ag,al)]
    for i in range(p,len(g)):
        ag=(ag*(p-1)+g[i])/p; al=(al*(p-1)+l[i])/p; out.append(val(ag,al))
    return out

def sma(v,p):
    return [None]*(p-1)+[sum(v[i-p+1:i+1])/p for i in range(p-1,len(v))]

def build(cs,tf):
    cs=sorted(cs,key=lambda x:(x["date"],x["start"]))
    if not cs: raise ValueError(f"No candles for {tf}")
    dates=[x["date"]+" "+x["start"] for x in cs]
    c=[float(x["close"]) for x in cs]; vol=[int(x["volume"]) for x in cs]
    e20,e50,e200=ema(c,20),ema(c,50),ema(c,200); r=rsi(c)
    e12,e26=ema(c,12),ema(c,26)
    m=[None if a is None or b is None else a-b for a,b in zip(e12,e26)]
    vm=[x for x in m if x is not None]; sv=ema(vm,9)
    sig=[None]*(len(m)-len(sv))+sv
    hist=[None if a is None or b is None else a-b for a,b in zip(m,sig)]
    vs=sma(vol,20); vr=[None if a in (None,0) else b/a for a,b in zip(vs,vol)]
    def rnd(a,d=2): return [None if x is None else round(x,d) for x in a]
    def last(a,d=2): return None if a[-1] is None else round(a[-1],d)
    latest={"datetime":dates[-1],"open":cs[-1]["open"],"high":cs[-1]["high"],
      "low":cs[-1]["low"],"close":c[-1],"volume":vol[-1],"trades":cs[-1]["trades"],
      "partial":cs[-1].get("partial",False),"EMA20":last(e20),"EMA50":last(e50),
      "EMA200":last(e200),"EMA200_status":"ok" if e200[-1] is not None else "insufficient_history",
      "RSI14":last(r),"MACD":last(m),"MACD_signal":last(sig),"MACD_histogram":last(hist),
      "Volume_SMA20":last(vs),"Volume_Ratio":last(vr,3)}
    return {"candle_count":len(cs),"first_candle":dates[0],"last_candle":dates[-1],
      "latest":latest,"candles":cs,"indicators":{"EMA20":rnd(e20),"EMA50":rnd(e50),
      "EMA200":rnd(e200),"RSI14":rnd(r),"MACD":rnd(m),"MACD_signal":rnd(sig),
      "MACD_histogram":rnd(hist),"Volume":vol,"Volume_SMA20":rnd(vs),"Volume_Ratio":rnd(vr,3)}}

def read_candle_csv(path, timeframe):
    candles=[]
    with path.open(encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            start=row["time"][:5]
            hour=int(start[:2])
            # Keep regular-session one-hour bars; closing-auction prints at 18:00
            # remain in the raw CSV but are not mixed into continuous-session indicators.
            if timeframe=="1h" and hour==18:
                continue
            # The 16:00-18:00 remainder is a partial two-hour bar, not a full 4h bar.
            if timeframe=="4h" and hour==16:
                continue
            candles.append({
                "date":row["date"],"start":start,
                "open":int(row["open"]),"high":int(row["high"]),
                "low":int(row["low"]),"close":int(row["close"]),
                "volume":int(row["volume"]),"trades":int(row["trade_count"]),
                "partial":False
            })
    return candles


def main():
    if not CACHE.exists():
        raise FileNotFoundError("Intraday cache missing")

    h1_csv=CACHE/"ayar_1h.csv"
    h4_csv=CACHE/"ayar_4h.csv"
    report_path=CACHE/"validation_report.json"
    historical_report={}
    source_mode="daily_json_cache"

    if h1_csv.exists() and h4_csv.exists() and report_path.exists():
        historical_report=json.loads(report_path.read_text(encoding="utf-8"))
        if historical_report.get("candle_validation",{}).get("passed") is not True:
            raise ValueError("Historical 1h/4h CSV candle validation did not pass")
        h1=read_candle_csv(h1_csv,"1h")
        h4=read_candle_csv(h4_csv,"4h")
        source_mode="full_history_validated_csv"
        files=sorted(CACHE.glob("ayar_*.json"))
    else:
        h1=[]; h4=[]; files=sorted(CACHE.glob("ayar_*.json"))
        for p in files:
            d=json.loads(p.read_text(encoding="utf-8"))
            if d.get("validated") is not True:
                raise ValueError(f"Unvalidated cache: {p.name}")
            h1.extend([x for x in d.get("candles_1h",[]) if not x.get("partial",False)])
            h4.extend([x for x in d.get("candles_4h",[]) if
                int(x.get("end","00:00")[:2])-int(x.get("start","00:00")[:2])==4])

    live_path=DATA/"ayar_intraday_live.json"
    meta={"status":"not_available"}
    if live_path.exists():
        live=json.loads(live_path.read_text(encoding="utf-8"))
        meta={k:live.get(k) for k in ("status","date","retrieved_at","trade_count",
          "quote_trade_count","quote_retrieved_at","observed_trade_count_delta",
          "count_reconciliation_status","quality_note_fa","error")}
        if live.get("status")=="ok":
            def merge(base,new):
                d={(x["date"],x["start"]):x for x in base}
                for x in new:
                    d[(x["date"],x["start"])]=x
                return sorted(d.values(),key=lambda x:(x["date"],x["start"]))
            h1=merge(h1,[x for x in live.get("candles_1h",[]) if not x.get("partial",False)])
            h4=merge(h4,[x for x in live.get("candles_4h",[]) if
                int(x.get("end","00:00")[:2])-int(x.get("start","00:00")[:2])==4 and not x.get("partial",False)])

    coverage=historical_report.get("timeframe_coverage",{})
    validation={
        "source_mode":source_mode,
        "historical_candle_validation_passed":historical_report.get("candle_validation",{}).get("passed"),
        "expected_trading_days":historical_report.get("expected_trading_days"),
        "one_hour_validated_days":coverage.get("1h",{}).get("validated_days"),
        "one_hour_missing_days":coverage.get("1h",{}).get("missing_days"),
        "four_hour_validated_days":coverage.get("4h",{}).get("validated_days"),
        "four_hour_missing_days":coverage.get("4h",{}).get("missing_days"),
        "historical_1h_candles":len(h1),
        "historical_complete_4h_candles":len(h4),
        "minimum_candles_for_EMA200":200,
        "live_intraday_overlay":meta,
        "one_hour_candle_policy":"Regular-session candles only; exclude the separate 18:00 closing-auction bar.",
        "four_hour_candle_policy":"Complete 08:00-12:00 and 12:00-16:00 bars only; exclude the partial 16:00-18:00 remainder."
    }
    result={"symbol":"عیار","source":"TSETMC raw trades","timezone":"Asia/Tehran",
      "calculated_at":datetime.now(TEHRAN).isoformat(),
      "method":"EMA seeded by SMA; RSI Wilder; MACD 12-26-9; volume SMA20",
      "validation":validation,
      "timeframes":{"1h":build(h1,"1h"),"4h":build(h4,"4h")}}
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(OUT)
    print(json.dumps({k:{"count":v["candle_count"],"latest":v["latest"]} for k,v in result["timeframes"].items()},ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
