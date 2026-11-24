"""DART(금융감독원 전자공시) Open API로 국내 종목의 최근 공시 목록을 받아 assets/data/dart/recent.json에 저장한다.

- 인증키: 환경변수 DART_API_KEY (GitHub Secrets). 키는 출력하지 않는다. 없으면 건너뛴다.
- 대상: 종목 상세 탭이 지원하는 국내 종목(assets/data/krx/index.json)만 공개 파일에 쓴다.
  전 종목 목록은 tools/state/dart_all.json(Actions 캐시)에 두어, 새로 링크된 종목도 지난 공시가 바로 보이게 한다.
- 공시검색(list.json)을 회사 지정 없이 날짜로 받아(코스피 Y, 코스닥 K) 종목코드별로 나눈다.
  처음엔 최근 DAYS일, 이후엔 마지막으로 받은 날부터 다시 받는다(같은 접수번호는 한 번만).
- 종목별 최근 KEEP건, DAYS일 이내만 남긴다.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "data", "dart", "recent.json")
STATE = os.path.join(ROOT, "tools", "state", "dart_all.json")
UNIVERSE = os.path.join(ROOT, "assets", "data", "krx", "index.json")
KST = dt.timezone(dt.timedelta(hours=9))
DAYS = 90
KEEP = 20


def get(params):
    url = "https://opendart.fss.or.kr/api/list.json?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def main():
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("DART_API_KEY가 없어 공시 수집을 건너뜁니다.")
        return
    codes = set(load(UNIVERSE, {}).get("stocks", {}))
    if not codes:
        print("국내 종목 목록(index.json)이 없어 건너뜁니다.")
        return
    today = dt.datetime.now(KST).date()
    data = load(STATE, {"by_code": {}})
    by = data.get("by_code", {})
    start = dt.date.fromisoformat(data["last"]) if data.get("last") else today - dt.timedelta(days=DAYS - 1)
    start = max(start, today - dt.timedelta(days=DAYS - 1))  # 회사 미지정 검색은 3개월까지

    seen = {r[2] for rows in by.values() for r in rows}
    added = 0
    for cls in ("Y", "K"):
        page = 1
        while True:
            res = get({"crtfc_key": key, "bgn_de": start.strftime("%Y%m%d"), "end_de": today.strftime("%Y%m%d"),
                       "corp_cls": cls, "page_no": page, "page_count": 100, "sort": "date", "sort_mth": "desc"})
            st = res.get("status")
            if st == "013":  # 조회된 데이터 없음
                break
            if st != "000":
                sys.exit(f"DART 오류 {st}: {res.get('message')}")
            for r in res.get("list", []):
                c = (r.get("stock_code") or "").strip()
                if not c or r["rcept_no"] in seen:
                    continue
                d = r["rcept_dt"]
                by.setdefault(c, []).append([f"{d[:4]}-{d[4:6]}-{d[6:]}", r["report_nm"].strip(), r["rcept_no"],
                                             r.get("flr_nm", ""), r.get("rm", "")])
                seen.add(r["rcept_no"])
                added += 1
            if page >= int(res.get("total_page") or 1):
                break
            page += 1
            time.sleep(0.15)

    cut = (today - dt.timedelta(days=DAYS)).isoformat()
    keep = {}
    for c in by:
        rows = sorted((r for r in by[c] if r[0] >= cut), key=lambda r: (r[0], r[2]), reverse=True)[:KEEP]
        if rows:
            keep[c] = rows
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"last": today.isoformat(), "by_code": keep}, f, ensure_ascii=False, separators=(",", ":"))
    out = {c: keep[c] for c in sorted(keep) if c in codes}
    data = {"source": "금융감독원 전자공시시스템(DART) Open API",
            "updated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), "by_code": out}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"공시 {start}~{today}: 새로 {added}건, 종목 {len(out)}개")


if __name__ == "__main__":
    main()
