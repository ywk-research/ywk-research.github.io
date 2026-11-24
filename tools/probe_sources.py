"""데이터 소스 점검용(일회성). 키는 출력하지 않는다."""
import json, os, urllib.request, urllib.parse, time
key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
print("TD key set:", bool(key))
def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return None, repr(e)
cands = ["SPX", "IXIC", "DJI", "NDX", "SOX", "VIX", "N225", "NI225", "HSI", "SSEC", "000001", "SHCOMP", "DXY", "USDX",
         "XAU/USD", "WTI/USD", "CL1", "USD/KRW", "US10Y", "TNX", "KS11", "SPY", "QQQ", "SOXX"]
for s in cands:
    q = urllib.parse.urlencode({"symbol": s, "interval": "1day", "outputsize": 3})
    st, body = get("https://api.twelvedata.com/time_series?" + q, {"Authorization": "apikey " + key})
    try:
        j = json.loads(body)
    except Exception:
        j = {"raw": body[:120]}
    if j.get("status") == "ok":
        m = j.get("meta", {}); v = j["values"][0]
        print(f"OK   {s:9} {m.get('exchange')} {m.get('type')} {m.get('symbol')} | {v['datetime']} close={v['close']}")
    else:
        print(f"FAIL {s:9} {str(j.get('code'))} {str(j.get('message'))[:110]}")
    time.sleep(8)  # 무료 플랜 분당 8회 제한
for s in ["S&P 500", "Nikkei", "Hang Seng", "Shanghai", "Semiconductor", "Dollar Index"]:
    st, body = get("https://api.twelvedata.com/symbol_search?" + urllib.parse.urlencode({"symbol": s, "outputsize": 6}))
    try:
        print("SEARCH", s, [(d["symbol"], d.get("exchange"), d.get("instrument_type")) for d in json.loads(body).get("data", [])])
    except Exception:
        print("SEARCH", s, st, body[:100])
st, body = get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv")
print("CBOE", st, body.strip().splitlines()[-1] if st == 200 else body[:120])
st, body = get("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv")
print("TREASURY", st, " | ".join(body.strip().splitlines()[:2]) if st == 200 else body[:120])
