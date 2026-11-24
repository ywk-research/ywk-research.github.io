"""Calendar 'Earnings' 탭용 미국·한국 실적발표 일정을 공식 공시로 모아 _data/earnings.json에 저장한다.

미국 — SEC EDGAR (SEC_USER_AGENT 필요)
- 대상: SEC company_tickers.json 앞쪽(시가총액 순에 가까움) US_TOP개 회사 + _data/watchlist.yml 종목
- 발표 완료: 회사별 submissions API에서 Item 2.02(Results of Operations)가 든 8-K. 접수 시각(미 동부)을 한국시간으로 바꾼다
- 발표 예정: 미국은 공식·무료 '예정일' 자료가 없어서, 1년 전 같은 분기 발표일 + 52주(같은 요일)로 추정하고 '예상'으로 표시한다.
  실제 8-K가 들어오면 추정치는 사라진다

한국 — 금융감독원 OpenDART (DART_API_KEY 필요)
- 대상: fetch_krx.py가 Actions 캐시에 남긴 tools/state/caps.json 기준 코스피 KR_TOP_KOSPI개, 코스닥 KR_TOP_KOSDAQ개
- 발표 완료: '영업(잠정)실적(공정공시)' 계열 공시(접수일)
- 발표 예정: '기업설명회(IR)개최(안내공시)' 본문에서 개최 일시를 읽고, 본문에 '실적'이 있는 것만 넣는다
- 회사 미지정 검색은 최근 3개월까지만 되므로 지난 기록은 이 파일에 쌓아 둔다

키는 출력하지 않는다. 한쪽이 실패하면 그쪽은 이전 값을 유지한다.
"""
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_data", "earnings.json")
CAPS = os.path.join(ROOT, "tools", "state", "caps.json")
KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
NOW = dt.datetime.now(KST)
TODAY = NOW.date()
US_TOP = 100
KR_TOP_KOSPI, KR_TOP_KOSDAQ = 100, 30
KEEP_DAYS = 400          # 추정용으로 1년 남짓 보관
AHEAD_DAYS = 100         # 추정 일정은 앞으로 이 기간만
DART_BACK = 89           # 회사 미지정 검색 한도(3개월)


def get(url, headers=None, binary=False):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0 (ywk-research.github.io data job)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            return body if binary else body.decode("utf-8", "replace")
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(type(e).__name__) from None  # URL(키 포함)이 섞이지 않게
            time.sleep(3 * (attempt + 1))


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


# ---------------- 미국 ----------------

def sec(url):
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    body = get(url, {"User-Agent": ua, "Accept-Encoding": "identity"})
    time.sleep(0.2)  # SEC 한도(초당 10건)보다 훨씬 느리게
    return body


def watch_symbols():
    out = []
    path = os.path.join(ROOT, "_data", "watchlist.yml")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            m = re.match(r"\s*-\s*symbol:\s*\"?([^\"#\s]+)", line)
            if m:
                t = m.group(1).split(":")[-1].upper()
                if not t.isdigit():
                    out.append(t)
    return out


def us_events(old):
    if not os.environ.get("SEC_USER_AGENT", "").strip():
        raise RuntimeError("SEC_USER_AGENT 없음")
    tick = json.loads(sec("https://www.sec.gov/files/company_tickers.json"))
    rows = [tick[k] for k in sorted(tick, key=int)]
    by_t = {r["ticker"].upper(): r for r in rows}
    pick, seen = [], set()
    for r in rows[: US_TOP * 2]:          # 같은 회사 여러 클래스(GOOGL·GOOG)는 하나로
        if r["cik_str"] not in seen and len(pick) < US_TOP:
            seen.add(r["cik_str"]); pick.append((r, len(pick) + 1))
    for t in watch_symbols():
        r = by_t.get(t.replace(".", "-"))
        if r and r["cik_str"] not in seen:
            seen.add(r["cik_str"]); pick.append((r, US_TOP + 1))
    cut = (TODAY - dt.timedelta(days=KEEP_DAYS)).isoformat()
    out = []
    for r, rank in pick:
        cik = int(r["cik_str"])
        try:
            sub = json.loads(sec(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
        except Exception as e:
            print(f"  실패 — {r['ticker']}: {e}")
            continue
        rec = sub["filings"]["recent"]
        name = sub.get("name") or r["title"]
        days = set()
        for form, items, acc_t, acc in zip(rec["form"], rec["items"], rec["acceptanceDateTime"], rec["accessionNumber"]):
            if form != "8-K" or "2.02" not in (items or ""):
                continue
            # acceptanceDateTime은 UTC(예: 애플 16:30 ET 발표 → 20:30Z)
            et = dt.datetime.fromisoformat(acc_t[:19]).replace(tzinfo=dt.timezone.utc).astimezone(ET)
            if et.date().isoformat() < cut or et.date() in days:  # 같은 날 정정·추가 제출은 한 번만
                continue
            days.add(et.date())
            k = et.astimezone(KST)
            out.append({"c": "us", "sym": r["ticker"].upper(), "name": name, "rank": rank,
                        "date": k.date().isoformat(), "time": k.strftime("%H:%M"),
                        "et": et.strftime("%Y-%m-%d %H:%M"), "no": acc, "cik": cik})
    return out


def us_estimates(actual):
    """1년 전 같은 분기 발표일 + 52주. 그 주변(±25일)에 실제 발표가 이미 있으면 넣지 않는다."""
    by = {}
    for e in actual:
        by.setdefault(e["sym"], []).append(e)
    out = []
    for sym, rows in by.items():
        dates = [dt.date.fromisoformat(e["et"][:10]) for e in rows]
        for e in rows:
            d0 = dt.date.fromisoformat(e["et"][:10])
            guess = d0 + dt.timedelta(weeks=52)
            if not (TODAY - dt.timedelta(days=3) <= guess <= TODAY + dt.timedelta(days=AHEAD_DAYS)):
                continue
            if any(abs((d - guess).days) <= 25 for d in dates if d > d0 + dt.timedelta(days=200)):
                continue
            et = dt.datetime.combine(guess, dt.time.fromisoformat(e["et"][11:16]), ET)
            k = et.astimezone(KST)
            out.append({**{x: e[x] for x in ("c", "sym", "name", "rank", "cik")},
                        "date": k.date().isoformat(), "time": k.strftime("%H:%M"), "et": et.strftime("%Y-%m-%d %H:%M"), "est": 1})
    return out


# ---------------- 한국 ----------------

def dart_list(key, start, end, ty):
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"crtfc_key": key, "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                                    "pblntf_ty": ty, "page_no": page, "page_count": 100})
        t = json.loads(get("https://opendart.fss.or.kr/api/list.json?" + q))
        if t.get("status") == "013":
            break
        if t.get("status") != "000":
            raise RuntimeError(f"DART 응답 {t.get('status')} {t.get('message')}")
        rows += t.get("list", [])
        if page >= int(t.get("total_page", 1)):
            break
        page += 1
        time.sleep(0.3)
    return rows


KR_SKIP = []
DATE_RE = re.compile(r"(20\d\d)\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2})\s*[:시]\s*(\d{2})")


def doc_text(key, rcept_no):
    raw = get("https://opendart.fss.or.kr/api/document.xml?" + urllib.parse.urlencode({"crtfc_key": key, "rcept_no": rcept_no}), binary=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = " ".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text)
    time.sleep(0.3)
    return re.sub(r"\s+", " ", text)


def is_quarterly(text):
    """잠정실적 공시가 분기·반기·연간 실적인지(월별 판매실적 등은 뺀다)."""
    return bool(re.search(r"분기|반기|연간|사업연도|[1-4]\s*Q|당기", text))


def ir_detail(key, rcept_no):
    """IR 개최 안내공시 본문에서 (개최일, 시각, 실적 관련 여부)."""
    text = doc_text(key, rcept_no)
    i = text.find("일시")
    seg = text[i: i + 200] if i >= 0 else ""
    m = DATE_RE.search(seg)
    if not m:
        return None, None, False
    day = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    tm = TIME_RE.search(seg[m.end(): m.end() + 60])
    hhmm = f"{int(tm.group(1)):02d}:{tm.group(2)}" if tm and 0 < int(tm.group(1)) < 24 else None
    # 실적 발표 IR만: '목적'·'내용' 칸에 실적·결산이 있어야 한다(해외 NDR·컨퍼런스 참가 안내는 뺀다)
    j = max(text.find("목적"), text.find("내용"))
    purpose = text[j: j + 160] if j >= 0 else ""
    return day, hhmm, bool(re.search(r"실적|결산", purpose))


def kr_events(old):
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DART_API_KEY 없음")
    caps = load(CAPS, {}).get("rows", {})
    if not caps:
        raise RuntimeError("시가총액 표(tools/state/caps.json) 없음")
    rank = {}
    for mkt, n in (("kospi", KR_TOP_KOSPI), ("kosdaq", KR_TOP_KOSDAQ)):
        top = sorted(((c, v[1]) for c, v in caps.items() if v[0] == mkt), key=lambda x: -x[1])[:n]
        for i, (c, cap) in enumerate(top):
            rank[c] = (mkt, i + 1 if mkt == "kospi" else KR_TOP_KOSPI + i + 1)
    prev = {e["no"]: e for e in (old.get("events") or []) if e.get("c") == "kr"}
    skip = set(old.get("kr_skip") or [])  # 본문을 읽고 뺀 공시(다시 읽지 않게)
    start = TODAY - dt.timedelta(days=DART_BACK)
    if prev:  # 이미 받은 기간은 최근 2주만 다시 본다(정정·늦은 반영)
        start = max(start, TODAY - dt.timedelta(days=14))
    found = {}
    for r in dart_list(key, start, TODAY, "I"):
        code = (r.get("stock_code") or "").strip()
        if code not in rank:
            continue
        nm = re.sub(r"\s+", "", r.get("report_nm", ""))
        head = nm.split("]")[0] if nm.startswith("[") else ""
        if "정정" in head:
            continue
        mkt, rk = rank[code]
        base = {"c": "kr", "sym": code, "name": r["corp_name"], "rank": rk, "mkt": mkt, "no": r["rcept_no"]}
        filed = f"{r['rcept_dt'][:4]}-{r['rcept_dt'][4:6]}-{r['rcept_dt'][6:]}"
        if r["rcept_no"] in prev or r["rcept_no"] in skip:  # 이미 본 공시
            continue
        title = re.sub(r"^\[[^\]]*\]", "", r["report_nm"]).strip()
        try:
            if "영업(잠정)실적" in nm:
                if is_quarterly(doc_text(key, r["rcept_no"])):
                    found[r["rcept_no"]] = {**base, "date": filed, "kind": "prelim", "title": title}
                else:
                    skip.add(r["rcept_no"])
            elif "기업설명회" in nm:
                day, hhmm, earn = ir_detail(key, r["rcept_no"])
                if day and earn:
                    found[r["rcept_no"]] = {**base, "date": day.isoformat(), "time": hhmm, "kind": "ir", "filed": filed, "title": title}
                else:
                    skip.add(r["rcept_no"])
        except Exception as e:
            print(f"  본문 실패 {r['corp_name']}: {e}")
    prev.update(found)
    # 같은 회사·같은 날은 하나로(자회사 실적을 지주사가 여러 건 내는 경우, IR 안내와 잠정실적이 같은 날인 경우).
    # 잠정실적을 남기고, 시각은 IR 안내에서 가져온다
    one = {}
    for e in sorted(prev.values(), key=lambda e: (e["kind"] != "prelim", e["no"])):
        k = (e["sym"], e["date"])
        if k in one:
            if e.get("time") and not one[k].get("time"):
                one[k]["time"] = e["time"]
            continue
        one[k] = dict(e)
    KR_SKIP[:] = sorted(skip)[-3000:]
    return list(one.values())


def kr_names_en(old, events):
    """영어 모드용 회사 영문명(DART 기업개황). 처음 보는 회사만 받는다."""
    names = dict(old.get("names_en") or {})
    key = os.environ.get("DART_API_KEY", "").strip()
    todo = {e["sym"] for e in events if e["c"] == "kr"} - set(names)
    if not key or not todo:
        return names
    corp = {}
    try:  # 종목코드 → DART 고유번호
        raw = get("https://opendart.fss.or.kr/api/corpCode.xml?" + urllib.parse.urlencode({"crtfc_key": key}), binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read(z.namelist()[0]).decode("utf-8", "replace")
        for blk in re.findall(r"<list>(.*?)</list>", xml, re.S):  # 회사 하나씩
            cc = re.search(r"<corp_code>(\d+)</corp_code>", blk)
            sc = re.search(r"<stock_code>\s*(\d{6})\s*</stock_code>", blk)
            if cc and sc:
                corp[sc.group(1)] = cc.group(1)
    except Exception as e:
        print(f"  고유번호 목록 실패: {e}")
        return names
    for code in sorted(todo):
        if code not in corp:
            continue
        try:
            j = json.loads(get("https://opendart.fss.or.kr/api/company.json?" + urllib.parse.urlencode({"crtfc_key": key, "corp_code": corp[code]})))
            en = (j.get("corp_name_eng") or "").strip()
            if en:
                names[code] = re.sub(r"\s*(Co\.,?\s*Ltd\.?|Corporation|Corp\.?|Inc\.?|Ltd\.?)\s*$", "", en, flags=re.I).strip(" ,")
        except Exception as e:
            print(f"  영문명 실패 {code}: {e}")
        time.sleep(0.2)
    return names


def main():
    old = load(OUT, {})
    prev = old.get("events") or []
    events, ok = [], []
    try:
        act = us_events(old)
        events += act + us_estimates(act)
        ok.append("us")
        print(f"미국: 발표 {len(act)}건, 예상 {len(events) - len(act)}건")
    except Exception as e:
        print(f"실패 — 미국: {str(e)[:120]}; 이전 값 유지")
        events += [e for e in prev if e["c"] == "us"]
    try:
        kr = kr_events(old)
        events += kr
        ok.append("kr")
        print(f"한국: {len(kr)}건 (잠정실적 {sum(e['kind'] == 'prelim' for e in kr)}, IR {sum(e['kind'] == 'ir' for e in kr)})")
    except Exception as e:
        print(f"실패 — 한국: {str(e)[:120]}; 이전 값 유지")
        events += [e for e in prev if e["c"] == "kr"]
    if not ok:
        sys.exit(0)
    cut = (TODAY - dt.timedelta(days=KEEP_DAYS)).isoformat()
    events = sorted((e for e in events if e["date"] >= cut), key=lambda e: (e["date"], e.get("time") or "99", e["rank"]))
    out = {"source": "SEC EDGAR(8-K Item 2.02), 금융감독원 OpenDART", "names_en": kr_names_en(old, events),
           "events": events, "kr_skip": KR_SKIP if "kr" in ok else old.get("kr_skip", [])}
    if {k: old.get(k) for k in out} == out:
        print("earnings: 변경 없음")
        return
    out["updated_at"] = NOW.strftime("%Y-%m-%d %H:%M KST")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
