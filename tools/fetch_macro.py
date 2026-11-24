"""데이터 탭 '매크로' 섹션용 금리 데이터를 받아 _data/macro.json에 저장한다.

- 미 재무부 공식 일별 수익률 곡선 CSV (키 불필요): 1개월~30년 만기
- 한국은행 ECOS (ECOS_API_KEY): 국고채 1~30년(817Y002), 콜·CD·CP(817Y002), 기준금리(722Y001)
- FRED (FRED_API_KEY): 연준 정책금리 목표 상단(DFEDTARU) — 한미 기준금리차 계산용

키는 출력하지 않는다. 한 소스가 실패하면 그 부분만 이전 값을 유지한다.
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
DATA_PATH = os.path.join(ROOT, "_data", "macro.json")
KST = dt.timezone(dt.timedelta(hours=9))
DAYS = int(os.environ.get("HISTORY_DAYS", "400"))
UA = {"User-Agent": "Mozilla/5.0 (ywk-research.github.io data job)"}

# 미 재무부 CSV 열 이름 → 표시 이름
US_TENORS = [("1 Mo", "1M"), ("3 Mo", "3M"), ("6 Mo", "6M"), ("1 Yr", "1Y"), ("2 Yr", "2Y"), ("3 Yr", "3Y"),
             ("5 Yr", "5Y"), ("7 Yr", "7Y"), ("10 Yr", "10Y"), ("20 Yr", "20Y"), ("30 Yr", "30Y")]
# ECOS 817Y002 시장금리(일별) 항목 코드
KR_TENORS = [("010190000", "1Y"), ("010195000", "2Y"), ("010200000", "3Y"), ("010200001", "5Y"),
             ("010210000", "10Y"), ("010220000", "20Y"), ("010230000", "30Y")]
KR_SHORT = [("call", "817Y002", "010101000"), ("cd", "817Y002", "010502000"),
            ("cp", "817Y002", "010503000"), ("base", "722Y001", "0101000")]


def get(url):
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8-sig")
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def us_curve(start):
    rows = {}
    for year in range(start.year, dt.date.today().year + 1):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
               f"{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
        for r in csv.DictReader(io.StringIO(get(url))):
            m, d, y = r["Date"].split("/")
            day = dt.date(int(y), int(m), int(d))
            if day >= start:
                rows[day.isoformat()] = [num(r.get(col)) for col, _ in US_TENORS]
        time.sleep(0.5)
    return [[d, rows[d]] for d in sorted(rows)]


def ecos(table, item, start):
    key = os.environ["ECOS_API_KEY"].strip()
    end = dt.datetime.now(KST).date()
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/{table}/D/"
           f"{start:%Y%m%d}/{end:%Y%m%d}/{item}")
    try:
        j = json.loads(get(url))
    except Exception as e:  # 오류 메시지에 키가 들어간 URL이 섞이지 않게
        raise RuntimeError(type(e).__name__) from None
    if "StatisticSearch" not in j:
        r = j.get("RESULT", {})
        raise RuntimeError(f"{table}/{item}: {r.get('CODE')} {r.get('MESSAGE')}")
    out = {}
    for r in j["StatisticSearch"]["row"]:
        v = num(r.get("DATA_VALUE"))
        if v is not None:
            out[dt.datetime.strptime(r["TIME"], "%Y%m%d").date().isoformat()] = v
    return out


def kr_curve(start):
    cols = [ecos("817Y002", code, start) for code, _ in KR_TENORS]
    days = sorted(set().union(*cols))
    return [[d, [c.get(d) for c in cols]] for d in days]


def kr_short(start):
    return {name: sorted([d, v] for d, v in ecos(t, code, start).items()) for name, t, code in KR_SHORT}


def fed_upper(start):
    key = os.environ["FRED_API_KEY"].strip()
    q = urllib.parse.urlencode({"series_id": "DFEDTARU", "api_key": key, "file_type": "json",
                                "observation_start": start.isoformat()})
    try:
        obs = json.loads(get("https://api.stlouisfed.org/fred/series/observations?" + q))["observations"]
    except Exception as e:
        raise RuntimeError(type(e).__name__) from None
    return [[o["date"], float(o["value"])] for o in obs if o["value"] not in (".", "")]


PARTS = [
    ("us", "미 재무부", None, us_curve),
    ("kr", "한국은행 ECOS", "ECOS_API_KEY", kr_curve),
    ("kr_short", "한국은행 ECOS", "ECOS_API_KEY", kr_short),
    ("fed_upper", "FRED(연준)", "FRED_API_KEY", fed_upper),
]


def main():
    start = dt.datetime.now(KST).date() - dt.timedelta(days=DAYS)
    old = {}
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, encoding="utf-8") as f:
            old = json.load(f)

    out = {"us_tenors": [t for _, t in US_TENORS], "kr_tenors": [t for _, t in KR_TENORS]}
    for key, credit, env, fn in PARTS:
        try:
            if env and not os.environ.get(env, "").strip():
                raise RuntimeError(f"{env} 없음")
            val = fn(start)
            if not val:
                raise RuntimeError("빈 응답")
            out[key] = val
            last = val[-1][0] if isinstance(val, list) else {k: v[-1][0] for k, v in val.items() if v}
            print(f"{key} ({credit}): 최근 {last}")
        except Exception as e:  # 소스 하나가 실패해도 나머지는 계속
            print(f"실패 — {key} ({credit}): {str(e)[:120]}" + ("; 이전 값 유지" if key in old else ""))
            if key in old:
                out[key] = old[key]

    if all(old.get(k) == out.get(k) for k, *_ in PARTS):
        print("변경 없음")
        return
    out["source"] = "미 재무부, 한국은행 ECOS, FRED"
    out["updated_at"] = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
