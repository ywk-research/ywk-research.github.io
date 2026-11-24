"""데이터 탭 '매크로' 섹션용 금리 데이터를 받아 _data/macro.json에 저장한다.

- 미 재무부 공식 일별 수익률 곡선 CSV (키 불필요): 1개월~30년 만기
- 한국은행 ECOS (ECOS_API_KEY): 국고채 1~30년(817Y002), 콜·CD·CP(817Y002), 기준금리(722Y001)
- FRED (FRED_API_KEY): 연준 정책금리 목표 상단(DFEDTARU) — 한미 기준금리차 계산용
- 미 재무부 실질 수익률 곡선 CSV: 5·10년 실질금리(TIPS) → _data/macro.json의 us_real
- ECOS 731Y001 매매기준율: 원/달러·원/100엔·원/위안·원/유로 → _data/krw_fx.json
- ECOS 월간 지표: 소비자물가·근원물가, 수출입, 경상수지, 실업률, 산업생산, 소비자심리 → _data/kr_monthly.json
- FRED 미국 월간 지표(원자료 BLS·BEA·인구조사국·연준, 모두 미 정부 공개 자료): CPI·근원 CPI, PCE·근원 PCE,
  비농업 고용, 실업률, 소매판매, 실질 GDP(분기) → _data/us_monthly.json

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
DATA_DIR = os.path.join(ROOT, "_data")
KST = dt.timezone(dt.timedelta(hours=9))
DAYS = int(os.environ.get("HISTORY_DAYS", "400"))
UA = {"User-Agent": "Mozilla/5.0 (ywk-research.github.io data job)"}

# 미 재무부 CSV 열 이름 → 표시 이름
US_TENORS = [("1 Mo", "1M"), ("3 Mo", "3M"), ("6 Mo", "6M"), ("1 Yr", "1Y"), ("2 Yr", "2Y"), ("3 Yr", "3Y"),
             ("5 Yr", "5Y"), ("7 Yr", "7Y"), ("10 Yr", "10Y"), ("20 Yr", "20Y"), ("30 Yr", "30Y")]
US_REAL = [("5 YR", "5Y"), ("10 YR", "10Y")]
# ECOS 731Y001 매매기준율 (원/100엔은 100엔당)
KRW_FX = [("usd", "0000001"), ("jpy100", "0000002"), ("cny", "0000053"), ("eur", "0000003")]
# ECOS 월간 지표: (키, 통계표, 항목 경로)
KR_MONTHLY = [
    ("cpi", "901Y009", "0"),            # 소비자물가지수 총지수 (2020=100)
    ("core", "901Y010", "QB"),          # 농산물및석유류제외지수
    ("exports", "901Y118", "T002"),     # 수출금액 (천 달러, 통관 기준)
    ("imports", "901Y118", "T004"),     # 수입금액 (천 달러)
    ("current", "301Y017", "SA000"),    # 경상수지 계절조정 (백만 달러)
    ("unemp", "901Y027", "I61BC/I28B"), # 실업률 계절조정 (%)
    ("ip", "901Y033", "A00/2"),         # 전산업생산지수 계절조정 (2020=100)
    ("csi", "511Y002", "FME/99988"),    # 소비자심리지수
]
# FRED 미국 월간 지표: (키, 시리즈 ID). 저작권 제한이 있는 민간 지표(미시간대·ISM·컨퍼런스보드)는 넣지 않는다
US_MONTHLY = [
    ("cpi", "CPIAUCSL"),               # CPI 전체, 계절조정 (BLS)
    ("core", "CPILFESL"),              # 근원 CPI(식품·에너지 제외) (BLS)
    ("pce", "PCEPI"),                  # PCE 물가지수 (BEA)
    ("core_pce", "PCEPILFE"),          # 근원 PCE (BEA)
    ("nfp", "PAYEMS"),                 # 비농업 고용, 천 명 (BLS)
    ("unemp", "UNRATE"),               # 실업률 (BLS)
    ("retail", "RSAFS"),               # 소매·외식 판매, 백만 달러 (인구조사국)
    ("gdp", "A191RL1Q225SBEA"),        # 실질 GDP 전기 대비 연율 %, 분기 (BEA)
]
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


def treasury(kind, cols, start):
    rows = {}
    for year in range(start.year, dt.date.today().year + 1):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
               f"{year}/all?type={kind}&field_tdr_date_value={year}&page&_format=csv")
        for r in csv.DictReader(io.StringIO(get(url))):
            m, d, y = r["Date"].split("/")
            day = dt.date(int(y), int(m), int(d))
            if day >= start:
                rows[day.isoformat()] = [num(r.get(col)) for col in cols]
        time.sleep(0.5)
    return [[d, rows[d]] for d in sorted(rows)]


def us_curve(start):
    return treasury("daily_treasury_yield_curve", [c for c, _ in US_TENORS], start)


def us_real(start):
    # 실질 수익률 CSV 열 이름은 "5 YR", "10 YR"
    return treasury("daily_treasury_real_yield_curve", [c for c, _ in US_REAL], start)


def ecos(table, item, start, cycle="D"):
    key = os.environ["ECOS_API_KEY"].strip()
    end = dt.datetime.now(KST).date()
    fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/{table}/{cycle}/"
           f"{start.strftime(fmt)}/{end.strftime(fmt)}/{item}")
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
            t = r["TIME"]
            out[f"{t[:4]}-{t[4:6]}-{t[6:]}" if cycle == "D" else f"{t[:4]}-{t[4:6]}"] = v
    return out


def kr_curve(start):
    cols = [ecos("817Y002", code, start) for code, _ in KR_TENORS]
    days = sorted(set().union(*cols))
    return [[d, [c.get(d) for c in cols]] for d in days]


def kr_short(start):
    return {name: sorted([d, v] for d, v in ecos(t, code, start).items()) for name, t, code in KR_SHORT}


def fred(series, start):
    key = os.environ["FRED_API_KEY"].strip()
    q = urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json",
                                "observation_start": start.isoformat()})
    try:
        obs = json.loads(get("https://api.stlouisfed.org/fred/series/observations?" + q))["observations"]
    except Exception as e:  # 오류 메시지에 키가 들어간 URL이 섞이지 않게
        raise RuntimeError(f"{series}: {type(e).__name__}") from None
    return [[o["date"], float(o["value"])] for o in obs if o["value"] not in (".", "")]


def fed_upper(start):
    return fred("DFEDTARU", start)


def us_monthly(start):
    # 전년 대비 계산용으로 5년치. 날짜는 YYYY-MM(분기 GDP는 분기 첫 달)
    m0 = start.replace(day=1, year=start.year - 4)
    out = {}
    for name, series in US_MONTHLY:
        try:
            out[name] = [[d[:7], v] for d, v in fred(series, m0)]
        except Exception as e:  # 지표 하나가 실패해도 나머지는 싣는다
            print(f"  실패 — {name}: {str(e)[:100]}")
    return out


def krw_fx(start):
    out = {name: sorted([d, v] for d, v in ecos("731Y001", code, start).items()) for name, code in KRW_FX}
    # 서울 외환시장 원/달러 종가(15:30, 731Y003) — 개장 전 브리핑(tools/make_briefing.py)이 쓴다
    try:
        out["usd_close"] = sorted([d, v] for d, v in ecos("731Y003", "0000003", start).items())
    except Exception as e:
        print(f"  실패 — usd_close: {str(e)[:100]}")
    return out


def kr_monthly(start):
    # 전년 대비 계산용으로 5년치를 받는다
    m0 = start.replace(day=1, year=start.year - 4)
    out = {}
    for name, table, item in KR_MONTHLY:
        try:
            out[name] = sorted([d, v] for d, v in ecos(table, item, m0, "M").items())
        except Exception as e:  # 지표 하나가 실패해도 나머지는 싣는다
            print(f"  실패 — {name}: {str(e)[:100]}")
    return out


# (파일, 키, 출처, 필요한 키, 함수)
PARTS = [
    ("macro", "us", "미 재무부", None, us_curve),
    ("macro", "kr", "한국은행 ECOS", "ECOS_API_KEY", kr_curve),
    ("macro", "kr_short", "한국은행 ECOS", "ECOS_API_KEY", kr_short),
    ("macro", "fed_upper", "FRED(연준)", "FRED_API_KEY", fed_upper),
    ("macro", "us_real", "미 재무부", None, us_real),
    ("krw_fx", "series", "한국은행 ECOS", "ECOS_API_KEY", krw_fx),
    ("kr_monthly", "series", "한국은행 ECOS", "ECOS_API_KEY", kr_monthly),
    ("us_monthly", "series", "FRED(BLS·BEA·인구조사국·연준)", "FRED_API_KEY", us_monthly),
]
FIXED = {"macro": {"us_tenors": [t for _, t in US_TENORS], "kr_tenors": [t for _, t in KR_TENORS],
                   "us_real_tenors": [t for _, t in US_REAL],
                   "source": "미 재무부, 한국은행 ECOS, FRED"},
         "krw_fx": {"source": "한국은행 ECOS"},
         "kr_monthly": {"source": "한국은행 ECOS"},
         "us_monthly": {"source": "FRED (BLS, BEA, U.S. Census Bureau, Federal Reserve)"}}


def main():
    start = dt.datetime.now(KST).date() - dt.timedelta(days=DAYS)
    for fname in FIXED:
        path = os.path.join(DATA_DIR, fname + ".json")
        old = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        out = dict(FIXED[fname])
        parts = [p for p in PARTS if p[0] == fname]
        for _, key, credit, env, fn in parts:
            try:
                if env and not os.environ.get(env, "").strip():
                    raise RuntimeError(f"{env} 없음")
                val = fn(start)
                if not val:
                    raise RuntimeError("빈 응답")
                if isinstance(val, dict) and isinstance(old.get(key), dict):  # 빠진 지표는 이전 값으로
                    val = {**{k: v for k, v in old[key].items()}, **val}
                out[key] = val
                last = val[-1][0] if isinstance(val, list) else {k: v[-1][0] for k, v in val.items() if v}
                print(f"{fname}.{key} ({credit}): 최근 {last}")
            except Exception as e:  # 소스 하나가 실패해도 나머지는 계속
                print(f"실패 — {fname}.{key} ({credit}): {str(e)[:120]}" + ("; 이전 값 유지" if key in old else ""))
                if key in old:
                    out[key] = old[key]
        if all(old.get(k) == out.get(k) for _, k, *_ in parts):
            print(f"{fname}: 변경 없음")
            continue
        if not any(k in out for _, k, *_ in parts):
            continue
        out["updated_at"] = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
