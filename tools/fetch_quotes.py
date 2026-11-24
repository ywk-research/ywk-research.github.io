"""개장 전 브리핑용 전일 종가(지수·금리·환율·원자재·주요 종목)를 Yahoo Finance 차트 API에서 받아
_data/briefing_quotes.json에 저장한다. 키 없음.

- 사용자 결정(2026-11-25): 지수·주요 종목 종가·등락률은 Yahoo Finance에서 가져온다(차트·시세 화면은 쓰지 않음).
  Yahoo에는 공식 API가 없어 접속이 막히는 날이 있다. 그때는 이 파일이 갱신되지 않고,
  브리핑 예약 작업이 통신사 마감 기사로 채운다(tools/briefing_task.md).
- 날짜는 각 거래소 현지 날짜. 마지막 봉이 장중이면(거래 중) 버린다.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_data", "briefing_quotes.json")
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# (키, 심볼, 이름)
INDEX = [
    ("spx", "^GSPC", "S&P500"), ("ndx", "^IXIC", "나스닥"), ("dji", "^DJI", "다우"),
    ("sox", "^SOX", "필라델피아 반도체(SOX)"), ("vix", "^VIX", "VIX"),
    ("kospi", "^KS11", "코스피"), ("n225", "^N225", "닛케이225"), ("sse", "000001.SS", "상하이종합"), ("hsi", "^HSI", "항셍"),
    ("tnx", "^TNX", "미국 10년물 국채금리"), ("dxy", "DX-Y.NYB", "달러인덱스"),
    ("wti", "CL=F", "WTI"), ("gold", "GC=F", "금"),
]
# 주요 종목 후보(미국 대형주·반도체, 한국 ADR). 브리핑은 이 중 많이 움직인 것을 고른다
STOCKS = [
    ("NVDA", "엔비디아"), ("AAPL", "애플"), ("MSFT", "마이크로소프트"), ("GOOGL", "알파벳"), ("AMZN", "아마존"),
    ("META", "메타"), ("TSLA", "테슬라"), ("AVGO", "브로드컴"), ("TSM", "TSMC ADR"), ("AMD", "AMD"),
    ("MU", "마이크론"), ("INTC", "인텔"), ("QCOM", "퀄컴"), ("ARM", "Arm"), ("ORCL", "오라클"), ("PLTR", "팔란티어"),
    ("NFLX", "넷플릭스"), ("JPM", "JP모건"), ("LLY", "일라이릴리"), ("XOM", "엑슨모빌"),
    ("ANT", "앤트로픽"), ("COIN", "코인베이스"), ("MSTR", "스트래티지"),
]


def chart(symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol)
           + "?range=10d&interval=1d")
    last = None
    for host in ("query1", "query2"):
        try:
            req = urllib.request.Request(url.replace("query1", host), headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)["chart"]["result"][0]
        except Exception as e:
            last = e
            time.sleep(1)
    raise RuntimeError(f"{symbol}: {type(last).__name__}")


def bars(res):
    """[(현지 날짜, 종가)] — 거래 중인 마지막 봉은 뺀다"""
    meta = res["meta"]
    off = meta.get("gmtoffset", 0)
    ts = res.get("timestamp") or []
    cl = res["indicators"]["quote"][0].get("close") or []
    out = [(dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat(), c)
           for t, c in zip(ts, cl) if c is not None]
    tp = meta.get("currentTradingPeriod", {}).get("regular", {})
    now = time.time()
    if out and tp and tp.get("start", 0) <= now < tp.get("end", 0):
        today = dt.datetime.fromtimestamp(now + off, dt.timezone.utc).date().isoformat()
        if out[-1][0] == today:
            out = out[:-1]
    # 같은 날짜가 두 번 나오면 마지막 것
    seen = {}
    for d, c in out:
        seen[d] = c
    return sorted(seen.items())


def one(key, symbol, name):
    b = bars(chart(symbol))
    if len(b) < 2:
        raise RuntimeError(f"{symbol}: 자료 부족")
    (pd_, pv), (d, v) = b[-2], b[-1]
    row = {"symbol": symbol, "name": name, "d": d, "close": round(v, 4), "prev": round(pv, 4), "prev_d": pd_,
           "chg": round(v - pv, 4), "pct": round((v / pv - 1) * 100, 2)}
    return key, row


def main():
    out = {"source": "Yahoo Finance", "updated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
           "index": {}, "stocks": {}}
    fails = 0
    for key, sym, name in INDEX:
        try:
            k, row = one(key, sym, name)
            out["index"][k] = row
        except Exception as e:
            fails += 1
            print(f"  실패 — {sym}: {str(e)[:80]}")
        time.sleep(0.3)
    for sym, name in STOCKS:
        try:
            k, row = one(sym, sym, name)
            out["stocks"][k] = row
        except Exception as e:
            fails += 1
            print(f"  실패 — {sym}: {str(e)[:80]}")
        time.sleep(0.3)
    total = len(INDEX) + len(STOCKS)
    print(f"Yahoo 종가: {total - fails}/{total}")
    for k, r in out["index"].items():
        print(f"  {k:6} {r['d']} {r['close']:>12,.2f} {r['pct']:+.2f}%")
    if not out["index"]:
        print("하나도 못 받음 — 파일을 바꾸지 않음")
        return 1
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
