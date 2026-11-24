"""FRED API에서 해외 지수·금리·유가 일별 값을 받아 _data/global_index.json에 저장한다.

- 인증키: 환경변수 FRED_API_KEY (GitHub Secrets). 키는 출력하지 않는다.
- 시리즈마다 최근 HISTORY_DAYS일치를 통째로 다시 받아 덮어쓴다(값 수정·지연 반영 포함).
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
DATA_PATH = os.path.join(ROOT, "_data", "global_index.json")
API = "https://api.stlouisfed.org/fred/series/observations"
KST = dt.timezone(dt.timedelta(hours=9))
HISTORY_DAYS = int(os.environ.get("HISTORY_DAYS", "400"))

# 키: (FRED 시리즈, 표시 이름, 단위)  단위 "pt"=지수, "pct"=금리(%), "usd"=달러
SERIES = [
    ("sp500", "SP500", "S&P500", "pt"),
    ("nasdaq", "NASDAQCOM", "나스닥", "pt"),
    ("dow", "DJIA", "다우", "pt"),
    ("vix", "VIXCLS", "VIX", "pt"),
    ("nikkei", "NIKKEI225", "닛케이225", "pt"),
    ("us10y", "DGS10", "美 10년물", "pct"),
    ("wti", "DCOILWTICO", "WTI", "usd"),
]


def fetch(series_id, start, key):
    q = urllib.parse.urlencode({"series_id": series_id, "api_key": key, "file_type": "json",
                                "observation_start": start.isoformat()})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(API + "?" + q, timeout=30) as r:
                return json.loads(r.read().decode("utf-8")).get("observations", [])
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403) and attempt == 0:
                body = e.read().decode("utf-8", "ignore")
                if "api_key" in body:
                    sys.exit("FRED API 인증 실패: FRED_API_KEY 값을 확인하세요.")
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))


def main():
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        sys.exit("FRED_API_KEY 환경변수가 없습니다. 저장소 Settings → Secrets에 등록하세요.")

    today = dt.datetime.now(KST).date()
    start = today - dt.timedelta(days=HISTORY_DAYS)
    out = {"source": "FRED, Federal Reserve Bank of St. Louis", "series": []}
    for name, sid, label, unit in SERIES:
        obs = [(o["date"], float(o["value"])) for o in fetch(sid, start, key) if o.get("value") not in (".", "", None)]
        rows = []
        for i, (d, v) in enumerate(obs):
            prev = obs[i - 1][1] if i else None
            chg = round(v - prev, 4) if prev is not None else None
            pct = round((v / prev - 1) * 100, 2) if prev else None
            rows.append({"d": d, "close": v, "chg": chg, "pct": pct})
        out["series"].append({"key": name, "id": sid, "label": label, "unit": unit, "data": rows})
        last = rows[-1] if rows else {}
        print(f"{label}({sid}): {len(rows)}일, 최근 {last.get('d')} {last.get('close')}")
        time.sleep(0.3)

    old = None
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, encoding="utf-8") as f:
            old = json.load(f)
    if old and old.get("series") == out["series"]:
        print("변경 없음")
        return
    out["updated_at"] = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
