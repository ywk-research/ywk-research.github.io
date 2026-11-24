"""KRX Open API에서 국내 지수·종목 데이터를 받아 _data/에 저장한다.

- 인증키: 환경변수 KRX_API_KEY (GitHub Secrets). 키는 출력하지 않는다.
- _data/krx_index.json : 코스피·코스닥·VKOSPI 일별 종가 누적
  (처음 받는 시리즈는 최근 BACKFILL_DAYS일을 채우고, 이후에는 마지막 저장일 다음 날부터 받는다)
- _data/krx_movers.json : 직전 거래일 코스피·코스닥 등락률 상·하위, 거래대금 상위
- 이용 신청이 안 된 부가 서비스(VKOSPI, 종목 시세)는 건너뛰고 나머지는 계속 받는다.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(ROOT, "_data", "krx_index.json")
MOVERS_PATH = os.path.join(ROOT, "_data", "krx_movers.json")
SECTORS_PATH = os.path.join(ROOT, "_data", "krx_sectors.json")
ETF_PATH = os.path.join(ROOT, "_data", "krx_etf.json")
ETF_ENDPOINT = "etp/etf_bydd_trd"
ETF_TOP_N = 10
BASE = "https://data-dbg.krx.co.kr/svc/apis/"
KST = dt.timezone(dt.timedelta(hours=9))
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "400"))


def is_vkospi(nm):
    return "변동성" in nm and "200" in nm and "선물" not in nm


# 이름: (엔드포인트, 행 선택 함수, 필수 여부)
SERIES = {
    "kospi": ("idx/kospi_dd_trd", lambda nm: nm == "코스피", True),
    "kosdaq": ("idx/kosdaq_dd_trd", lambda nm: nm == "코스닥", True),
    "vkospi": ("idx/drvprod_dd_trd", is_vkospi, False),
}
STOCKS = {
    # 시장: (엔드포인트, 최소 시가총액(원))
    "kospi": ("sto/stk_bydd_trd", 1_000_000_000_000),
    "kosdaq": ("sto/ksq_bydd_trd", 300_000_000_000),
}
TOP_N = 5


class NotApproved(Exception):
    pass


def num(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def fetch(endpoint, bas_dd, key):
    url = BASE + endpoint + "?" + urllib.parse.urlencode({"basDd": bas_dd})
    req = urllib.request.Request(url, headers={"AUTH_KEY": key, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return (json.loads(r.read().decode("utf-8")) or {}).get("OutBlock_1") or []
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise NotApproved(endpoint)
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def now_str():
    return dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def update_index(key, today):
    data = {"source": "한국거래소(KRX) Open API", "updated_at": None}
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as f:
            data.update(json.load(f))

    added = 0
    for name, (endpoint, pick, required) in SERIES.items():
        series = data.setdefault(name, [])
        day = (dt.date.fromisoformat(series[-1]["d"]) + dt.timedelta(days=1)) if series \
            else today - dt.timedelta(days=BACKFILL_DAYS)
        try:
            while day <= today:
                if day.weekday() < 5:  # 주말 제외, 공휴일은 빈 응답으로 걸러진다
                    rows = fetch(endpoint, day.strftime("%Y%m%d"), key)
                    row = next((r for r in rows if pick(r.get("IDX_NM", ""))), None)
                    close = num(row.get("CLSPRC_IDX")) if row else None
                    if close is not None:
                        series.append({
                            "d": day.isoformat(), "close": close,
                            "chg": num(row.get("CMPPREVDD_IDX")), "pct": num(row.get("FLUC_RT")),
                            "open": num(row.get("OPNPRC_IDX")), "high": num(row.get("HGPRC_IDX")),
                            "low": num(row.get("LWPRC_IDX")),
                        })
                        added += 1
                    time.sleep(0.25)
                day += dt.timedelta(days=1)
        except NotApproved:
            msg = f"KRX API 인증 실패({endpoint}): 인증키와 해당 서비스 이용 승인 여부를 확인하세요."
            if required:
                sys.exit(msg)
            print("건너뜀 — " + msg)
        print(f"{name}: {len(series)}일")

    if added:
        data["updated_at"] = now_str()
        save(INDEX_PATH, data)
    return added


# 업종지수가 아닌 시리즈(시장 대표·규모별·테마 지수)를 걸러내는 단어
NON_SECTOR = ("코스피", "코스닥", "KOSPI", "KOSDAQ", "KRX", "대형", "중형", "소형", "배당", "우량", "지배구조",
              "벤처", "글로벌", "기술주", "프리미어", "레버리지", "인버스", "선물", "변동성", "TR", "외국인", "동일가중")


def is_sector(nm):
    return bool(nm) and not any(w in nm for w in NON_SECTOR) and not any(c.isdigit() for c in nm)


def last_trading_day(key, today):
    for back in range(10):
        day = today - dt.timedelta(days=back)
        if day.weekday() < 5 and fetch(SERIES["kospi"][0], day.strftime("%Y%m%d"), key):
            return day
    return None


def closes_on_or_before(endpoint, day, key):
    """day 또는 그 직전 거래일의 {지수명: 종가}. (거래일, 값) 반환"""
    for back in range(10):
        d = day - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        rows = fetch(endpoint, d.strftime("%Y%m%d"), key)
        if rows:
            return d, {r.get("IDX_NM", ""): num(r.get("CLSPRC_IDX")) for r in rows}
        time.sleep(0.25)
    return None, {}


def chg(now, then):
    return round((now / then - 1) * 100, 2) if now and then else None


def update_sectors(key, day):
    out = {"source": "한국거래소(KRX) Open API", "d": day.isoformat()}
    for mkt in ("kospi", "kosdaq"):
        rows = fetch(SERIES[mkt][0], day.strftime("%Y%m%d"), key)
        names = [r.get("IDX_NM", "") for r in rows]
        print(f"{mkt} 지수 시리즈 {len(names)}개: " + ", ".join(names))
        # 1주·1개월 등락: 7일·1개월 전(휴장이면 그 직전 거래일) 종가와 비교
        w_day, w_close = closes_on_or_before(SERIES[mkt][0], day - dt.timedelta(days=7), key)
        m_day, m_close = closes_on_or_before(SERIES[mkt][0], day - dt.timedelta(days=30), key)
        out[mkt + "_base"] = {"w1": w_day and w_day.isoformat(), "m1": m_day and m_day.isoformat()}
        sec = [{"name": r["IDX_NM"], "close": num(r.get("CLSPRC_IDX")), "pct": num(r.get("FLUC_RT")),
                "w1": chg(num(r.get("CLSPRC_IDX")), w_close.get(r["IDX_NM"])),
                "m1": chg(num(r.get("CLSPRC_IDX")), m_close.get(r["IDX_NM"]))}
               for r in rows if is_sector(r.get("IDX_NM", "")) and num(r.get("FLUC_RT")) is not None]
        out[mkt] = sorted(sec, key=lambda x: x["pct"], reverse=True)
        print(f"{mkt} 업종 {len(sec)}개 선택: " + ", ".join(x["name"] for x in sec))
    return write_if_changed(SECTORS_PATH, out)


def write_if_changed(path, out):
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    strip = lambda o: {k: v for k, v in (o or {}).items() if k != "updated_at"}
    if strip(old) == strip(out):
        return 0
    out["updated_at"] = now_str()
    save(path, out)
    return 1


def is_common_stock(r):
    code, name = r.get("ISU_CD", ""), r.get("ISU_NM", "")
    if not code.endswith("0"):  # 우선주(끝자리 5·7·9 등) 제외
        return False
    if "스팩" in name or "SPAC" in r.get("SECT_TP_NM", "").upper():
        return False
    return True


def won_txt(eok):
    jo, rest = divmod(int(eok), 10000)
    return (f"{jo}조 " if jo else "") + (f"{rest:,}억" if rest or not jo else "")


def slim(r):
    eok = round((num(r.get("ACC_TRDVAL")) or 0) / 1e8)
    return {
        "name": r["ISU_NM"], "code": r["ISU_CD"],
        "close": num(r.get("TDD_CLSPRC")), "pct": num(r.get("FLUC_RT")),
        "value": eok, "value_txt": won_txt(eok).strip(),  # 거래대금, 억 원
    }


def update_movers(key, day):
    out = {"source": "한국거래소(KRX) Open API", "d": day.isoformat(), "rules": {}}
    bas_dd = day.strftime("%Y%m%d")
    try:
        for mkt, (endpoint, min_cap) in STOCKS.items():
            rows = [r for r in fetch(endpoint, bas_dd, key) if is_common_stock(r)]
            big = [r for r in rows if (num(r.get("MKTCAP")) or 0) >= min_cap and num(r.get("FLUC_RT")) is not None]
            by_pct = sorted(big, key=lambda r: num(r["FLUC_RT"]))
            by_val = sorted(rows, key=lambda r: num(r.get("ACC_TRDVAL")) or 0, reverse=True)
            out[mkt] = {
                "gainers": [slim(r) for r in reversed(by_pct[-TOP_N:])],
                "losers": [slim(r) for r in by_pct[:TOP_N]],
                "value": [slim(r) for r in by_val[:TOP_N]],
            }
            out["rules"][mkt] = f"시가총액 {won_txt(min_cap // 100_000_000).strip()} 원 이상, 우선주·스팩 제외"
            time.sleep(0.25)
    except NotApproved as e:
        print(f"건너뜀 — KRX API 인증 실패({e}): 유가증권·코스닥 일별매매정보 이용 승인 여부를 확인하세요.")
        return 0
    return write_if_changed(MOVERS_PATH, out)


def update_etf(key, day):
    """국내 ETF 거래대금 상위 (ETF 일별매매정보). 승인 전이면 건너뛴다."""
    try:
        rows = fetch(ETF_ENDPOINT, day.strftime("%Y%m%d"), key)
    except NotApproved as e:
        print(f"건너뜀 — KRX API 인증 실패({e}): ETF 일별매매정보 이용 승인 여부를 확인하세요.")
        return 0
    if not rows:
        print("ETF 응답 0건")
        return 0
    rows = sorted(rows, key=lambda r: num(r.get("ACC_TRDVAL")) or 0, reverse=True)
    top = []
    for r in rows[:ETF_TOP_N]:
        eok = round((num(r.get("ACC_TRDVAL")) or 0) / 1e8)
        nav_eok = round((num(r.get("INVSTASST_NETASST_TOTAMT")) or 0) / 1e8)
        top.append({"name": r.get("ISU_NM", ""), "code": r.get("ISU_CD", ""),
                    "close": num(r.get("TDD_CLSPRC")), "pct": num(r.get("FLUC_RT")),
                    "value": eok, "value_txt": won_txt(eok).strip(),
                    "aum_txt": won_txt(nav_eok).strip() if nav_eok else "",
                    "index": r.get("IDX_IND_NM", "")})
    total = round(sum(num(r.get("ACC_TRDVAL")) or 0 for r in rows) / 1e8)
    out = {"source": "한국거래소(KRX) Open API", "d": day.isoformat(), "count": len(rows),
           "total_txt": won_txt(total).strip(), "top": top}
    return write_if_changed(ETF_PATH, out)


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        sys.exit("KRX_API_KEY 환경변수가 없습니다. 저장소 Settings → Secrets에 등록하세요.")
    today = dt.datetime.now(KST).date()
    added = update_index(key, today)
    day = last_trading_day(key, today)
    if not day:
        print("최근 거래일을 찾지 못했습니다.")
        return
    sec = update_sectors(key, day)
    moved = update_movers(key, day)
    etf = update_etf(key, day)
    print(f"{day} 기준 · 지수 추가 {added}건, 업종 {'갱신' if sec else '변경 없음'}, 종목 {'갱신' if moved else '변경 없음'}, ETF {'갱신' if etf else '변경 없음'}")


if __name__ == "__main__":
    main()
