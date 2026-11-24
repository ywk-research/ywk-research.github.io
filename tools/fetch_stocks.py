"""종목 상세 탭(/stock/)의 국내 종목 차트 자료를 만든다.

KRX 약관(제3자 제공 금지)에 따라 원본은 저장소에 커밋하지 않는다.
- 원본: tools/state/krx_daily/YYYY-MM-DD.json = {코드: [종가, 거래량]} (보통주 전체)
  tools/state/는 .gitignore이고 워크플로가 Actions 캐시로만 넘긴다. 캐시가 없으면 최근 HIST_DAYS일을 다시 받는다.
- 공개(커밋): 사이트에서 실제로 링크되는 종목만, 화면에 필요한 값만
  - assets/data/krx/index.json : 종목 이름·시장과 마지막 거래일 종가·등락·시가총액·거래량
  - assets/data/krx/c/{코드}.json : 최근 1년 [날짜, 종가, 거래량]
  링크되는 종목 = 종목 등락(krx_movers) + 이평선 크로스(krx_cross) + 52주 신고가·신저가(krx_highlow) + 국내 백테스트(watchlist_kr)
"""
import datetime as dt
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_krx import KST, NotApproved, fetch, is_common_stock, num  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "tools", "state", "krx_daily")
OUT = os.path.join(ROOT, "assets", "data", "krx")
HIST_DAYS = int(os.environ.get("STOCK_HIST_DAYS", "370"))
SHOW_DAYS = 366
MARKETS = {"KOSPI": "sto/stk_bydd_trd", "KOSDAQ": "sto/ksq_bydd_trd"}
LINK_SOURCES = ["_data/krx_movers.json", "_data/krx_cross.json", "_data/krx_highlow.json",
                "_data/watchlist_kr.yml", "_data/watchlist_kr.json"]
SKIPPED = []


def safe_fetch(endpoint, day, key):
    """가끔 빈 응답(JSON 아님)이 오면 잠깐 쉬고 다시. 끝내 안 되면 그날은 건너뛴다."""
    for attempt in range(3):
        try:
            return fetch(endpoint, day.strftime("%Y%m%d"), key)
        except ValueError:
            time.sleep(5 * (attempt + 1))
    SKIPPED.append(f"{endpoint} {day}")
    return None


def day_rows(key, day):
    """그날 두 시장 보통주 {코드: (시장, 원본 행)}. 휴장이면 빈 dict, 응답 오류면 None."""
    out = {}
    for mkt, endpoint in MARKETS.items():
        rows = safe_fetch(endpoint, day, key)
        if rows is None:
            return None
        for r in rows:
            if is_common_stock(r):
                out[r["ISU_CD"]] = (mkt, r)
        time.sleep(0.2)
    return out


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def compact(rows):
    out = {}
    for c, (_, r) in rows.items():
        close = int(num(r.get("TDD_CLSPRC")) or 0)
        if close > 0:  # 거래정지 등 종가 없는 날은 뺀다
            out[c] = [close, int(num(r.get("ACC_TRDVOL")) or 0)]
    return out


def linked_codes():
    codes = set()
    for rel in LINK_SOURCES:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            text = open(p, encoding="utf-8").read()
            codes |= set(re.findall(r"""["']?code["']?\s*:\s*["'](\d{6})["']""", text))
    return codes


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        sys.exit("KRX_API_KEY 환경변수가 없습니다.")
    today = dt.datetime.now(KST).date()
    os.makedirs(STATE, exist_ok=True)
    have = {os.path.basename(p)[:-5] for p in glob.glob(os.path.join(STATE, "*.json"))}

    # 마지막 거래일(요약용 원본 행이 필요해서 매번 받는다)
    last, last_rows = None, {}
    try:
        for back in range(10):
            day = today - dt.timedelta(days=back)
            if day.weekday() < 5:
                rows = day_rows(key, day)
                if rows:
                    last, last_rows = day, rows
                    break
    except NotApproved as e:
        print(f"건너뜀 — KRX API 인증 실패({e})")
        return
    if not last:
        print("최근 거래일을 찾지 못했습니다.")
        return
    save(os.path.join(STATE, last.isoformat() + ".json"), compact(last_rows))
    have.add(last.isoformat())

    # 캐시에 없는 날 채우기(휴장일은 빈 파일로 남겨 다시 묻지 않는다)
    fetched = 0
    day = last - dt.timedelta(days=HIST_DAYS)
    while day < last:
        if day.weekday() < 5 and day.isoformat() not in have:
            rows = day_rows(key, day)
            if rows is not None:
                save(os.path.join(STATE, day.isoformat() + ".json"), compact(rows))
            fetched += 1
        day += dt.timedelta(days=1)
    for p in glob.glob(os.path.join(STATE, "*.json")):  # 오래된 날 정리
        if os.path.basename(p)[:-5] < (last - dt.timedelta(days=HIST_DAYS)).isoformat():
            os.remove(p)

    # 공개 파일: 링크되는 종목만
    codes = sorted(c for c in linked_codes() if c in last_rows)
    days = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(STATE, "*.json")))
    days = [d for d in days if d > (last - dt.timedelta(days=SHOW_DAYS)).isoformat()]
    series = {c: [] for c in codes}
    for d in days:
        with open(os.path.join(STATE, d + ".json"), encoding="utf-8") as f:
            snap = json.load(f)
        for c in codes:
            if c in snap:
                series[c].append([d] + snap[c])
    os.makedirs(os.path.join(OUT, "c"), exist_ok=True)
    for p in glob.glob(os.path.join(OUT, "c", "*.json")):
        if os.path.basename(p)[:-5] not in series:
            os.remove(p)
    for c, s in series.items():
        save(os.path.join(OUT, "c", c + ".json"), s)

    stocks = {}
    for c in codes:
        mkt, r = last_rows[c]
        stocks[c] = {
            "n": r.get("ISU_NM"), "m": mkt,
            "close": int(num(r.get("TDD_CLSPRC")) or 0), "chg": int(num(r.get("CMPPREVDD_PRC")) or 0),
            "pct": num(r.get("FLUC_RT")), "vol": int(num(r.get("ACC_TRDVOL")) or 0),
            "cap": round((num(r.get("MKTCAP")) or 0) / 1e8),  # 시가총액, 억 원
        }
    save(os.path.join(OUT, "index.json"), {"source": "한국거래소(KRX) Open API", "d": last.isoformat(),
                                           "updated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
                                           "stocks": stocks})
    if SKIPPED:
        print("빈 응답으로 건너뛴 날:", ", ".join(SKIPPED))
    print(f"{last} 기준 · 캐시 {len(days)}일(새로 받은 날 {fetched}), 공개 종목 {len(codes)}개")


if __name__ == "__main__":
    main()
