"""52주 신고가·신저가 종목 수 추이. tools/fetch_krx.py가 부른다.

- 과거 종가는 이평선 크로스와 같은 tools/state/krx_closes/ 를 쓴다(krx_cross.sync가 관리, 보정비율 반영).
- 종가 기준: 그날 보정 종가가 직전 WINDOW-1거래일 종가의 최고(최저)보다 높으면(낮으면) 신고가(신저가).
  장중 고가·저가가 아니라 종가로 판단하므로 증권사 HTS의 52주 신고가 수와 다를 수 있다.
- 시장 구분은 최근 거래일 종목 목록(코스피·코스닥 보통주) 기준. 상장한 지 WINDOW거래일이 안 된 종목은 뺀다.
- 결과: _data/krx_highlow.json — 최근 SHOW거래일의 일별 개수 + 최근 거래일 신고가·신저가 종목(시가총액 상위)
"""
import os

import krx_cross

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "_data", "krx_highlow.json")
WINDOW = 250   # 52주 ≈ 250거래일
SHOW = 60      # 추이로 보여 줄 거래일 수 (약 3개월)
TOP = 10       # 최근 거래일 종목 목록 개수
NEED = WINDOW + SHOW - 1  # 필요한 저장 거래일 수


def build(days, universe, latest):
    """universe: {code: {name, mkt, cap}} (최근 거래일 보통주)"""
    dates = sorted(days)
    show = dates[-SHOW:]
    pos = {d: i for i, d in enumerate(dates)}
    counts = {d: {"kospi": [0, 0], "kosdaq": [0, 0]} for d in show}
    names = {"high": [], "low": []}
    eligible = {"kospi": 0, "kosdaq": 0}
    for code, info in universe.items():
        ser = krx_cross._adjusted(days, dates, code)
        if len(ser) < WINDOW:
            continue
        mkt = info["mkt"]
        eligible[mkt] += 1
        idx = {d: i for i, (d, _) in enumerate(ser)}
        vals = [v for _, v in ser]
        for d in show:
            i = idx.get(d)
            if i is None or i < WINDOW - 1:
                continue
            prev = vals[i - WINDOW + 1:i]
            v = vals[i]
            if v > max(prev):
                counts[d][mkt][0] += 1
                if d == show[-1]:
                    names["high"].append((info.get("cap") or 0, info["name"], mkt, code))
            elif v < min(prev):
                counts[d][mkt][1] += 1
                if d == show[-1]:
                    names["low"].append((info.get("cap") or 0, info["name"], mkt, code))
    out = {
        "source": "한국거래소(KRX) Open API", "d": latest.isoformat(), "window": WINDOW,
        "stored": len(dates), "eligible": eligible,
        "series": [{"d": d, "kospi_hi": counts[d]["kospi"][0], "kospi_lo": counts[d]["kospi"][1],
                    "kosdaq_hi": counts[d]["kosdaq"][0], "kosdaq_lo": counts[d]["kosdaq"][1]} for d in show],
    }
    for k in ("high", "low"):
        top = sorted(names[k], reverse=True)[:TOP]
        out[k] = [{"name": n, "mkt": m, "code": c} for _, n, m, c in top]  # code: 종목 상세 링크
        out[k + "_total"] = len(names[k])
    return out
