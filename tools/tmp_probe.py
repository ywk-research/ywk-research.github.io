import os, json, re, urllib.request, urllib.parse, urllib.error
UA={"User-Agent":"Mozilla/5.0 (ywk-research.github.io data job)"}
def get(url, h=None):
    r=urllib.request.Request(url, headers={**UA, **(h or {})})
    with urllib.request.urlopen(r, timeout=40) as f: return f.read()
key=os.environ.get("KRX_API_KEY","").strip()
for ep in ["gen/gold_bydd_trd","gen/oil_bydd_trd"]:
    try:
        b=get("https://data-dbg.krx.co.kr/svc/apis/"+ep+"?basDd=20261123",{"AUTH_KEY":key})
        d=json.loads(b); rows=d.get("OutBlock_1") or []
        print(ep, len(rows)); [print(r) for r in rows[:5]]
    except urllib.error.HTTPError as e: print(ep,"HTTP",e.code, e.read()[:200])
fk=os.environ.get("FRED_API_KEY","").strip()
for s in ["DCOILWTICO","DCOILBRENTEU","DHHNGSP"]:
    try:
        q=urllib.parse.urlencode({"series_id":s,"api_key":fk,"file_type":"json","observation_start":"2026-11-01"})
        o=json.loads(get("https://api.stlouisfed.org/fred/series/observations?"+q))["observations"]
        print(s,[ (x["date"],x["value"]) for x in o[-3:]])
    except Exception as e: print(s,"ERR",e)
try:
    print("EIA nokey", get("https://api.eia.gov/v2/petroleum/pri/spt/data/?frequency=daily&data[0]=value&facets[series][]=RWTC&length=2")[:200])
except urllib.error.HTTPError as e: print("EIA nokey HTTP", e.code, e.read()[:200])
try:
    html=get("https://www.worldbank.org/en/research/commodity-markets").decode("utf-8","ignore")
    print("WB links", sorted(set(re.findall(r'https?://[^"\']+CMO-Historical-Data-Monthly\.xlsx', html)))[:3])
    print("WB pdf", sorted(set(re.findall(r'https?://[^"\']+\.xlsx', html)))[:8])
except Exception as e: print("WB ERR", e)
