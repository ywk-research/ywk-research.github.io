"""해외 지수·금리·원자재·환율 일별 값을 받아 _data/global_index.json에 저장한다.

소스별로 가장 빠르고 공식적인 곳을 쓴다.
- Cboe 공식 CSV (키 불필요): VIX
- 미 재무부 공식 CSV (키 불필요): 10년물 금리
- FRED (FRED_API_KEY): S&P500, 나스닥, 다우, 닛케이225, WTI — 지표에 따라 1~수일 늦게 올라온다
- 한국은행 ECOS (ECOS_API_KEY): 원/달러 매매기준율(731Y001)
키는 출력하지 않는다. 한 소스가 실패해도 나머지는 계속 받고, 실패한 지표는 이전 값을 유지한다.
"""
import csv
import datetime as dt
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, "_data", "global_index.json")
KST = dt.timezone(dt.timedelta(hours=9))
DAYS = int(os.environ.get("HISTORY_DAYS", "400"))
UA = {"User-Agent": "Mozilla/5.0 (ywk-research.github.io data job)"}

# (키, 표시 이름, 단위, 소스, 소스 식별자, 출처 표기)   단위: pt 지수, pct 금리, usd 달러, krw 원
SERIES = [
    ("vix", "VIX", "pt", "cboe", "VIX", "Cboe"),
    ("us10y", "美 10년물", "pct", "treasury", "10 Yr", "미 재무부"),
    ("usdkrw", "원/달러", "krw", "ecos", "731Y001/0000001", "한국은행 ECOS"),
]


def get(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8-sig")
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def mdy(s):
    m, d, y = s.split("/")
    return dt.date(int(y), int(m), int(d))


def from_fred(sid, start):
    key = os.environ["FRED_API_KEY"].strip()
    q = urllib.parse.urlencode({"series_id": sid, "api_key": key, "file_type": "json",
                                "observation_start": start.isoformat()})
    obs = json.loads(get("https://api.stlouisfed.org/fred/series/observations?" + q))["observations"]
    return [(dt.date.fromisoformat(o["date"]), float(o["value"])) for o in obs if o["value"] not in (".", "")]


def from_cboe(sym, start):
    text = get(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv")
    rows = [(mdy(r["DATE"]), float(r["CLOSE"])) for r in csv.DictReader(io.StringIO(text)) if r.get("CLOSE")]
    return [r for r in rows if r[0] >= start]


def from_treasury(col, start):
    out = []
    for year in range(start.year, dt.date.today().year + 1):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
               f"{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
        for r in csv.DictReader(io.StringIO(get(url))):
            if r.get(col):
                out.append((mdy(r["Date"]), float(r[col])))
        time.sleep(0.5)
    return sorted(r for r in out if r[0] >= start)


def from_ecos(sid, start):
    key = os.environ["ECOS_API_KEY"].strip()
    table, item = sid.split("/")
    end = dt.datetime.now(KST).date()
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/{table}/D/"
           f"{start:%Y%m%d}/{end:%Y%m%d}/{item}")
    try:
        j = json.loads(get(url))
    except Exception as e:  # 오류 메시지에 키가 들어간 URL이 섞이지 않게
        raise RuntimeError(type(e).__name__) from None
    if "StatisticSearch" not in j:
        r = j.get("RESULT", {})
        raise RuntimeError(f"{r.get('CODE')} {r.get('MESSAGE')}")
    rows = [(dt.datetime.strptime(r["TIME"], "%Y%m%d").date(), float(r["DATA_VALUE"]))
            for r in j["StatisticSearch"]["row"] if r.get("DATA_VALUE") not in (None, "", "-")]
    return sorted(r for r in rows if r[0] >= start)


SOURCES = {"fred": (from_fred, "FRED_API_KEY"), "cboe": (from_cboe, None),
           "treasury": (from_treasury, None), "ecos": (from_ecos, "ECOS_API_KEY")}


def main():
    start = dt.datetime.now(KST).date() - dt.timedelta(days=DAYS)
    old = {}
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, encoding="utf-8") as f:
            old = {s["key"]: s for s in json.load(f).get("series", [])}

    series = []
    for name, label, unit, src, sid, credit in SERIES:
        fn, env = SOURCES[src]
        try:
            if env and not os.environ.get(env, "").strip():
                raise RuntimeError(f"{env} 없음")
            obs = fn(sid, start)
            rows = []
            for i, (d, v) in enumerate(obs):
                prev = obs[i - 1][1] if i else None
                rows.append({"d": d.isoformat(), "close": v,
                             "chg": round(v - prev, 4) if prev is not None else None,
                             "pct": round((v / prev - 1) * 100, 2) if prev else None})
            entry = {"key": name, "label": label, "unit": unit, "src": credit, "data": rows}
            print(f"{label} ({credit} {sid}): {len(rows)}일, 최근 {rows[-1]['d'] if rows else '-'}")
        except Exception as e:  # 소스 하나가 실패해도 나머지는 계속
            entry = old.get(name)
            if entry and entry.get("src") != credit:  # 출처가 바뀐 지표는 예전 값을 쓰지 않는다
                entry = None
            print(f"실패 — {label} ({credit} {sid}): {str(e)[:120]}; 이전 값 유지" if entry else
                  f"실패 — {label} ({credit} {sid}): {str(e)[:120]}")
        if entry and entry.get("data"):
            series.append(entry)

    if [s for s in series] == list(old.values()):
        print("변경 없음")
        return
    credits = sorted({s["src"] for s in series})
    out = {"source": ", ".join(credits), "series": series,
           "updated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")}
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
