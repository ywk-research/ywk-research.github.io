"""개장 전 브리핑 글(_posts/YYYY-MM-DD-pre-market-briefing.md) 초안을 _data/의 공식 자료로 만든다.

쓰는 자료 (모두 워크플로가 이미 받아 둔 것, 이 스크립트는 외부 접속을 하지 않는다)
- 코스피: KRX Open API (_data/krx_index.json)
- VIX: Cboe, 美 10년물: 미 재무부 (_data/global_index.json) — 전 거래일 자료가 이미 있을 때만
- 원/달러: 한국은행 ECOS 서울 외환시장 종가(15:30) (_data/krw_fx.json usd_close, 없으면 매매기준율)
- 국내 주요 종목: KRX (_data/krx_movers.json)
- 오늘 일정: _data/econ_events.yml(손으로 관리) + _data/econ_calendar.json(BEA 자동)
- 신규 상장 ETF: 글에는 include만 넣고, 화면은 빌드할 때 _data/new_etf_confirmed.yml·new_etf.json으로 그린다
  (05:52 확인 작업이 늦게 끝나도 Pages가 다시 빌드되면 글에 바로 반영된다)

- 미국·아시아 지수, 달러인덱스, WTI·금, 미국 주요 종목: Yahoo Finance 전일 종가(_data/briefing_quotes.json, tools/fetch_close.py가 개인용 Worker에서 받음)
그래도 비는 칸과 주요 뉴스는 "—"/표시로 남긴다.
평일 06:08 Claude 예약 작업이 이 초안을 만든 뒤 빈칸과 뉴스를 채워 올리고,
예약 작업이 실패하면 .github/workflows/briefing.yml(06:40)이 이 초안을 그대로 올린다.

사용: python tools/make_briefing.py [--date YYYY-MM-DD] [--force] [--check]
  --date  발간일(한국 날짜, 기본 오늘). --force  이미 있는 글도 덮어씀. --check  파일을 쓰지 않고 출력만
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "_data")
KST = dt.timezone(dt.timedelta(hours=9))
WD = "월화수목금토일"
MINUS = "\u2212"

# NYSE 휴장일 (해마다 추가)
US_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
}
FLAG = {"us": "🇺🇸", "kr": "🇰🇷", "jp": "🇯🇵", "cn": "🇨🇳", "eu": "🇪🇺"}
DASH = "—"


def load(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def prev_us_day(pub):
    d = pub - dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in US_HOLIDAYS:
        d -= dt.timedelta(days=1)
    return d


def md(d):
    return f"{d.month}/{d.day}"


def fmt(v, nd=2):
    s = f"{abs(v):,.{nd}f}"
    return (MINUS + s) if v < 0 else s


def span(v, txt):
    """등락 표시: 상승 up, 하락 down, 0이면 보합"""
    if v is None:
        return DASH
    if round(v, 6) == 0:
        return '<span class="flat">보합</span>'
    cls = "up" if v > 0 else "down"
    sign = "+" if v > 0 else MINUS
    return f'<span class="{cls}">{sign}{txt}</span>'


def pct(v):
    return span(v, f"{abs(v):.2f}%") if v is not None else DASH


def last_on_or_before(rows, day):
    """[{d, close, chg, pct}] 또는 [[d, v]]에서 day 이하 마지막 두 값"""
    pts = []
    for r in rows:
        if isinstance(r, dict):
            pts.append((r["d"], r.get("close")))
        else:
            pts.append((r[0], r[1]))
    pts = [p for p in pts if p[1] is not None and p[0] <= day]
    pts.sort()
    if not pts:
        return None, None, None
    d, v = pts[-1]
    prev = pts[-2][1] if len(pts) > 1 else None
    return d, v, prev


def econ_events(pub):
    """발간일 07:00 ~ 다음 날 06:59(한국시간) 일정"""
    nxt = pub + dt.timedelta(days=1)
    out = []
    p = os.path.join(DATA, "econ_events.yml")
    if os.path.exists(p):
        # 한 줄에 하나씩 { date: "...", time: "...", c: us, t: "...", ... } 형식
        pat = re.compile(r'date:\s*"([^"]+)".*?time:\s*"([^"]*)".*?c:\s*(\w+).*?t:\s*"([^"]+)"')
        with open(p, encoding="utf-8") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    out.append({"date": m.group(1), "time": m.group(2), "c": m.group(3), "t": m.group(4)})
    cal = load("econ_calendar.json") or {}
    for e in cal.get("events", []):
        out.append({k: e.get(k, "") for k in ("date", "time", "c", "t")})
    rows, seen = [], set()
    for e in out:
        key = (e["date"], e["time"], e["t"])
        if key in seen:
            continue
        seen.add(key)
        t = e["time"] or ""
        if e["date"] == pub.isoformat() and (t == "" or t >= "07:00"):
            rows.append((0, t or "99", e))
        elif e["date"] == nxt.isoformat() and t and t < "07:00":
            rows.append((1, t, e))
    rows.sort(key=lambda x: (x[0], x[1]))
    res = []
    for day, t, e in rows:
        tm = e["time"] or "시각 미정"
        if day:
            tm = f"{nxt.day}일 {tm}"
        res.append((FLAG.get(e["c"], ""), tm, e["t"]))
    return res


def build(pub, now):
    us_d = prev_us_day(pub)
    gi = load("global_index.json") or {}
    gser = {s["key"]: s for s in gi.get("series", [])}
    ki = load("krx_index.json") or {}
    fx = load("krw_fx.json") or {}
    mv = load("krx_movers.json") or {}
    yq = load("briefing_quotes.json") or {}
    yi, ys = yq.get("index", {}), yq.get("stocks", {})

    def yus(key):
        """Yahoo 전일 종가: 미국 전 거래일 D 것만"""
        r = yi.get(key)
        return r if r and r.get("d") == us_d.isoformat() else None

    def yasia(key):
        """아시아: 국내 전 거래일 이하 가장 최근(휴장이면 직전 거래일), 5일 넘게 묵은 건 버림"""
        r = yi.get(key)
        if r and (kr_d - dt.timedelta(days=5)).isoformat() <= r.get("d", "") <= kr_d.isoformat():
            return r
        return None
    missing = []

    # 국내 전 거래일 = 발간일 이전 코스피 마지막 날짜
    # 국내 전 거래일: KRX Open API는 다음 날 아침에야 전일 자료를 주므로(06시대엔 없음) ECOS 서울 종가 날짜로 정한다.
    # ECOS 종가는 당일 저녁에 들어온다. 그것도 없으면 발간일 전 평일
    closes = [r[0] for r in (fx.get("series", {}).get("usd_close") or []) if r[0] < pub.isoformat()]
    kd_fx = max(closes) if closes else None
    kd_wd = pub - dt.timedelta(days=1)
    while kd_wd.weekday() >= 5:
        kd_wd -= dt.timedelta(days=1)
    kr_d = dt.date.fromisoformat(kd_fx) if kd_fx and kd_fx >= (kd_wd - dt.timedelta(days=4)).isoformat() else kd_wd
    kospi_row = next((r for r in ki.get("kospi", []) if r["d"] == kr_d.isoformat()), None)

    # 글 날짜는 발간일. 오늘 글이면 지금 시각(빌드 시점보다 미래면 Jekyll이 글을 숨김), 아니면 06:00
    stamp = now.strftime("%H:%M:%S") if now.date() == pub else "06:00:00"
    L = []
    title = f"개장 전 브리핑 | 전일 시황 종합 ({pub:%Y.%m.%d} {WD[pub.weekday()]})"
    L += ["---", f'title: "{title}"', f"date: {pub.isoformat()} {stamp} +0900", 'summary: "SUMMARY"',
          f'us_day: "{us_d.isoformat()}"', f'kr_day: "{kr_d.isoformat()}"', "auto: true", "---", ""]

    # 0. 신규 상장 ETF (1순위)
    L += ["## 전 거래일 신규 상장 ETF", "",
          "{% include briefing-etf.html us=page.us_day kr=page.kr_day %}", ""]

    # 1. 미국 증시
    L += [f"## 미국 증시 ({md(us_d)} 마감)", "", "| 지표 | 종가 | 등락률 |", "|---|--:|--:|"]
    for key, name in [("spx", "S&P500"), ("ndx", "나스닥"), ("dji", "다우"), ("sox", "필라델피아 반도체(SOX)")]:
        r = yus(key)
        if r:
            L.append(f"| {name} | {fmt(r['close'])} | {pct(r['pct'])} |")
        else:
            L.append(f"| {name} | {DASH} | {DASH} |")
            missing.append(f"미국 {name}")
    vix = gser.get("vix")
    vrow = next((r for r in (vix or {}).get("data", []) if r["d"] == us_d.isoformat()), None) or yus("vix")
    if vrow:
        L.append(f"| VIX | {vrow['close']:.2f} | {pct(vrow.get('pct'))} |")
    else:
        L.append(f"| VIX | {DASH} | {DASH} |")
        missing.append("VIX")
    L.append("")

    # 2. 아시아 증시
    L += ["## 아시아 증시", "", "| 지표 | 기준일 | 종가 | 등락률 |", "|---|---|--:|--:|"]
    if not kospi_row:
        r = yasia("kospi")
        kospi_row = r if r and r["d"] == kr_d.isoformat() else None
    if kospi_row:
        L.append(f"| 코스피 | {md(kr_d)} | {fmt(kospi_row['close'])} | {pct(kospi_row.get('pct'))} |")
    else:
        L.append(f"| 코스피 | {md(kr_d)} | {DASH} | {DASH} |")
        missing.append("코스피")
    closed = []
    for key, name in [("n225", "닛케이225"), ("sse", "상하이종합"), ("hsi", "항셍")]:
        r = yasia(key)
        if r:
            rd = dt.date.fromisoformat(r["d"])
            L.append(f"| {name} | {md(rd)} | {fmt(r['close'])} | {pct(r['pct'])} |")
            if rd != kr_d:
                closed.append(f"{name.rstrip('225')}({md(rd)})")
        else:
            L.append(f"| {name} | {md(kr_d)} | {DASH} | {DASH} |")
            missing.append(name)
    L.append("")
    if closed:
        L += [f'<p class="note">※ {", ".join(closed)}은 {md(kr_d)}에 휴장해 직전 거래일 종가를 적었어요.</p>', ""]

    # 3. 금리·환율·원자재
    L += [f"## 금리 · 환율 · 원자재 ({md(us_d)})", "", "| 지표 | 수준 | 변동 |", "|---|--:|--:|"]
    tn = gser.get("us10y")
    trow = next((r for r in (tn or {}).get("data", []) if r["d"] == us_d.isoformat()), None)
    if trow:
        bp = round((trow.get("chg") or 0) * 100)
        L.append(f"| 미국 10년물 국채금리 | {trow['close']:.2f}% | {span(bp, f'{abs(bp)}bp')} |")
    elif yus("tnx"):
        r = yus("tnx")
        bp = round(r["chg"] * 100)
        L.append(f"| 미국 10년물 국채금리 | {r['close']:.2f}% | {span(bp, f'{abs(bp)}bp')} |")
    else:
        L.append(f"| 미국 10년물 국채금리 | {DASH} | {DASH} |")
        missing.append("미국 10년물")
    r = yus("dxy")
    if r:
        L.append(f"| 달러인덱스 | {r['close']:.2f} | {pct(r['pct'])} |")
    else:
        L.append(f"| 달러인덱스 | {DASH} | {DASH} |")
        missing.append("달러인덱스")
    s = fx.get("series", {})
    close = s.get("usd_close")
    label = "원/달러 (서울 종가)"
    if not close:  # 종가가 없으면 매매기준율(다음 날 아침 고시분이 전일 시장평균)로 대신
        close, label = s.get("usd"), "원/달러 (매매기준율)"
    fx_day = kr_d.isoformat() if close is s.get("usd_close") else pub.isoformat()
    d, v, pv = last_on_or_before(close or [], fx_day)
    if d == fx_day:
        chg = None if pv is None else round(v - pv, 1)
        L.append(f"| {label} | {v:,.1f}원 | {span(chg, f'{abs(chg):.1f}원') if chg is not None else DASH} |")
    else:
        L.append(f"| 원/달러 (서울 종가) | {DASH} | {DASH} |")
        missing.append("원/달러")
    for key, name in [("wti", "WTI (근월물)"), ("gold", "금 (근월물)")]:
        r = yus(key)
        if r:
            L.append(f"| {name} | ${r['close']:,.2f} | {pct(r['pct'])} |")
        else:
            L.append(f"| {name} | {DASH} | {DASH} |")
            missing.append(name)
    L.append("")

    # 4. 주요 종목 (국내는 KRX 자료로 미리 채움)
    L += ["## 주요 종목", ""]
    if mv.get("d") == kr_d.isoformat() and mv.get("kospi"):
        k = mv["kospi"]
        val = ", ".join(f"{x['name']} {pct(x['pct'])}" for x in k.get("value", [])[:3])
        if val:
            L.append(f"- **코스피 거래대금 상위:** {val}")
        g = k.get("gainers", [])[:1]
        lo = k.get("losers", [])[:1]
        if g:
            L.append(f"- **코스피 상승 1위(시총 1조↑):** {g[0]['name']} {pct(g[0]['pct'])}")
        if lo:
            L.append(f"- **코스피 하락 1위(시총 1조↑):** {lo[0]['name']} {pct(lo[0]['pct'])}")
    # 주요 종목: Yahoo 종가로 등락률·종가를 미리 넣는다(이유는 예약 작업이 덧붙임)
    # 미국은 D에 가장 많이 움직인 4개, 국내 대형주(.KS)는 K에 가장 많이 움직인 2개
    isk = lambda r: str(r.get("symbol", "")).endswith(".KS")
    us_mv = sorted((r for r in ys.values() if not isk(r) and r.get("d") == us_d.isoformat()), key=lambda r: -abs(r["pct"]))[:4]
    kr_mv = sorted((r for r in ys.values() if isk(r) and r.get("d") == kr_d.isoformat()), key=lambda r: -abs(r["pct"]))[:2]
    for r in us_mv:
        L.append(f"- **{r['name']} {pct(r['pct'])} (${r['close']:,.2f})**")
    for r in kr_mv:
        L.append(f"- **{r['name']} {pct(r['pct'])} ({r['close']:,.0f}원)**")
    if not us_mv:
        missing.append("미국 주요 종목")
    L.append("<!-- US_STOCKS -->")
    L.append("")

    # 5. 뉴스 (예약 작업이 채움. 비어 있으면 섹션째 빠짐)
    L += ["<!-- NEWS -->", ""]

    # 6. 오늘 일정
    ev = econ_events(pub)
    L += ["## 오늘 일정 (한국시간)", ""]
    if ev:
        L += ["| 시간 | 일정 |", "|---|---|"]
        L += [f"| {f} {t} | {name} |" for f, t, name in ev]
    else:
        L.append("오늘은 큰 발표 일정이 없어요.")
    L.append("")
    if pub.isoformat() in US_HOLIDAYS:
        L += [f"> 미국 증시는 {pub.day}일(현지) 휴장해요.", ""]

    srcs = "한국거래소(KRX) Open API, 한국은행 ECOS, Cboe, 미 재무부, SEC EDGAR" + (", Yahoo Finance(지수·종목 종가)" if yi else "")
    L.append(f'<p class="note">자료: {srcs} 및 각 거래소·언론 보도 종합. 정보 정리 목적이며 투자 권유가 아닙니다.</p>')
    L.append("")

    # 요약(예약 작업이 다시 씀)
    if yus("spx") and yus("ndx"):
        summ = f"S&P500 {fmt(yus('spx')['pct'])}%·나스닥 {fmt(yus('ndx')['pct'])}%, 전 거래일 시장 정리"
    elif kospi_row:
        summ = f"코스피 {fmt(kospi_row['pct'])}%, 전 거래일 시장 데이터 정리"
    else:
        summ = "전 거래일 시장 데이터 정리"
    text = "\n".join(L).replace("SUMMARY", summ)
    return text, missing, us_d, kr_d


def prune(text, note=True):
    """바로 올릴 때: 값이 없는("—") 표 줄과 빈 섹션을 뺀다. note면 안내 한 줄을 붙인다(뉴스 없이 올릴 때)"""
    out = []
    for line in text.split("\n"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("| ") else []
        if cells and len(cells) >= 3 and cells[-2] == DASH:
            continue
        out.append(line)
    # 머리줄·구분줄만 남은 표, 내용 없는 섹션 제거
    text = "\n".join(out)
    text = re.sub(r"\| 지표 \|[^\n]*\n\|[-:| ]+\|\n(?=\n)", "", text)
    text = re.sub(r"## [^\n]+\n\n(<!-- [A-Z_]+ -->\n)?\n(?=## |<!--|<p)", "", text)
    if not note:
        return text
    note = ('<p class="note">※ 자동 정리 단계가 시간 안에 끝나지 않아 공식 자료로 받은 수치만 먼저 올렸어요. '
            '미국·아시아 지수와 뉴스는 확인되는 대로 추가해요.</p>\n\n')
    return text.replace("## 전 거래일 신규 상장 ETF", note + "## 전 거래일 신규 상장 ETF", 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--prune", action="store_true", help="빈 항목을 빼고 바로 올릴 수 있는 형태로(예비 발간용)")
    ap.add_argument("--no-note", action="store_true", help="--prune 때 '자동 정리 단계…' 안내를 붙이지 않음(Gemini가 뉴스를 채울 때)")
    a = ap.parse_args()
    now = dt.datetime.now(KST)
    pub = dt.date.fromisoformat(a.date) if a.date else now.date()
    text, missing, us_d, kr_d = build(pub, now)
    if a.prune:
        text = prune(text, note=not a.no_note)
    path = os.path.join(ROOT, "_posts", f"{pub.isoformat()}-pre-market-briefing.md")
    print(f"발간일 {pub} · 미국 전 거래일 {us_d} · 국내 전 거래일 {kr_d}")
    print("채우지 못한 항목: " + (", ".join(missing) or "없음"))
    if a.check:
        print(text)
        return
    if os.path.exists(path) and not a.force:
        print(f"이미 있음: {os.path.relpath(path, ROOT)} (덮어쓰려면 --force)")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"작성: {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
