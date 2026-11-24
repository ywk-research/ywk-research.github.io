"""국내 관심종목 백테스트. tools/fetch_krx.py가 부르고, 혼자 실행해도 된다(키 불필요, 저장된 종가만 씀).

- 종목·비중: _data/watchlist_kr.yml
- 종가: tools/state/krx_closes (krx_cross.py가 쌓는 KRX 보통주 종가, 분할·증자 보정)
- 코스피: _data/krx_index.json
- 무위험수익률(샤프지수): _data/macro.json의 CD(91일) 최근값
- 결과: _data/watchlist_kr.json
  · 기간 수익률(1주·1개월·3개월·6개월·전체): 기간 첫날 종가에 비중대로 사서 보유
  · 전체 기간 전략 비교: 그대로 보유 vs 매달 첫 거래일 비중 재조정(거래비용·세금 무시)
  · 지표: 누적 수익률, 코스피 대비 초과수익, 최대 낙폭, 연율 변동성, 샤프지수
배당은 KRX 시세에 없어 빠진다(가격 수익률).
"""
import datetime as dt
import json
import math
import os

import krx_cross

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_PATH = os.path.join(ROOT, "_data", "watchlist_kr.yml")
INDEX_PATH = os.path.join(ROOT, "_data", "krx_index.json")
MACRO_PATH = os.path.join(ROOT, "_data", "macro.json")
OUT_PATH = os.path.join(ROOT, "_data", "watchlist_kr.json")


def load_conf():
    """의존성 없이 읽는 간단한 yml 파서 (이 파일 형식 전용)"""
    stocks, cur = [], None
    with open(CONF_PATH, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip()
            s = line.strip()
            if s.startswith("- "):
                cur = {}
                stocks.append(cur)
                s = s[2:]
            if cur is not None and ":" in s:
                k, v = s.split(":", 1)
                cur[k.strip()] = v.strip().strip('"').strip("'")
    for x in stocks:
        x["weight"] = float(x.get("weight", 1))
    return stocks


def months_back(d, m):
    y, mo = d.year, d.month - m
    while mo < 1:
        mo += 12
        y -= 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1])
    return dt.date(y, mo, day)


def r2(x):
    return None if x is None else round(x, 2)


def metrics(vals, rf):
    """vals: 지수(첫날 100). 누적 수익률, 최대 낙폭, 연율 변동성, 샤프지수"""
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    n = len(rets)
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0
    vol = sd * math.sqrt(252)
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    ann = (vals[-1] / vals[0]) ** (252 / n) - 1 if n else 0
    sharpe = (mean * 252 - rf) / vol if vol else None
    return {"ret": r2((vals[-1] / vals[0] - 1) * 100), "ann": r2(ann * 100), "mdd": r2(mdd * 100),
            "vol": r2(vol * 100), "sharpe": None if sharpe is None else round(sharpe, 2)}


def build(days=None, names=None):
    conf = load_conf()
    if days is None:
        days = krx_cross._load_all()
    dates = sorted(days)
    with open(INDEX_PATH, encoding="utf-8") as f:
        kospi = {p["d"]: p["close"] for p in json.load(f).get("kospi", [])}
    dates = [d for d in dates if d in kospi]

    # 보정 종가, 거래정지 등으로 빈 날은 직전 종가로 채움
    px = {}
    for s in conf:
        adj = dict(krx_cross._adjusted(days, dates, s["code"]))
        if not adj:
            raise SystemExit(f"종가 없음: {s['code']} {s.get('name')}")
        last, col = None, []
        for d in dates:
            last = adj.get(d, last)
            col.append(last)
        px[s["code"]] = col
    start = next(i for i in range(len(dates)) if all(px[s["code"]][i] for s in conf))
    dates = dates[start:]
    for k in px:
        px[k] = px[k][start:]
    kp = [kospi[d] for d in dates]
    tw = sum(s["weight"] for s in conf)
    w = {s["code"]: s["weight"] / tw for s in conf}
    n = len(dates)

    # 그대로 보유: 첫날 비중대로 산 주식 수 고정
    hold = [100 * sum(w[c] * px[c][i] / px[c][0] for c in w) for i in range(n)]
    # 월별 리밸런싱: 매달 첫 거래일 종가에 비중을 다시 맞춤
    rebal, val, base, base_i = [], 100.0, None, 0
    for i, d in enumerate(dates):
        if i == 0 or d[:7] != dates[i - 1][:7]:
            if i:
                val = rebal[-1]
            base, base_i = val, i
        rebal.append(base * sum(w[c] * px[c][i] / px[c][base_i] for c in w))
    kidx = [100 * v / kp[0] for v in kp]

    rf = 0.0
    try:
        with open(MACRO_PATH, encoding="utf-8") as f:
            cd = json.load(f)["kr_short"]["cd"]
        rf = cd[-1][1] / 100
    except Exception:
        pass

    # 기간 수익률
    end = dt.date.fromisoformat(dates[-1])
    periods = [("1w", None), ("1m", 1), ("3m", 3), ("6m", 6), ("all", None)]
    rows = []
    for key, m in periods:
        if key == "1w":
            i0 = max(0, n - 6)
        elif key == "all":
            i0 = 0
        else:
            cut = months_back(end, m).isoformat()
            i0 = max([i for i, d in enumerate(dates) if d <= cut] or [0])
        port = sum(w[c] * (px[c][-1] / px[c][i0] - 1) for c in w) * 100
        kret = (kp[-1] / kp[i0] - 1) * 100
        rows.append({"key": key, "from": dates[i0], "port": r2(port), "kospi": r2(kret), "excess": r2(port - kret),
                     "stocks": {c: r2((px[c][-1] / px[c][i0] - 1) * 100) for c in w}})

    out = {
        "source": "한국거래소(KRX) Open API",
        "start": dates[0], "end": dates[-1], "days": n, "rf": round(rf * 100, 2),
        "stocks": [{"code": s["code"], "name": s.get("name", s["code"]),
                    "krx_name": (names or {}).get(s["code"]), "weight": round(w[s["code"]] * 100, 1),
                    "first": round(px[s["code"]][0]), "last": round(px[s["code"]][-1])} for s in conf],
        "periods": rows,
        "strategies": {
            "hold": metrics(hold, rf), "rebal": metrics(rebal, rf), "kospi": metrics(kidx, rf),
        },
        "series": {"d": dates, "hold": [r2(v) for v in hold], "rebal": [r2(v) for v in rebal], "kospi": [r2(v) for v in kidx]},
    }
    for k in ("hold", "rebal"):
        out["strategies"][k]["excess"] = r2(out["strategies"][k]["ret"] - out["strategies"]["kospi"]["ret"])
    for s in out["stocks"]:
        if s["krx_name"] and s["krx_name"].replace(" ", "").lower() != s["name"].replace(" ", "").lower():
            print(f"주의: {s['code']} 설정 이름 '{s['name']}' ≠ KRX '{s['krx_name']}'")
    return out


def update(days=None, names=None):
    out = build(days, names)
    old = None
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8") as f:
            old = json.load(f)
    if old and not out["stocks"][0]["krx_name"]:  # 혼자 실행할 때는 이전 KRX 이름을 유지
        prev = {s["code"]: s.get("krx_name") for s in old.get("stocks", [])}
        for s in out["stocks"]:
            s["krx_name"] = prev.get(s["code"])
    if out == old:
        return False
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"국내 백테스트: {out['start']}~{out['end']} {out['days']}거래일")
    return True


if __name__ == "__main__":
    update()
