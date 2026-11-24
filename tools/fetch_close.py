"""개장 전 브리핑용 전일 종가를 개인용 Worker(desk-pvt /api/close)에서 받아 _data/briefing_quotes.json에 저장한다.

- 사용자 결정(2026-11-25): 지수·주요 종목 종가·등락률은 Yahoo Finance 값을 쓴다. GitHub Actions에서는 Yahoo가 막혀 있어
  개인용 Worker가 대신 받아 준다(Worker는 Cloudflare에서 Yahoo에 접속). 시세 화면·차트는 쓰지 않고 종가 숫자만.
- 환경 변수: DESK_PVT_URL(Worker 주소, 예: https://desk-pvt.xxx.workers.dev), DESK_CLOSE_TOKEN(Worker의 CLOSE_TOKEN과 같은 값)
- 하나도 못 받으면 파일을 바꾸지 않고 0이 아닌 값으로 끝난다. 그날 브리핑은 예약 작업이 마감 기사로 채운다
"""
import datetime as dt
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_data", "briefing_quotes.json")
KST = dt.timezone(dt.timedelta(hours=9))


def get(base, token, g):
    req = urllib.request.Request(f"{base.rstrip('/')}/api/close?g={g}",
                                 headers={"Authorization": "Bearer " + token, "User-Agent": "desk-notes-actions"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except Exception as e:  # 오류 메시지에 토큰이 섞이지 않게 종류만
        print(f"  실패 — {g}: {type(e).__name__} {getattr(e, 'code', '')}")
        return None


def main():
    base = os.environ.get("DESK_PVT_URL", "").strip()
    token = os.environ.get("DESK_CLOSE_TOKEN", "").strip()
    if not base or not token:
        print("DESK_PVT_URL·DESK_CLOSE_TOKEN이 없어 건너뜀")
        return 1
    idx, stk = get(base, token, "index"), get(base, token, "stocks")
    if not idx or not idx.get("items"):
        print("지수 종가를 못 받음 — 파일을 바꾸지 않음")
        return 1
    out = {"source": "Yahoo Finance", "updated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
           "index": idx["items"], "stocks": (stk or {}).get("items", {}),
           "failed": idx.get("failed", []) + (stk or {}).get("failed", [])}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for k, r in out["index"].items():
        print(f"  {k:6} {r['d']} {r['close']:>12,.2f} {r['pct']:+.2f}%")
    print(f"주요 종목 {len(out['stocks'])}개, 실패 {out['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
