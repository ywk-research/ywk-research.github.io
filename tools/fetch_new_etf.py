"""신규 상장 ETF (미국·한국)를 공식 자료로 모아 _data/new_etf.json에 저장한다.

미국 — SEC EDGAR Form 8-A12B
- ETF를 거래소(NYSE Arca·Nasdaq·Cboe BZX 등)에 상장하려면 운용사가 증권거래법 12(b)에 따라 Form 8-A를 SEC에 낸다.
  보통 첫 거래일 하루 전이나 당일에 제출되므로, 제출일을 '상장(예정)일'의 근사치로 쓴다.
- EDGAR 일별 색인(form.YYYYMMDD.idx)에서 8-A12B를 고르고, 제출 원문 머리말의 펀드 시리즈·티커로 ETF만 남긴다.
  시리즈가 없는 단독 신탁(현물 비트코인 ETF 같은 상품 신탁)은 회사 이름에 ETF가 있거나 SIC 6221이면 포함한다.
- SEC는 자동 요청에 연락처가 담긴 User-Agent를 요구한다: 환경변수 SEC_USER_AGENT (GitHub Secrets). 없으면 미국은 건너뛴다.
- EDGAR 자료는 미국 정부 공개 자료라 재게시에 제약이 없다.

한국 — KRX Open API ETF 일별매매정보(etp/etf_bydd_trd)
- KRX는 상장일 필드를 주지 않으므로, 전 거래일 목록에 없던 종목코드가 처음 나타난 날을 신규 상장일로 본다.
- 전 거래일 종목코드 원본은 tools/state/etf_codes.json(Actions 캐시)에만 두고 커밋하지 않는다(KRX 약관).
  사이트에는 신규 상장 종목의 상장일 값(이름·코드·기초지수·종가·순자산)만 올린다.
- 캐시가 없으면 최근 BACKFILL_DAYS일을 다시 훑어 기준 목록을 만든다.
"""
import datetime as dt
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_data", "new_etf.json")
STATE = os.path.join(ROOT, "tools", "state", "etf_codes.json")
SEEN = os.path.join(ROOT, "tools", "state", "sec_8a_seen.json")
KST = dt.timezone(dt.timedelta(hours=9))
KEEP_DAYS = 45          # 사이트에 남겨 둘 기간(달력일)
BACKFILL_DAYS = int(os.environ.get("NEW_ETF_BACKFILL", "45"))  # 처음 실행·캐시 유실 때 다시 훑는 기간(달력일)
US_RESCAN = 5           # 미국: 매번 다시 보는 최근 영업일 수(EDGAR 색인이 늦게 올라오는 경우 대비)

UA = os.environ.get("SEC_USER_AGENT", "").strip()
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/"
KRX_ETF = "etp/etf_bydd_trd"

EXCHANGES = [  # 8-A 본문에서 찾는 거래소 이름 → 표시 이름(같은 위치면 앞의 것)
    (r"Nasdaq\s+Texas|NASDAQ\s+Texas", "Nasdaq Texas"),
    (r"NYSE\s+Texas", "NYSE Texas"),
    (r"Texas\s+Stock\s+Exchange|TXSE", "TXSE"),
    (r"NYSE\s*Arca", "NYSE Arca"),
    (r"NYSE\s*American", "NYSE American"),
    (r"Nasdaq|NASDAQ", "Nasdaq"),
    (r"Cboe\s*BZX|CBOE\s*BZX|Cboe\s*Exchange|BZX\s*Exchange", "Cboe BZX"),
    (r"New\s+York\s+Stock\s+Exchange", "NYSE"),
]


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.write("\n")


def num(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


# ───────────────────────── 미국 (SEC EDGAR) ─────────────────────────

def sec_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            time.sleep(0.2)  # SEC 공정 접근 기준(초당 10건)보다 느리게
            try:
                return body.decode("utf-8")
            except UnicodeDecodeError:
                return body.decode("latin-1")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(3 * (attempt + 1))


def us_business_days(today, n_cal):
    d = today
    out = []
    for _ in range(n_cal):
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out


TEXAS = ("Nasdaq Texas", "NYSE Texas", "TXSE")  # 2026년 새로 연 텍사스 거래소: 기존 ETF의 교차상장이 대부분이라 뺀다
EXCH_FULL = re.compile(
    r"(?i)nasdaq\s+texas,?\s*(llc|inc\.?)?|nyse\s+texas,?\s*(llc|inc\.?)?|(the\s+)?texas\s+stock\s+exchange(\s*\(txse\))?,?\s*(llc|inc\.?)?|"
    r"(the\s+)?nasdaq\s+stock\s+market,?\s*(llc|inc\.?)?|nasdaq\s+(global|capital)\s+(select\s+)?market,?\s*(llc)?"
    r"|nyse\s*arca,?\s*(inc\.?|llc)?|nyse\s+american,?\s*(llc)?|cboe\s+bzx\s+exchange,?\s*(inc\.?)?"
    r"|(the\s+)?new\s+york\s+stock\s+exchange,?\s*(llc|inc\.?)?|(the\s+)?nyse,?\s+llc")
EIN = re.compile(r"\(?\b\d{2}\s*-\s*\d{7}\b\)?")
HDR = re.compile(r"(?i)\(?\s*((securities\s+(to\s+be\s+)?registered\s+)?pursuant\s+to\s+section\s+12\s*\(b\)\s+of\s+the\s+act|title\s+of\s+each\s+class(\s+of\s+securities)?(\s+to\s+be(\s+so)?\s+registered)?"
                 r"|name\s+of\s+((each|the)\s+)?exchange\s+on\s+which(\s+each\s+class\s+is)?(\s+to\s+be)?(\s+so)?\s+registered"
                 r"|each\s+class\s+is\s+to\s+be(\s+so)?\s+registered|to\s+be\s+so\s+registered"
                 r"|i\.?\s*r\.?\s*s\.?\s+employer\s+identification\s+(no\.?|number))\s*:?\s*\)?")
GENERIC = re.compile(r"(?i)\b(common\s+)?shares?\s+of\s+(beneficial\s+interest|common\s+stock)[,;:]?(\s+(of|in)\s+(the\s+)?)?"
                     r"|[,;]?\s*(no|\$?[\d.]+)\s+par\s+value(\s+per\s+share)?[,;:]?(\s+(of|in)\s+(the\s+)?)?"
                     r"|^\s*shares\s+of\s+")


def exch_of(text):
    best = None
    for pat, label in EXCHANGES:
        mm = re.search(pat, text)
        if mm and (best is None or mm.start() < best[0]):
            best = (mm.start(), label)
    return best[1] if best else ""


HDR_TAIL = re.compile(r"(?i).*(identification\s+(no\.?|number)|id\s+number|to\s+be\s+(so\s+)?registered|exchange\s+on\s+which)\)?[\s:_]*")
PAR = re.compile(r"(?i)[,;]?\s*par\s+value(\s+per\s+share)?(\s+of)?\s*\$?[\d.]+(\s+per\s+share)?[,;:]?(\s+of\b:?)?")
NOT_FUND = re.compile(r"(?i)common\s+stock|ordinary\s+shares|warrants?\b|notes?\s+due|preferred|depositary|\bunits?\b|^(nyse|nasdaq|cboe)\b")


def fund_name(txt):
    txt = HDR_TAIL.sub("", txt.strip())
    txt = PAR.sub(" ", txt)
    txt = re.sub(r"\s*[®Ò™]\s*", " ", txt).replace("_", " ")
    txt = re.sub(r"^\s*Exchange\s+(?=\w)", "", txt)  # 머리글 'Name of each Exchange'의 남은 단어
    txt = GENERIC.sub(" ", txt.strip())
    txt = re.sub(r"\s+", " ", txt).strip(" ,;:-–•*")
    txt = re.sub(r"(?i)^(and|each|per|of)\b[\s:,]*", "", txt).strip(" ,;:-–•*")
    if re.match(r"(?i)(see|none|n/a|\(?the\s+series)", txt):
        return ""
    return txt


def split_names(seg):
    """'A ETF B ETF'처럼 붙은 이름을 나눈다."""
    out = []
    for piece in EIN.split(seg):
        for nm in re.split(r"(?<=\bETF)\s+(?!Class\b|Shares\b)(?=[A-Z0-9])", fund_name(piece)):
            nm = fund_name(nm)
            if len(nm) >= 4 and re.search(r"[A-Za-z]{2}", nm) and not NOT_FUND.search(nm):
                out.append(nm)
    return out


def parse_8a(txt):
    """제출 원문(.txt)에서 회사 이름, SIC, 펀드 이름·거래소를 뽑는다(8-A 머리말에는 시리즈·티커 정보가 없다).
    1) 표지의 '등록할 증권 / 거래소' 칸  2) 없으면 본문 표에서 IRS 번호가 붙은 줄(펀드 목록)"""
    head, _, body = txt.partition("</SEC-HEADER>")
    m = re.search(r"COMPANY CONFORMED NAME:\s*(.+)", head)
    company = m.group(1).strip() if m else ""
    m = re.search(r"STANDARD INDUSTRIAL CLASSIFICATION:.*?\[(\d{4})\]", head)
    sic = m.group(1) if m else ""
    body = body[:400000]
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"[\s\u200b]+", " ", text)
    exch = exch_of(text)
    funds = []
    st = re.search(r"(?i)pursuant\s+to\s+section\s+12\s*\(b\)\s+of\s+the\s+act\s*:?|title\s+of\s+each\s+class", text)
    if st:
        en = re.search(r"(?i)if\s+this\s+form\s+relates|securities\s+act\s+registration", text[st.end():])
        region = HDR.sub(" ", text[st.start(): st.end() + (en.start() if en else 1500)])
        pos, chunks = 0, []
        for mm in EXCH_FULL.finditer(region):
            chunks.append((region[pos:mm.start()], exch_of(mm.group(0))))
            pos = mm.end()
        chunks.append((region[pos:], chunks[-1][1] if chunks else exch))
        for seg, ex in chunks:
            funds += [{"name": nm, "exchange": ex} for nm in split_names(seg)]
    if not funds:  # 표지에 'Shares of beneficial interest'만 있고 펀드는 뒤쪽 표에 있는 경우
        for row in re.findall(r"(?is)<tr\b.*?</tr>", body):
            cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                     for c in re.findall(r"(?is)<t[dh]\b.*?</t[dh]>", row)]
            cells = [c for c in cells if c]
            if not any(EIN.fullmatch(c) for c in cells):
                continue
            for c in cells:
                if EIN.fullmatch(c) or EXCH_FULL.fullmatch(c):
                    continue
                nm = fund_name(c)
                if len(nm) >= 4 and re.search(r"[A-Za-z]{2}", nm) and not re.search(r"(?i)^name\b|identification", nm):
                    funds.append({"name": nm, "exchange": exch_of(" ".join(cells)) or exch})
                    break
    if os.environ.get("DEBUG") and not funds:
        i = text.lower().find("12(b)")
        print("    [본문]", text[max(i, 0):max(i, 0) + 700])
    seen, out = set(), []
    for f in funds:
        if f["name"].lower() not in seen:
            seen.add(f["name"].lower())
            out.append(f)
    return company, sic, out, exch


# ── 티커: SEC 투자회사 시리즈·클래스 목록(연간 CSV, 수시 갱신)과 company_tickers_mf.json ──
SERIES_CSV = "https://www.sec.gov/files/investment/data/other/investment-company-series-class-information/investment-company-series-class-{y}.csv"
_tick = None


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("&", "and"))


def ticker_table(year):
    """(CIK, 시리즈 이름 정규화) → [(클래스 이름, 티커)]"""
    global _tick
    if _tick is not None:
        return _tick
    _tick = {}
    try:
        mf = json.loads(sec_get("https://www.sec.gov/files/company_tickers_mf.json") or "{}")
        by_class = {r[2]: r[3] for r in mf.get("data", [])}
        raw = sec_get(SERIES_CSV.format(y=year)) or sec_get(SERIES_CSV.format(y=year - 1)) or ""
        import csv
        for r in csv.DictReader(io.StringIO(raw.lstrip("\ufeff").lstrip("ï»¿"))):
            cik = (r.get("CIK Number") or "").lstrip("0")
            tk = (r.get("Class Ticker") or "").strip() or by_class.get(r.get("Class ID", ""), "")
            _tick.setdefault((cik, norm(r.get("Series Name", ""))), []).append((r.get("Class Name", ""), tk))
        print(f"티커 표: 시리즈 {len(_tick):,}개")
    except Exception as e:
        print(f"티커 표 실패: {type(e).__name__}: {e}")
    return _tick


def find_ticker(cik, name, year):
    tab = ticker_table(year)
    cik = cik.lstrip("0")
    base, _, cls = name.partition(":")
    for key in (norm(name), norm(base)):
        rows = tab.get((cik, key))
        if rows:
            if cls:
                rows = [r for r in rows if norm(cls) in norm(r[0])] or rows
            ticks = [t for _, t in rows if t]
            if ticks:
                return ticks[0] if not cls else ticks[-1]
    return ""


def is_fund_filer(company, sic):
    """운용사·펀드 신탁만 남긴다. 투자회사는 EDGAR에 SIC 코드가 없고, 상품 신탁(현물 코인·금 ETF)은 6221."""
    return sic in ("", "6221") and not re.search(r"(?i)acquisition|\bspac\b", company) \
        or bool(re.search(r"\bETF\b", company, re.I))


ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}
SMALL = {"OF", "AND", "THE", "FOR", "INC", "CO", "LLC", "LP"}


def clean_company(name):
    name = re.sub(r"\s*/[A-Z]{2}/?\s*$", "", name).strip()  # 'TRUST /DE/' 같은 주 표시 제거
    if name != name.upper():
        return name  # 대소문자가 섞여 있으면 등록 이름 그대로
    out = []
    for w in name.split():
        core = w.strip(",.")
        if core in ROMAN or core == "ETF" or (len(core) <= 3 and core not in SMALL and core.isalpha()):
            out.append(w)
        else:
            out.append(w.capitalize() if core != "LLC" else w)
    return " ".join(out)


def update_us(today, prev):
    if not UA:
        print("미국 건너뜀 — SEC_USER_AGENT가 없습니다.")
        return prev
    items = {it["key"]: it for it in prev.get("items", [])}
    seen = set(load(SEEN, []))
    done = set(prev.get("days", []))
    cutoff = today - dt.timedelta(days=KEEP_DAYS)
    days = [d for d in us_business_days(today, BACKFILL_DAYS)
            if d >= cutoff and (d.isoformat() not in done or (today - d).days <= US_RESCAN + 2)]
    for d in sorted(days):
        q = (d.month - 1) // 3 + 1
        idx = sec_get(f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{q}/form.{d:%Y%m%d}.idx")
        if idx is None:
            continue  # 휴일이거나 아직 안 올라옴
        done.add(d.isoformat())
        for line in idx.splitlines():
            if not line.startswith("8-A12B "):
                continue  # 8-A12B/A(정정)는 제외
            m = re.search(r"(edgar/data/\d+/(\d{10}-\d{2}-\d{6})\.txt)\s*$", line)
            if not m:
                continue
            path, acc = m.group(1), m.group(2)
            if acc in seen:
                continue
            txt = sec_get("https://www.sec.gov/Archives/" + path)
            if txt is None:
                continue
            seen.add(acc)
            company, sic, funds, exch = parse_8a(txt)
            fund = is_fund_filer(company, sic)
            if os.environ.get("DEBUG"):
                print(f"  8-A {d} {company} sic={sic} fund={fund} exch={exch} -> {[f['name'] for f in funds][:12]}")
            if not fund:
                continue
            cik = path.split("/")[2]
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"
            if not funds:  # 표를 못 읽으면 신탁 이름만
                funds = [{"name": "", "exchange": exch}]
            funds = [f for f in funds if f["exchange"] not in TEXAS]
            if not funds and exch in TEXAS:
                continue
            for f in funds:
                key = (cik + ":" + f["name"].lower()) if f["name"] else acc
                if key in items:  # 같은 펀드가 다시 8-A를 내면 처음 날짜를 남긴다
                    continue
                items[key] = {"key": key, "d": d.isoformat(), "name": f["name"], "ticker": "", "cik": cik,
                              "issuer": clean_company(company), "exchange": f["exchange"] or exch, "url": url}
        print(f"미국 {d}: 누적 {len(items)}건")
    keep = sorted((it for it in items.values() if it["d"] >= cutoff.isoformat()),
                  key=lambda it: (it["d"], it["issuer"], it["name"]), reverse=True)
    for it in keep:  # 시리즈 등록이 늦게 반영되므로 티커가 빈 펀드는 매번 다시 찾는다
        if not it.get("ticker") and it.get("name") and it.get("cik"):
            it["ticker"] = find_ticker(it["cik"], it["name"], today.year)
    save(SEEN, sorted(seen)[-5000:], compact=True)
    return {"source": "SEC EDGAR Form 8-A12B", "updated": today.isoformat(),
            "days": sorted(x for x in done if x >= cutoff.isoformat()), "items": keep}


# ───────────────────────── 한국 (KRX Open API) ─────────────────────────

class NotApproved(Exception):
    pass


def krx_fetch(bas_dd, key):
    url = KRX_BASE + KRX_ETF + "?" + urllib.parse.urlencode({"basDd": bas_dd})
    req = urllib.request.Request(url, headers={"AUTH_KEY": key, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return (json.loads(r.read().decode("utf-8")) or {}).get("OutBlock_1") or []
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise NotApproved(KRX_ETF)
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))


def eok_txt(eok):
    eok = int(round(eok))
    jo, rest = divmod(eok, 10000)
    if jo:
        return f"{jo}조 {rest:,}억" if rest else f"{jo}조"
    return f"{rest:,}억"


def kr_row(r, d):
    close = num(r.get("TDD_CLSPRC")) or 0
    aum = round((num(r.get("INVSTASST_NETASST_TOTAMT")) or 0) / 1e8)
    val = round((num(r.get("ACC_TRDVAL")) or 0) / 1e8, 1)
    return {"d": d.isoformat(), "name": r.get("ISU_NM", ""), "code": r.get("ISU_CD", ""),
            "index": r.get("IDX_IND_NM", ""), "close": close, "close_txt": f"{int(close):,}",
            "aum": aum, "aum_txt": eok_txt(aum) if aum else "", "value": val}


def update_kr(today, prev):
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        print("한국 건너뜀 — KRX_API_KEY가 없습니다.")
        return prev
    state = load(STATE, {})
    items = {it["code"]: it for it in prev.get("items", [])}
    codes = set(state.get("codes", []))
    last = dt.date.fromisoformat(state["d"]) if state.get("d") else None
    if not last or (today - last).days > 20:  # 캐시가 없거나 너무 오래됨 → 기준 목록부터 다시
        print("한국: 기준 목록을 새로 만듭니다.")
        codes, last = set(), today - dt.timedelta(days=BACKFILL_DAYS)
    d = last + dt.timedelta(days=1)
    latest = None
    while d < today + dt.timedelta(days=1):
        if d.weekday() < 5:
            rows = krx_fetch(d.strftime("%Y%m%d"), key)
            if rows:
                now = {r.get("ISU_CD", "") for r in rows}
                if codes:  # 첫 기준일은 비교 대상이 없으므로 건너뜀
                    for r in rows:
                        c = r.get("ISU_CD", "")
                        if c and c not in codes and c not in items:
                            items[c] = kr_row(r, d)
                            print(f"한국 신규 {d}: {r.get('ISU_NM')} ({c})")
                codes, latest = now, d
            time.sleep(0.3)
        d += dt.timedelta(days=1)
    if latest:
        save(STATE, {"d": latest.isoformat(), "codes": sorted(codes)}, compact=True)
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    keep = sorted((it for it in items.values() if it["d"] >= cutoff),
                  key=lambda it: (it["d"], it["aum"]), reverse=True)
    return {"source": "한국거래소(KRX) Open API", "updated": (latest or last).isoformat(),
            "listed": len(codes) or prev.get("listed", 0), "items": keep}


def main():
    today = dt.datetime.now(KST).date()
    old = load(OUT, {})
    out = dict(old)
    for name, fn in (("us", update_us), ("kr", update_kr)):
        try:
            out[name] = fn(today, old.get(name, {}))
        except NotApproved as e:
            print(f"한국 건너뜀 — KRX 인증 실패({e})")
        except Exception as e:  # 한쪽이 실패해도 다른 쪽은 저장
            print(f"{name} 실패: {type(e).__name__}: {e}")
    if out != old:
        save(OUT, out)
        print("저장:", OUT)
    else:
        print("변경 없음")


if __name__ == "__main__":
    sys.exit(main())
