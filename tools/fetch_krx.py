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


def update_movers(key, today):
    # 직전 거래일 찾기: 오늘부터 거꾸로 최대 10일
    out = {"source": "한국거래소(KRX) Open API", "rules": {}, "updated_at": None}
    found_day = None
    try:
        for back in range(10):
            day = today - dt.timedelta(days=back)
            if day.weekday() >= 5:
                continue
            bas_dd = day.strftime("%Y%m%d")
            first_ep = STOCKS["kospi"][0]
            if fetch(first_ep, bas_dd, key):
                found_day = day
                break
        if not found_day:
            print("종목 시세: 최근 거래일 데이터를 찾지 못했습니다.")
            return 0
        bas_dd = found_day.strftime("%Y%m%d")
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

    out["d"] = found_day.isoformat()
    out["updated_at"] = now_str()
    old = None
    if os.path.exists(MOVERS_PATH):
        with open(MOVERS_PATH, encoding="utf-8") as f:
            old = json.load(f)
    if old and {k: v for k, v in old.items() if k != "updated_at"} == {k: v for k, v in out.items() if k != "updated_at"}:
        return 0
    save(MOVERS_PATH, out)
    print(f"종목 시세: {out['d']} 기준 저장")
    return 1


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        sys.exit("KRX_API_KEY 환경변수가 없습니다. 저장소 Settings → Secrets에 등록하세요.")
    today = dt.datetime.now(KST).date()
    added = update_index(key, today)
    moved = update_movers(key, today)
    print(f"지수 추가 {added}건, 종목 시세 {'갱신' if moved else '변경 없음'}")


if __name__ == "__main__":
    main()
