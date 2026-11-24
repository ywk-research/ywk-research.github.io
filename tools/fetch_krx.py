"""KRX Open API에서 코스피·코스닥 일별 지수를 받아 _data/krx_index.json에 누적한다.

- 인증키: 환경변수 KRX_API_KEY (GitHub Secrets). 키는 출력하지 않는다.
- 엔드포인트: https://data-dbg.krx.co.kr/svc/apis/idx/{kospi_dd_trd|kosdaq_dd_trd}?basDd=YYYYMMDD
- 처음 실행하면 최근 BACKFILL_DAYS일을 채우고, 이후에는 마지막 저장일 다음 날부터 어제까지만 받는다.
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
DATA_PATH = os.path.join(ROOT, "_data", "krx_index.json")
BASE = "https://data-dbg.krx.co.kr/svc/apis/idx/"
SERIES = {
    "kospi": ("kospi_dd_trd", "코스피"),
    "kosdaq": ("kosdaq_dd_trd", "코스닥"),
}
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "400"))
KST = dt.timezone(dt.timedelta(hours=9))


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
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"KRX API 인증 실패(HTTP {e.code}): 인증키와 '지수' 서비스 이용 신청 승인 여부를 확인하세요.")
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))


def load():
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"source": "한국거래소(KRX) Open API", "updated_at": None, "kospi": [], "kosdaq": []}


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        sys.exit("KRX_API_KEY 환경변수가 없습니다. 저장소 Settings → Secrets에 등록하세요.")

    data = load()
    today = dt.datetime.now(KST).date()
    last_dates = [s[-1]["d"] for s in (data["kospi"], data["kosdaq"]) if s]
    if last_dates:
        start = dt.date.fromisoformat(min(last_dates)) + dt.timedelta(days=1)
    else:
        start = today - dt.timedelta(days=BACKFILL_DAYS)

    added = 0
    day = start
    while day <= today:
        if day.weekday() < 5:  # 주말 제외, 공휴일은 빈 응답으로 걸러진다
            bas_dd = day.strftime("%Y%m%d")
            for name, (endpoint, idx_nm) in SERIES.items():
                rows = (fetch(endpoint, bas_dd, key) or {}).get("OutBlock_1") or []
                row = next((r for r in rows if r.get("IDX_NM") == idx_nm), None)
                close = num(row.get("CLSPRC_IDX")) if row else None
                if close is None:
                    continue
                rec = {
                    "d": day.isoformat(),
                    "close": close,
                    "chg": num(row.get("CMPPREVDD_IDX")),
                    "pct": num(row.get("FLUC_RT")),
                    "open": num(row.get("OPNPRC_IDX")),
                    "high": num(row.get("HGPRC_IDX")),
                    "low": num(row.get("LWPRC_IDX")),
                }
                series = data[name]
                if not series or series[-1]["d"] < rec["d"]:
                    series.append(rec)
                    added += 1
            time.sleep(0.3)
        day += dt.timedelta(days=1)

    if added:
        data["updated_at"] = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"추가된 레코드: {added}, 코스피 {len(data['kospi'])}일, 코스닥 {len(data['kosdaq'])}일")


if __name__ == "__main__":
    main()
