"""종목 상세 탭(/stock/)의 국내 종목 일별 시세를 KRX Open API에서 받아 assets/data/krx/에 저장한다.

- 인증키: 환경변수 KRX_API_KEY. 키는 출력하지 않는다.
- 대상 종목(universe): 보통주 중 코스피 시가총액 1조 원 이상, 코스닥 3천억 원 이상 + 그날 거래대금 상위.
  한 번 들어온 종목은 상장폐지(시세 없음)될 때까지 남긴다.
- 파일
  - index.json   : 종목 목록과 마지막 거래일 요약(이름, 시장, 시가총액, 종가, 등락률 등)
  - recent.json  : 이번 달 일별 시세 {코드: [[날짜, 시가, 고가, 저가, 종가, 거래량], ...]} (매일 바뀌는 파일은 이것 하나)
  - h/{코드}.json: 지난달까지의 일별 시세(최근 HIST_DAYS일). 달이 바뀔 때 recent에서 옮겨 적는다
  이렇게 나눠서 매일 커밋이 수백 개 파일을 건드리지 않게 한다.
- KRX API는 하루치 전 종목을 주므로, 새 종목이 생기면 지난 HIST_DAYS일을 하루씩 다시 받아 채운다.
"""
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_krx import KST, NotApproved, fetch, is_common_stock, num  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "data", "krx")
HIST = os.path.join(OUT, "h")
HIST_DAYS = int(os.environ.get("STOCK_HIST_DAYS", "380"))
MARKETS = {
    # 시장: (엔드포인트, 최소 시가총액(원))
    "KOSPI": ("sto/stk_bydd_trd", 1_000_000_000_000),
    "KOSDAQ": ("sto/ksq_bydd_trd", 300_000_000_000),
}
VALUE_TOP = 10  # 시가총액이 작아도 거래대금 상위면 포함


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


SKIPPED = []


def safe_fetch(endpoint, day, key):
    """가끔 빈 응답(JSON 아님)이 오면 잠깐 쉬고 다시. 끝내 안 되면 그날은 건너뛴다."""
    for attempt in range(3):
        try:
            return fetch(endpoint, day.strftime("%Y%m%d"), key)
        except ValueError:
            time.sleep(5 * (attempt + 1))
    SKIPPED.append(f"{endpoint} {day}")
    return []


def day_rows(key, day):
    """그날 두 시장 보통주 시세 {코드: (시장, 원본 행)}. 휴장이면 빈 dict."""
    out = {}
    for mkt, (endpoint, _) in MARKETS.items():
        for r in safe_fetch(endpoint, day, key):
            if is_common_stock(r):  # 거래정지(종가 0)도 목록에는 남긴다
                out[r["ISU_CD"]] = (mkt, r)
        time.sleep(0.2)
    return out


def bar(day, r):
    i = lambda k: int(num(r.get(k)) or 0)
    return [day.isoformat(), i("TDD_OPNPRC"), i("TDD_HGPRC"), i("TDD_LWPRC"), i("TDD_CLSPRC"), i("ACC_TRDVOL")]


def last_trading_day(key, today):
    for back in range(10):
        day = today - dt.timedelta(days=back)
        if day.weekday() < 5:
            rows = day_rows(key, day)
            if rows:
                return day, rows
    return None, {}


def pick_universe(rows):
    keep = set()
    for mkt, (_, min_cap) in MARKETS.items():
        mine = [(c, r) for c, (m, r) in rows.items() if m == mkt]
        keep |= {c for c, r in mine if (num(r.get("MKTCAP")) or 0) >= min_cap}
        by_val = sorted(mine, key=lambda x: num(x[1].get("ACC_TRDVAL")) or 0, reverse=True)
        keep |= {c for c, _ in by_val[:VALUE_TOP]}
    return keep


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        sys.exit("KRX_API_KEY 환경변수가 없습니다.")
    today = dt.datetime.now(KST).date()
    try:
        last, rows = last_trading_day(key, today)
    except NotApproved as e:
        print(f"건너뜀 — KRX API 인증 실패({e})")
        return
    if not last:
        print("최근 거래일을 찾지 못했습니다.")
        return

    index = load(os.path.join(OUT, "index.json"), {"stocks": {}})
    recent = load(os.path.join(OUT, "recent.json"), {})
    month0 = last.replace(day=1)

    # 상장폐지 등으로 시세가 없는 종목은 뺀다
    universe = {c for c in index["stocks"] if c in rows}
    for c in set(index["stocks"]) - universe:
        recent.pop(c, None)
        p = os.path.join(HIST, c + ".json")
        if os.path.exists(p):
            os.remove(p)
    new = pick_universe(rows) - universe
    universe |= new

    # 받아야 할 날: 이번 달 recent에 없는 날 + (새 종목이 있으면) 지난 HIST_DAYS일 전부
    have = {b[0] for bars in recent.values() for b in bars}
    start = (last - dt.timedelta(days=HIST_DAYS)) if new else month0
    if not new and have:  # 며칠 못 돌았으면 지난달 남은 날부터
        start = min(start, dt.date.fromisoformat(max(have)) + dt.timedelta(days=1))
    new_hist = {c: [] for c in new}
    day = start
    fetched = 0
    while day <= last:
        if not new and day.isoformat() in have:
            day += dt.timedelta(days=1)
            continue
        if day.weekday() < 5:
            rs = rows if day == last else day_rows(key, day)
            fetched += 1
            for c, (_, r) in rs.items():
                if c not in universe:
                    continue
                b = bar(day, r)
                if not b[4]:  # 거래정지 등 종가 없는 날
                    continue
                if c in new_hist and day < month0:
                    new_hist[c].append(b)
                elif c in new or b[0] not in {x[0] for x in recent.get(c, [])}:
                    recent.setdefault(c, []).append(b)
        day += dt.timedelta(days=1)
    for c in recent:
        recent[c].sort(key=lambda b: b[0])

    # 새 종목 지난 시세 파일
    for c, bars in new_hist.items():
        save(os.path.join(HIST, c + ".json"), bars)

    # 달이 바뀌었으면 지난달 시세를 h/로 옮기고 오래된 날은 자른다
    cut = (last - dt.timedelta(days=HIST_DAYS)).isoformat()
    moved = 0
    for c, bars in list(recent.items()):
        old = [b for b in bars if b[0] < month0.isoformat()]
        if not old:
            continue
        p = os.path.join(HIST, c + ".json")
        hist = load(p, [])
        seen = {b[0] for b in hist}
        hist = [b for b in hist + [b for b in old if b[0] not in seen] if b[0] >= cut]
        hist.sort(key=lambda b: b[0])
        save(p, hist)
        recent[c] = [b for b in bars if b[0] >= month0.isoformat()]
        moved += 1

    # 종목 목록 + 마지막 거래일 요약
    stocks = {}
    for c in sorted(universe):
        mkt, r = rows[c]
        stocks[c] = {
            "n": r.get("ISU_NM"), "m": mkt, "sect": r.get("SECT_TP_NM") or "",
            "close": int(num(r.get("TDD_CLSPRC")) or 0), "chg": int(num(r.get("CMPPREVDD_PRC")) or 0),
            "pct": num(r.get("FLUC_RT")), "vol": int(num(r.get("ACC_TRDVOL")) or 0),
            "val": round((num(r.get("ACC_TRDVAL")) or 0) / 1e8),  # 거래대금, 억 원
            "cap": round((num(r.get("MKTCAP")) or 0) / 1e8),  # 시가총액, 억 원
            "sh": int(num(r.get("LIST_SHRS")) or 0),
        }
    index = {"source": "한국거래소(KRX) Open API", "d": last.isoformat(),
             "updated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), "stocks": stocks}
    old_index = load(os.path.join(OUT, "index.json"), {})
    if {k: v for k, v in old_index.items() if k != "updated_at"} != {k: v for k, v in index.items() if k != "updated_at"}:
        save(os.path.join(OUT, "index.json"), index)
    save(os.path.join(OUT, "recent.json"), {c: recent[c] for c in sorted(recent) if c in universe})
    if SKIPPED:
        print("빈 응답으로 건너뛴 날:", ", ".join(SKIPPED))
    print(f"{last} 기준 · 종목 {len(universe)}개(새 종목 {len(new)}), 받은 날 {fetched}일, 지난달 이관 {moved}개")


if __name__ == "__main__":
    main()
