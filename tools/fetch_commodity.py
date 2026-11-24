"""원자재 가격을 받아 _data/commodity.json에 저장한다. 화면에 그리는 값만 저장한다.

- 에너지 일간: 미 에너지정보청(EIA) 현물가격(퍼블릭 도메인). EIA_API_KEY가 있으면 EIA API에서,
  없으면 같은 EIA 시리즈를 FRED(FRED_API_KEY)에서 받는다. EIA가 주 1회 몰아서 공표해 최대 1주 늦다.
- 국내 금: 한국거래소(KRX) Open API 금시장 일별매매정보(KRX_API_KEY, 서비스 이용 승인 필요).
  원본 응답은 저장하지 않고 1kg 상품의 날짜·종가만 남긴다. 승인 전에는 건너뛴다.
- 월간: 세계은행 Commodity Price Data(Pink Sheet, CC BY 4.0) 월평균 가격.
키는 출력하지 않는다. 한 소스가 실패해도 나머지는 계속 받고, 실패한 항목은 이전 값을 유지한다.
"""
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
OUT_PATH = os.path.join(ROOT, "_data", "commodity.json")
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (ywk-research.github.io data job)"}
DAILY_DAYS = 400       # 에너지·금 일간 보관 기간(달력일)
MONTHS = 37            # 월간 보관 개월 수(3년 + 1개월, 전년비 계산용)

# (키, 한국어, 영어, 단위, EIA 경로, EIA 시리즈, FRED 시리즈)
ENERGY = [
    ("wti", "WTI", "WTI", "달러/배럴", "petroleum/pri/spt", "RWTC", "DCOILWTICO"),
    ("brent", "브렌트", "Brent", "달러/배럴", "petroleum/pri/spt", "RBRTE", "DCOILBRENTEU"),
    ("hh", "천연가스 (헨리허브)", "Natural gas (Henry Hub)", "달러/MMBtu", "natural-gas/pri/fut", "RNGWHHD", "DHHNGSP"),
]

# 세계은행 Pink Sheet 열 이름 → (키, 묶음, 한국어, 영어, 단위)
WB_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
WB_ITEMS = [
    ("Gold", "gold", "precious", "금", "Gold", "달러/온스"),
    ("Silver", "silver", "precious", "은", "Silver", "달러/온스"),
    ("Platinum", "platinum", "precious", "백금", "Platinum", "달러/온스"),
    ("Copper", "copper", "base", "구리", "Copper", "달러/톤"),
    ("Aluminum", "aluminum", "base", "알루미늄", "Aluminum", "달러/톤"),
    ("Nickel", "nickel", "base", "니켈", "Nickel", "달러/톤"),
    ("Zinc", "zinc", "base", "아연", "Zinc", "달러/톤"),
    ("Iron ore, cfr spot", "iron", "base", "철광석", "Iron ore", "달러/톤"),
    ("Natural gas, Europe", "gas_eu", "energy", "천연가스 (유럽)", "Natural gas (Europe)", "달러/MMBtu"),
    ("Liquefied natural gas, Japan", "lng_jp", "energy", "LNG (일본)", "LNG (Japan)", "달러/MMBtu"),
    ("Coal, Australian", "coal", "energy", "석탄 (호주)", "Coal (Australia)", "달러/톤"),
    ("Wheat, US HRW", "wheat", "agri", "밀", "Wheat", "달러/톤"),
    ("Maize", "maize", "agri", "옥수수", "Maize", "달러/톤"),
    ("Soybeans", "soy", "agri", "대두", "Soybeans", "달러/톤"),
    ("Coffee, Arabica", "coffee", "agri", "커피 (아라비카)", "Coffee (Arabica)", "달러/kg"),
    ("Cocoa", "cocoa", "agri", "코코아", "Cocoa", "달러/kg"),
    ("Sugar, world", "sugar", "agri", "설탕", "Sugar", "달러/kg"),
]

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/"
KRX_GOLD = "gen/gold_bydd_trd"


class NotApproved(Exception):
    pass


def get(url, headers=None, raw=False):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                return b if raw else b.decode("utf-8-sig")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(3 * (attempt + 1))


def num(v):
    if v in (None, "", "-", ".", "…", "..."):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


# ---------- 에너지 일간 (EIA) ----------
def energy_eia(key, path, series, start):
    q = [("api_key", key), ("frequency", "daily"), ("data[0]", "value"), ("facets[series][]", series),
         ("start", start.isoformat()), ("sort[0][column]", "period"), ("sort[0][direction]", "asc"), ("length", "5000")]
    rows = json.loads(get(f"https://api.eia.gov/v2/{path}/data/?" + urllib.parse.urlencode(q)))["response"]["data"]
    return [[r["period"], v] for r in rows if (v := num(r.get("value"))) is not None]


def energy_fred(key, sid, start):
    q = urllib.parse.urlencode({"series_id": sid, "api_key": key, "file_type": "json", "observation_start": start.isoformat()})
    obs = json.loads(get("https://api.stlouisfed.org/fred/series/observations?" + q))["observations"]
    return [[o["date"], v] for o in obs if (v := num(o["value"])) is not None]


def update_energy(prev):
    eia = os.environ.get("EIA_API_KEY", "").strip()
    fred = os.environ.get("FRED_API_KEY", "").strip()
    if not (eia or fred):
        print("에너지: EIA_API_KEY·FRED_API_KEY 둘 다 없어 건너뜀")
        return prev
    start = dt.date.today() - dt.timedelta(days=DAILY_DAYS)
    old = {s["key"]: s for s in (prev or {}).get("series", [])}
    out, via = [], set()
    for key, ko, en, unit, path, eid, fid in ENERGY:
        data = None
        for name, fn in (("EIA", lambda: energy_eia(eia, path, eid, start) if eia else None),
                         ("FRED", lambda: energy_fred(fred, fid, start) if fred else None)):
            try:
                data = fn()
            except Exception as e:
                print(f"에너지 {key} {name} 실패: {type(e).__name__}")
                data = None
            if data:
                via.add(name)
                break
        if not data:
            data = (old.get(key) or {}).get("data") or []
        if data:
            out.append({"key": key, "ko": ko, "en": en, "unit": unit, "data": data})
            print(f"에너지 {key}: {len(data)}일, 마지막 {data[-1][0]}")
    if not out:
        return prev
    return {"via": "EIA" if via == {"EIA"} else "FRED" if via else (prev or {}).get("via", "FRED"), "series": out}


# ---------- 국내 금 (KRX 금시장) ----------
def krx_gold_rows(key, day):
    url = KRX_BASE + KRX_GOLD + "?" + urllib.parse.urlencode({"basDd": day.strftime("%Y%m%d")})
    try:
        body = get(url, {"AUTH_KEY": key, "Accept": "application/json"})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise NotApproved(str(e.code))
        raise
    return (json.loads(body) or {}).get("OutBlock_1") or []


def pick_1kg(rows):
    """금 99.99 1kg 상품 행(없으면 첫 행)."""
    for r in rows:
        nm = str(r.get("ISU_NM", "")).replace(" ", "").lower()
        if "1kg" in nm or "1키로" in nm:
            return r
    return rows[0] if rows else None


def update_gold(prev):
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        return prev
    today = dt.datetime.now(KST).date()
    data = [p for p in (prev or {}).get("data", [])]
    have = {p[0] for p in data}
    start = dt.date.fromisoformat(data[-1][0]) + dt.timedelta(days=1) if data else today - dt.timedelta(days=DAILY_DAYS)
    name = (prev or {}).get("name", "")
    day, calls = start, 0
    try:
        while day <= today:
            if day.weekday() < 5 and day.isoformat() not in have:
                rows = krx_gold_rows(key, day)
                calls += 1
                r = pick_1kg(rows)
                close = num(r.get("TDD_CLSPRC")) if r else None
                if close:
                    data.append([day.isoformat(), close])
                    name = r.get("ISU_NM", name)
                time.sleep(0.15)
            day += dt.timedelta(days=1)
    except NotApproved as e:
        print(f"국내 금: KRX 인증 실패({e}) — '금시장 일별매매정보' 서비스 이용 신청·승인 여부를 확인하세요. 건너뜀")
        return prev
    except Exception as e:
        print(f"국내 금 실패: {type(e).__name__}: {e} (받은 데까지 저장)")
    cut = (today - dt.timedelta(days=DAILY_DAYS)).isoformat()
    data = sorted(p for p in data if p[0] >= cut)
    print(f"국내 금: 호출 {calls}회, {len(data)}일" + (f", 마지막 {data[-1][0]}" if data else ""))
    if not data:
        return prev
    return {"name": name, "unit": "원/g", "data": data}


# ---------- 월간 (세계은행 Pink Sheet) ----------
def wb_url():
    html = get(WB_PAGE)
    links = sorted(set(re.findall(r'https://thedocs\.worldbank\.org/[^"\'\s]+CMO-Historical-Data-Monthly\.xlsx', html)))
    if not links:
        raise RuntimeError("Pink Sheet 월간 파일 링크를 찾지 못함")
    return links[-1]


def update_monthly(prev):
    try:
        import openpyxl
    except ImportError:
        print("월간: openpyxl이 없어 건너뜀 (pip install openpyxl)")
        return prev
    try:
        b = get(wb_url(), raw=True)
        wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True, data_only=True)
        rows = list(wb["Monthly Prices"].iter_rows(values_only=True))
    except Exception as e:
        print(f"월간 실패: {type(e).__name__}: {e}")
        return prev
    updated = ""
    head = None
    for i, r in enumerate(rows[:12]):
        if r and isinstance(r[0], str) and r[0].startswith("Updated on"):
            updated = dt.datetime.strptime(r[0].replace("Updated on", "").strip(), "%B %d, %Y").date().isoformat()
        if r and r[0] is None and any(c == "Copper" for c in r):
            head = i
    if head is None:
        print("월간: 머리글 행을 찾지 못함")
        return prev
    cols = {str(c).strip(): j for j, c in enumerate(rows[head]) if c}
    body = [r for r in rows[head + 2:] if r and isinstance(r[0], str) and re.match(r"^\d{4}M\d{2}$", r[0])]
    body = body[-MONTHS:]
    items = []
    for col, key, grp, ko, en, unit in WB_ITEMS:
        j = cols.get(col)
        if j is None:
            print(f"월간: '{col}' 열 없음")
            continue
        data = [[r[0][:4] + "-" + r[0][5:], v] for r in body if (v := num(r[j])) is not None]
        if data:
            items.append({"key": key, "group": grp, "ko": ko, "en": en, "unit": unit, "data": data})
    if not items:
        return prev
    last = max(it["data"][-1][0] for it in items)
    print(f"월간: {len(items)}개 품목, 마지막 {last}, 공표 {updated}")
    return {"published": updated, "month": last, "items": items}


def main():
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            prev = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        prev = {}
    new = {k: v for k, v in {
        "energy": update_energy(prev.get("energy")),
        "gold_krx": update_gold(prev.get("gold_krx")),
        "monthly": update_monthly(prev.get("monthly")),
    }.items() if v}
    if not new:
        sys.exit("원자재 데이터를 하나도 받지 못했습니다.")
    if {k: v for k, v in prev.items() if k != "updated_at"} == new:
        print("변경 없음")
        return
    new["updated_at"] = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print("저장:", os.path.relpath(OUT_PATH, ROOT))


if __name__ == "__main__":
    main()
