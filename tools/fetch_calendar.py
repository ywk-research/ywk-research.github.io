"""미국 주요 지표 발표 일정을 공식 캘린더(.ics)에서 받아 _data/econ_calendar.json에 저장한다.

- 미 노동통계국(BLS): 고용보고서, CPI, PPI, JOLTS, 고용비용지수
- 미 경제분석국(BEA): GDP, 개인소득·지출(PCE)
시간은 한국시간(KST)으로 바꿔 저장한다. 한 소스가 실패하면 그 소스의 기존 일정을 유지한다.
중앙은행 회의 일정은 _data/econ_events.yml에서 손으로 관리한다.
"""
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, "_data", "econ_calendar.json")
KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
UA = {"User-Agent": "ywk-research.github.io economic calendar (+https://ywk-research.github.io/data/)", "Accept": "text/calendar,*/*"}

SOURCES = {
    "bls": "https://www.bls.gov/schedule/news_release/bls.ics",
    "bea": "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics",
}
# (소스, 제목에 들어 있는 말, 표시 이름, 달력 칸에 쓰는 짧은 이름)
RULES = [
    ("bls", "Employment Situation", "고용보고서(비농업 고용·실업률)", "고용"),
    ("bls", "Consumer Price Index", "소비자물가(CPI)", "CPI"),
    ("bls", "Producer Price Index", "생산자물가(PPI)", "PPI"),
    ("bls", "Job Openings and Labor Turnover", "구인·이직 보고서(JOLTS)", "JOLTS"),
    ("bls", "Employment Cost Index", "고용비용지수(ECI)", "ECI"),
    ("bea", "GDP", "GDP", "GDP"),
    ("bea", "Gross Domestic Product", "GDP", "GDP"),
    ("bea", "Personal Income and Outlays", "개인소득·지출(PCE 물가)", "PCE"),
]


def get(url):
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def parse_ics(text):
    text = re.sub(r"\r?\n[ \t]", "", text)  # 접힌 줄 펴기
    events, cur = [], None
    for line in text.splitlines():
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            k, v = line.split(":", 1)
            name, *params = k.split(";")
            cur[name] = (v, dict(p.split("=", 1) for p in params if "=" in p))
    return events


def start_kst(ev):
    v, p = ev["DTSTART"]
    if p.get("VALUE") == "DATE" or len(v) == 8:
        return dt.datetime.strptime(v[:8], "%Y%m%d").strftime("%Y-%m-%d"), ""
    if v.endswith("Z"):
        t = dt.datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    else:
        tz = ZoneInfo(p["TZID"]) if p.get("TZID") else ET
        t = dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S").replace(tzinfo=tz)
    t = t.astimezone(KST)
    return t.strftime("%Y-%m-%d"), t.strftime("%H:%M")


def main():
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            old = json.load(f)
    except (FileNotFoundError, ValueError):
        old = {"events": []}
    today = dt.datetime.now(KST).date()
    lo = (today - dt.timedelta(days=62)).isoformat()
    events, ok = [], []
    for src, url in SOURCES.items():
        try:
            items = parse_ics(get(url))
        except Exception as e:  # 실패하면 이전 일정 유지
            print(f"{src}: 실패 ({type(e).__name__}) — 이전 일정 유지")
            events += [e for e in old.get("events", []) if e.get("src") == src]
            continue
        n = 0
        for ev in items:
            summary = ev.get("SUMMARY", ("", {}))[0].replace("\\,", ",")
            for s, key, label, short in RULES:
                if s == src and key.lower() in summary.lower():
                    date, hm = start_kst(ev)
                    if date >= lo:
                        events.append({"date": date, "time": hm, "c": "us", "t": label, "s": short, "src": src})
                        n += 1
                    break
        print(f"{src}: 일정 {n}건 (전체 {len(items)}건 중)")
        ok.append(src)
    seen, uniq = set(), []
    for e in sorted(events, key=lambda e: (e["date"], e["time"], e["t"])):
        k = (e["date"], e["t"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    out = {"updated": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "events": uniq}
    if uniq != old.get("events"):
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    if not ok:
        raise SystemExit("모든 소스 실패")


if __name__ == "__main__":
    main()
