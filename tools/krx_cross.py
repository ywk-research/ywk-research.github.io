"""이동평균선 골든크로스·데드크로스 종목을 추린다. tools/fetch_krx.py가 부른다.

- 과거 종가: tools/state/krx_closes/YYYY-MM-DD.json (빌드 제외 폴더, 날짜별 파일)
  {"c": {종목코드: 종가}, "a": {종목코드: 보정비율}} — 보통주만, 거래량 0(거래정지)은 뺀다.
  KRX 일별 종가는 수정주가가 아니어서, 그날 기준가(종가 − 전일 대비)가 전날 저장 종가와 다르면
  (액면분할·무상증자 등) 그 비율을 "a"에 적어 두고, 계산할 때 그 전 종가에 곱해 보정한다.
- 처음 실행하거나 빠진 날이 있으면 KEEP거래일이 찰 때까지 과거를 채우고, 넘치는 오래된 파일은 지운다.
- 결과: _data/krx_cross.json (최근 LOOKBACK거래일 안에 크로스가 난 종목)
"""
import datetime as dt
import glob
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "tools", "state", "krx_closes")
OUT_PATH = os.path.join(ROOT, "_data", "krx_cross.json")

PAIRS = [(50, 200), (20, 60)]
KEEP = 205        # 저장할 거래일 수 (200일선 + 크로스 판정 여유)
LOOKBACK = 5      # 최근 몇 거래일 안의 크로스를 보여 줄지
MAX_BACKFILL_CAL_DAYS = 330  # 과거 채우기 한도(달력일)


def _num(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _path(day):
    return os.path.join(STATE_DIR, day.isoformat() + ".json")


def _load_all():
    days = {}
    for p in sorted(glob.glob(os.path.join(STATE_DIR, "*.json"))):
        d = os.path.basename(p)[:-5]
        try:
            dt.date.fromisoformat(d)
        except ValueError:
            continue
        with open(p, encoding="utf-8") as f:
            days[d] = json.load(f)
    return days


def _to_day(rows_by_mkt):
    """KRX 응답(시장별 행) → {"c": 종가, "b": 기준가} (보통주, 거래된 종목만)"""
    c, b = {}, {}
    for rows in rows_by_mkt.values():
        for r in rows:
            close = _num(r.get("TDD_CLSPRC"))
            if not close or close <= 0 or not (_num(r.get("ACC_TRDVOL")) or 0):
                continue
            code = r.get("ISU_CD", "")
            c[code] = close
            chg = _num(r.get("CMPPREVDD_PRC"))
            if chg is not None:
                b[code] = close - chg
    return {"c": c, "b": b}


def _write_day(day, raw, prev_close):
    """기준가와 직전 저장 종가를 비교해 보정비율을 붙이고 저장한다."""
    a = {}
    for code, base in raw["b"].items():
        p = prev_close.get(code)
        if p and base > 0 and abs(base / p - 1) > 0.005:
            a[code] = round(base / p, 6)
    obj = {"c": {k: raw["c"][k] for k in sorted(raw["c"])}, "a": {k: a[k] for k in sorted(a)}}
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(day), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    return obj


def sync(fetch_day, latest, latest_rows):
    """latest(오늘 기준 최근 거래일)까지 저장분을 맞춘다. fetch_day(day) → 시장별 행 dict, 휴장이면 빈 dict."""
    days = _load_all()
    raws = {}  # 이번 실행에서 받은 날: 기준가가 있어 보정비율을 다시 계산할 수 있다
    calls = 0

    def take(day, rows):
        raw = _to_day(rows)
        if raw["c"]:
            raws[day.isoformat()] = raw

    # 1) 마지막 저장일 다음 날 ~ latest 사이 빠진 날 (latest는 이미 받은 응답 사용)
    if days:
        day = dt.date.fromisoformat(max(days)) + dt.timedelta(days=1)
        while day < latest:
            if day.weekday() < 5:
                take(day, fetch_day(day)); calls += 2
            day += dt.timedelta(days=1)
    take(latest, latest_rows)

    # 2) 저장일이 KEEP보다 적으면 과거로 채우기
    have = set(days) | set(raws)
    if have and len(have) < KEEP:
        earliest = dt.date.fromisoformat(min(have))
        day = earliest - dt.timedelta(days=1)
        while len(have) < KEEP and (earliest - day).days <= MAX_BACKFILL_CAL_DAYS:
            if day.weekday() < 5:
                take(day, fetch_day(day)); calls += 2
                have = set(days) | set(raws)
            day -= dt.timedelta(days=1)

    # 새로 받은 날을 오래된 순으로 저장 (직전 종가와 기준가를 비교해 보정비율 계산)
    prev = {}
    for d in sorted(set(days) | set(raws)):
        if d in raws:
            days[d] = _write_day(dt.date.fromisoformat(d), raws[d], prev)
        prev.update(days[d]["c"])

    # 3) 오래된 파일 정리
    for d in sorted(days)[:-KEEP]:
        os.remove(_path(dt.date.fromisoformat(d)))
        del days[d]
    print(f"이평선 크로스: 저장 {len(days)}거래일, 추가 호출 {calls}회")
    return days


def _adjusted(days, dates, code):
    """보정 종가 [(날짜, 종가)] 오래된 순"""
    out, factor = [], 1.0
    for d in reversed(dates):
        day = days[d]
        c = day["c"].get(code)
        if c is not None:
            out.append((d, c * factor))
            r = day.get("a", {}).get(code)
            if r:
                factor *= r
    out.reverse()
    return out


def _ma(vals, n, end):
    return sum(vals[end - n + 1:end + 1]) / n


def _won_txt(eok):
    jo, rest = divmod(int(eok), 10000)
    return ((f"{jo}조 " if jo else "") + (f"{rest:,}억" if rest or not jo else "")).strip()


def build(days, universe, latest):
    """universe: {code: {name, mkt, cap, pct}} (순위 조건을 통과한 종목, latest 기준)"""
    dates = sorted(days)
    recent = dates[-LOOKBACK:]
    out = {"source": "한국거래소(KRX) Open API", "d": latest.isoformat(), "days": recent,
           "lookback": LOOKBACK, "stocks": len(universe), "pairs": []}
    series = {code: _adjusted(days, dates, code) for code in universe}
    for s, l in PAIRS:
        res = {"short": s, "long": l, "golden": [], "dead": [], "skipped": 0}
        for code, info in universe.items():
            ser = series[code]
            if len(ser) < l + 1:
                res["skipped"] += 1
                continue
            vals = [v for _, v in ser]
            n = len(vals)
            for i in range(n - 1, max(l, n - LOOKBACK - 1) - 1, -1):
                if ser[i][0] not in recent:
                    break
                ps, pl = _ma(vals, s, i - 1), _ma(vals, l, i - 1)
                cs, cl = _ma(vals, s, i), _ma(vals, l, i)
                kind = "golden" if ps <= pl and cs > cl else "dead" if ps >= pl and cs < cl else None
                if kind:
                    ms, ml = _ma(vals, s, n - 1), _ma(vals, l, n - 1)
                    res[kind].append({
                        "name": info["name"], "code": code, "mkt": info["mkt"], "d": ser[i][0],
                        "close": days[dates[-1]]["c"].get(code), "pct": info["pct"],
                        "gap": round((ms / ml - 1) * 100, 2), "cap": info["cap"],
                        "cap_txt": _won_txt(info["cap"] / 1e8),
                    })
                    break
        for kind in ("golden", "dead"):
            res[kind].sort(key=lambda x: (x["d"], x["cap"]), reverse=True)
        out["pairs"].append(res)
        print(f"이평선 {s}/{l}: 골든 {len(res['golden'])}, 데드 {len(res['dead'])} (데이터 부족 {res['skipped']})")
    return out
