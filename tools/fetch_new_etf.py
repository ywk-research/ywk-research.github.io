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

EXCHANGES = [  # 8-A 본문에서 찾는 거래소 이름 → 표시 이름(먼저 맞는 것)
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


def _cell_text(h):
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", h)
    h = html.unescape(re.sub(r"<[^>]+>", " ", h))
    return [re.sub(r"\s+", " ", x).strip() for x in h.split("\n") if x.strip()]


def exch_of(text):
    best = None
    for pat, label in EXCHANGES:
        mm = re.search(pat, text)
        if mm and (best is None or mm.start() < best[0]):
            best = (mm.start(), label)
    return best[1] if best else ""


GENERIC = re.compile(r"(?i)^(common\s+)?shares?\b.*?(beneficial interest|par value|common stock)[^A-Za-z0-9]*"
                     r"((no|\$?[\d.]+)\s+par value( per share)?)?[,;:]?\s*((of|in)\s+)?(the\s+)?")
SKIP = re.compile(r"(?i)title of|name of (each )?exchange|employer identification|^\(?[\d-]{9,11}\)?$|^none\.?$|^n/?a$")


def fund_name(txt):
    txt = GENERIC.sub("", txt).strip(" ,;:-–")
    txt = re.sub(r"\s*\(?\d{2}-\d{7}\)?\s*$", "", txt)  # 끝에 붙은 IRS 번호
    return txt.strip(" ,;:-–")


def parse_8a(txt):
    """제출 원문(.txt)에서 회사 이름, SIC, 표지 표의 펀드 이름·거래소를 뽑는다.
    8-A 머리말에는 시리즈·티커 정보가 없어서, 표지의 '등록할 증권 / 거래소' 표를 읽는다."""
    head, _, body = txt.partition("</SEC-HEADER>")
    m = re.search(r"COMPANY CONFORMED NAME:\s*(.+)", head)
    company = m.group(1).strip() if m else ""
    m = re.search(r"STANDARD INDUSTRIAL CLASSIFICATION:.*?\[(\d{4})\]", head)
    sic = m.group(1) if m else ""
    body = body[:400000]
    funds = []
    for row in re.findall(r"(?is)<tr\b.*?</tr>", body):
        cells = [_cell_text(c) for c in re.findall(r"(?is)<t[dh]\b.*?</t[dh]>", row)]
        flat = " ".join(" ".join(c) for c in cells)
        ex = exch_of(flat)
        if not ex or re.search(r"(?i)title of", flat):
            continue
        for c in cells:
            if exch_of(" ".join(c)):
                continue
            for line in c:
                if SKIP.search(line):
                    continue
                nm = fund_name(line)
                if len(nm) >= 3 and re.search(r"[A-Za-z]", nm):
                    funds.append({"name": nm, "exchange": ex})
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))
    return company, sic, funds, exch_of(text)


def is_fund_filer(company, sic):
    """운용사·펀드 신탁만 남긴다. 투자회사는 EDGAR에 SIC 코드가 없고, 상품 신탁(현물 코인·금 ETF)은 6221."""
    return sic in ("", "6221") and not re.search(r"(?i)acquisition|\bspac\b", company) \
        or bool(re.search(r"\bETF\b", company, re.I))


def clean_company(name):
    name = re.sub(r"\s*/[A-Z]{2}/?\s*$", "", name)  # 'TRUST /DE/' 같은 주 표시 제거
    return name.strip().title().replace("Etf", "ETF").replace("Ii", "II").replace("Iii", "III")


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
            for f in funds:
                key = (cik + ":" + f["name"].lower()) if f["name"] else acc
                if key in items:  # 같은 펀드가 다시 8-A를 내면 처음 날짜를 남긴다
                    continue
                items[key] = {"key": key, "d": d.isoformat(), "name": f["name"], "ticker": "",
                              "issuer": clean_company(company), "exchange": f["exchange"] or exch, "url": url}
        print(f"미국 {d}: 누적 {len(items)}건")
    keep = sorted((it for it in items.values() if it["d"] >= cutoff.isoformat()),
                  key=lambda it: (it["d"], it["issuer"], it["name"]), reverse=True)
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
