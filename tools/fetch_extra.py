"""포지셔닝·유동성·공시 등 추가 데이터를 받아 _data/에 저장한다. 모두 공식 공개 자료.

- FRED (FRED_API_KEY, 원자료 연준·미 재무부·시카고 연은): 연준 총자산(WALCL), 지급준비금(WRESBAL),
  재무부 일반계정 TGA(WTREGEN), 역레포(RRPONTSYD), 시카고 연은 금융여건지수 NFCI → _data/us_liquidity.json
- 미 에너지정보청 EIA 공개 파일(키 불필요, 퍼블릭 도메인): 미국 상업 원유 재고(전략비축유 제외, WCESTUS1 주간)
  → _data/crude_stocks.json (xlrd 필요)
- 미 재무부 Fiscal Data API (키 불필요): 국채(Note·Bond·TIPS) 입찰 결과 → _data/ust_auctions.json
- 미 상품선물거래위원회 CFTC 공개 API (키 불필요): 주간 투자자별 포지션(COT) → _data/cot.json
  · 금융선물(TFF): 레버리지 펀드 순포지션 — 통화, 미 10년물, S&P500
  · 원자재(Disaggregated): 머니매니저 순포지션 — WTI, 천연가스, 금, 구리
- BIS 통계 API (키 불필요): 실질실효환율(REER, 넓은 바스켓) → _data/reer.json
- 한국은행 ECOS (ECOS_API_KEY): 국제수지 증권투자 중 외국인 투자(부채) 주식·채권 → _data/kr_flows.json
- 금융감독원 OpenDART (DART_API_KEY): 전 영업일 주요 공시(주요사항보고·거래소공시 중 고른 종류)
  → _data/dart.json. 시가총액 필터는 fetch_krx.py가 Actions 캐시에 남긴 tools/state/caps.json을 쓴다(없으면 필터 없이 상위 일부)

키는 출력하지 않는다. 한 소스가 실패하면 그 파일만 이전 값을 유지한다.
"""
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "_data")
CAPS_PATH = os.path.join(ROOT, "tools", "state", "caps.json")
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (ywk-research.github.io data job)"}
TODAY = dt.datetime.now(KST).date()


def key(name):
    return os.environ.get(name, "").strip()


def get(url, raw=False):
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                return b if raw else b.decode("utf-8-sig")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                raise RuntimeError(re.sub(r"(api_key|crtfc_key)=[^&\s]+", r"\1=***", str(e)))
            time.sleep(3 * (attempt + 1))


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def save(fname, out, old):
    """내용(updated_at 제외)이 바뀌었을 때만 쓴다."""
    strip = lambda d: {k: v for k, v in (d or {}).items() if k != "updated_at"}
    if strip(out) == strip(old):
        print(f"{fname}: 변경 없음")
        return
    out["updated_at"] = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    with open(os.path.join(DATA_DIR, fname + ".json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{fname}: 저장")


def load(fname):
    p = os.path.join(DATA_DIR, fname + ".json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------- FRED ----------
def fred(series, start):
    k = key("FRED_API_KEY")
    if not k:
        raise RuntimeError("FRED_API_KEY 없음")
    q = urllib.parse.urlencode({"series_id": series, "api_key": k, "file_type": "json", "observation_start": start.isoformat()})
    obs = json.loads(get("https://api.stlouisfed.org/fred/series/observations?" + q))["observations"]
    return [[o["date"], num(o["value"])] for o in obs if num(o["value"]) is not None]


# (키, 시리즈, 단위 나눗수 → 10억 달러, 설명)
LIQ = [("assets", "WALCL", 1000), ("reserves", "WRESBAL", 1), ("tga", "WTREGEN", 1), ("rrp", "RRPONTSYD", 1), ("nfci", "NFCI", None)]


def us_liquidity():
    start = TODAY - dt.timedelta(days=365 * 4)
    out = {"source": "FRED (Federal Reserve Board, U.S. Treasury, Federal Reserve Bank of Chicago)", "unit": "10억 달러", "series": {}}
    for k, sid, div in LIQ:
        rows = fred(sid, start)
        out["series"][k] = [[d, round(v / div, 1) if div else round(v, 3)] for d, v in rows]
        time.sleep(0.4)
    return out


def crude_stocks():
    import xlrd  # EIA 과거 자료 파일은 옛 엑셀(.xls) 형식
    book = xlrd.open_workbook(file_contents=get("https://www.eia.gov/dnav/pet/hist_xls/WCESTUS1w.xls", raw=True))
    sh = book.sheet_by_name("Data 1")
    start = dt.date(TODAY.year - 7, 1, 1)
    rows = []
    for i in range(3, sh.nrows):  # 위 세 줄은 제목
        d, v = sh.cell_value(i, 0), num(sh.cell_value(i, 1))
        if not isinstance(d, float) or v is None:
            continue
        day = xlrd.xldate.xldate_as_datetime(d, book.datemode).date()
        if day >= start:
            rows.append([day.isoformat(), round(v / 1000, 1)])
    if not rows:
        raise RuntimeError("원유 재고 0건")
    return {"source": "U.S. Energy Information Administration (EIA)", "unit": "백만 배럴", "data": rows}


# ---------- 미 재무부 국채 입찰 ----------
def ust_auctions():
    start = (TODAY - dt.timedelta(days=365 * 2)).isoformat()
    q = urllib.parse.urlencode({"filter": f"security_type:in:(Note,Bond,TIPS),auction_date:gte:{start}",
                                "sort": "-auction_date", "page[size]": "300"}, safe=":(),")
    rows = json.loads(get("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query?" + q))["data"]
    out = []
    for r in rows:
        y, btc, acc = num(r.get("high_yield")), num(r.get("bid_to_cover_ratio")), num(r.get("total_accepted"))
        if y is None or btc is None:  # 아직 결과가 안 나온 예정 입찰
            continue
        comp = num(r.get("comp_accepted")) or acc
        share = lambda f: round(num(r.get(f)) / comp * 100, 1) if comp and num(r.get(f)) is not None else None
        tips = r.get("inflation_index_security") == "Yes" or r.get("security_type") == "TIPS"
        out.append({"d": r["auction_date"], "type": "TIPS" if tips else r["security_type"],
                    "term": r.get("original_security_term") or r.get("security_term"), "reopen": r.get("reopening") == "Yes",
                    "cusip": r.get("cusip"), "y": y, "btc": btc, "amt": round((num(r.get("offering_amt")) or 0) / 1e9, 1),
                    "ind": share("indirect_bidder_accepted"), "dir": share("direct_bidder_accepted"), "pd": share("primary_dealer_accepted")})
    if not out:
        raise RuntimeError("입찰 결과 0건")
    return {"source": "U.S. Department of the Treasury, Fiscal Data", "rows": out}


# ---------- CFTC COT ----------
COT_TFF = [  # (키, 한국어, 영어, 계약 코드)
    ("jpy", "엔", "Japanese yen", "097741"), ("eur", "유로", "Euro", "099741"), ("gbp", "파운드", "British pound", "096742"),
    ("aud", "호주달러", "Australian dollar", "232741"), ("dxy", "달러인덱스", "US Dollar Index", "098662"),
    ("ust10", "美 10년물 선물", "10Y T-Note", "043602"), ("spx", "S&P500 E-mini", "S&P 500 E-mini", "13874A"),
]
COT_DIS = [
    ("wti", "WTI", "WTI crude", "067651"), ("ng", "천연가스", "Natural gas", "023651"),
    ("gold", "금", "Gold", "088691"), ("copper", "구리", "Copper", "085692"),
]


def cot_rows(dataset, code, long_f, short_f, start):
    q = urllib.parse.urlencode({"cftc_contract_market_code": code, "$where": f"report_date_as_yyyy_mm_dd >= '{start}'",
                                "$order": "report_date_as_yyyy_mm_dd", "$limit": "1000"})
    rows = json.loads(get(f"https://publicreporting.cftc.gov/resource/{dataset}.json?" + q))
    out = []
    for r in rows:
        lo, sh, oi = num(r.get(long_f)), num(r.get(short_f)), num(r.get("open_interest_all"))
        if lo is None or sh is None:
            continue
        out.append([r["report_date_as_yyyy_mm_dd"][:10], int(lo - sh), int(oi) if oi else None])
    return out


def cot():
    start = (TODAY - dt.timedelta(days=365 * 3)).isoformat()
    out = {"source": "U.S. Commodity Futures Trading Commission (CFTC)", "fin": [], "cmd": []}
    for k, ko, en, code in COT_TFF:
        out["fin"].append({"key": k, "ko": ko, "en": en, "code": code,
                           "data": cot_rows("gpe5-46if", code, "lev_money_positions_long", "lev_money_positions_short", start)})
        time.sleep(0.3)
    for k, ko, en, code in COT_DIS:
        out["cmd"].append({"key": k, "ko": ko, "en": en, "code": code,
                           "data": cot_rows("72hh-3qpy", code, "m_money_positions_long_all", "m_money_positions_short_all", start)})
        time.sleep(0.3)
    if not any(s["data"] for s in out["fin"] + out["cmd"]):
        raise RuntimeError("COT 0건")
    return out


# ---------- BIS REER ----------
REER = [("KR", "한국", "Korea"), ("US", "미국", "US"), ("JP", "일본", "Japan"), ("CN", "중국", "China"), ("EA", "유로존", "Euro area")]  # noqa: E501


def reer():
    areas = "+".join(a for a, _, _ in REER)
    text = get(f"https://stats.bis.org/api/v2/data/dataflow/BIS/WS_EER/1.0/M.R.B.{areas}?startPeriod=2010-01&format=csv")
    data = {a: [] for a, _, _ in REER}
    for r in csv.DictReader(io.StringIO(text)):
        v = num(r.get("OBS_VALUE"))
        if r.get("REF_AREA") in data and v is not None:
            data[r["REF_AREA"]].append([r["TIME_PERIOD"], v])
    series = [{"key": a, "ko": ko, "en": en, "data": sorted(data[a])} for a, ko, en in REER if data[a]]
    if not series:
        raise RuntimeError("REER 0건")
    return {"source": "BIS effective exchange rate indices (real, broad, 2020=100)", "series": series}


# ---------- ECOS 외국인 증권투자 ----------
# 301Y013 국제수지(BPM6) 월별 > 금융계정 > 증권투자(부채) = 외국인의 국내 증권 투자. 양수면 순유입
KR_FLOW_TABLE = "301Y013"
KR_FLOW_ITEMS = [("equity", "BOPF22100000"), ("debt", "BOPF22200000")]  # 주식(부채), 부채성증권(부채)


def kr_flows():
    k = key("ECOS_API_KEY")
    if not k:
        raise RuntimeError("ECOS_API_KEY 없음")
    start, end = f"{TODAY.year - 6}01", TODAY.strftime("%Y%m")
    out = {"source": "한국은행 ECOS 국제수지", "unit": "백만 달러", "series": {}}
    for name, code in KR_FLOW_ITEMS:
        t = json.loads(get(f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/1000/{KR_FLOW_TABLE}/M/{start}/{end}/{code}"))
        rows = t.get("StatisticSearch", {}).get("row", [])
        out["series"][name] = [[r["TIME"][:4] + "-" + r["TIME"][4:6], num(r["DATA_VALUE"])] for r in rows if num(r["DATA_VALUE"]) is not None]
        time.sleep(0.3)
    if not all(out["series"].values()):
        raise RuntimeError("외국인 증권투자 0건")
    return out


# ---------- DART 주요 공시 ----------
# (분류, 한국어, 영어, 공시 제목에 들어가는 말)
DART_KINDS = [
    ("buyback", "자사주", "Buyback", ["자기주식취득결정", "자기주식소각결정", "자기주식취득신탁계약체결결정"]),
    ("capital", "증자·사채", "Equity & bonds", ["유상증자결정", "무상증자결정", "전환사채권발행결정", "신주인수권부사채권발행결정", "교환사채권발행결정"]),
    ("ma", "합병·분할", "M&A", ["회사합병결정", "회사분할결정", "회사분할합병결정", "영업양수결정", "영업양도결정",
                               "타법인주식및출자증권양수결정", "타법인주식및출자증권양도결정", "주식교환", "공개매수"]),
    ("earnings", "잠정실적", "Prelim. results", ["영업(잠정)실적", "연결재무제표기준영업(잠정)실적"]),
    ("contract", "공급계약", "Contracts", ["단일판매ㆍ공급계약체결", "단일판매·공급계약체결"]),
    ("dividend", "배당", "Dividends", ["현금ㆍ현물배당결정", "현금·현물배당결정"]),
]
DART_MIN_CAP = {"kospi": 1_000_000_000_000, "kosdaq": 300_000_000_000}
DART_KEEP_DAYS = 5


def prev_business_day(d):
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def dart(old):
    k = key("DART_API_KEY")
    if not k:
        raise RuntimeError("DART_API_KEY 없음")
    caps = {}
    if os.path.exists(CAPS_PATH):
        with open(CAPS_PATH, encoding="utf-8") as f:
            caps = json.load(f).get("rows", {})
    # 오늘 오전 실행이면 전 영업일, 저녁 실행이면 오늘까지 받는다(같은 날 다시 받아도 접수번호로 겹침 제거)
    now = dt.datetime.now(KST)
    days = [prev_business_day(TODAY)] + ([TODAY] if now.hour >= 16 and TODAY.weekday() < 5 else [])
    found = {}
    for day in days:
        for ty in ("B", "I"):
            page = 1
            while True:
                q = urllib.parse.urlencode({"crtfc_key": k, "bgn_de": day.strftime("%Y%m%d"), "end_de": day.strftime("%Y%m%d"),
                                            "pblntf_ty": ty, "page_no": page, "page_count": 100, "last_reprt_at": "Y"})
                t = json.loads(get("https://opendart.fss.or.kr/api/list.json?" + q))
                if t.get("status") == "013":  # 조회된 데이터 없음
                    break
                if t.get("status") != "000":
                    raise RuntimeError(f"DART 응답 {t.get('status')} {t.get('message')}")
                for r in t.get("list", []):
                    if r.get("corp_cls") not in ("Y", "K"):
                        continue
                    nm = re.sub(r"\s+", "", r.get("report_nm", ""))
                    if nm.startswith("[") and "정정" in nm.split("]")[0]:  # 정정 공시는 뺀다
                        continue
                    kind = next((kk for kk, _, _, words in DART_KINDS if any(w in nm for w in words)), None)
                    if not kind:
                        continue
                    code = r.get("stock_code", "")
                    mkt = "kospi" if r["corp_cls"] == "Y" else "kosdaq"
                    cap = caps.get(code, [None, None])[1] if caps else None
                    if caps and (cap is None or cap < DART_MIN_CAP[mkt]):
                        continue
                    found[r["rcept_no"]] = {"d": r["rcept_dt"][:4] + "-" + r["rcept_dt"][4:6] + "-" + r["rcept_dt"][6:],
                                            "corp": r["corp_name"], "code": code, "mkt": mkt, "cap": cap, "kind": kind,
                                            "title": re.sub(r"^\[[^\]]*\]", "", r["report_nm"]).strip(), "no": r["rcept_no"]}
                if page >= int(t.get("total_page", 1)):
                    break
                page += 1
                time.sleep(0.3)
    keep = {r["no"]: r for r in (old.get("rows") or [])}
    keep.update(found)
    dates = sorted({r["d"] for r in keep.values()}, reverse=True)[:DART_KEEP_DAYS]
    rows = sorted((r for r in keep.values() if r["d"] in dates), key=lambda r: (r["d"], r["cap"] or 0), reverse=True)
    return {"source": "금융감독원 전자공시시스템(DART) OpenDART", "filtered_by_cap": bool(caps),
            "kinds": [{"key": kk, "ko": ko, "en": en} for kk, ko, en, _ in DART_KINDS], "rows": rows}


JOBS = [("us_liquidity", us_liquidity), ("crude_stocks", crude_stocks), ("ust_auctions", ust_auctions),
        ("cot", cot), ("reer", reer), ("kr_flows", kr_flows), ("dart", None)]


def main():
    only = set(sys.argv[1:])
    os.makedirs(DATA_DIR, exist_ok=True)
    for fname, fn in JOBS:
        if only and fname not in only:
            continue
        old = load(fname)
        try:
            out = dart(old) if fname == "dart" else fn()
            save(fname, out, old)
        except Exception as e:  # 소스 하나가 실패해도 나머지는 계속
            print(f"실패 — {fname}: {type(e).__name__}: {str(e)[:200]}" + ("; 이전 값 유지" if old else ""))


if __name__ == "__main__":
    main()
