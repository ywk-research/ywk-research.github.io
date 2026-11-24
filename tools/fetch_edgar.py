"""SEC EDGAR 공식 데이터로 미국 종목의 내부자 매매(Form 4)와 기관 보유(13F)를 받아 assets/data/edgar/에 저장한다.

- 대상: _data/watchlist.yml의 종목(+ _data/stock_pages.yml의 us 목록이 있으면 함께). 13F는 cusip이 적힌 종목만
- SEC는 자동 요청에 연락처가 담긴 User-Agent를 요구한다: 환경변수 SEC_USER_AGENT (GitHub Secrets)
  없으면 아무것도 하지 않고 끝낸다. 요청은 초당 10건 제한보다 훨씬 느리게 보낸다.
- 파일: assets/data/edgar/{티커}.json
  - insider: 최근 Form 4 거래(날짜, 이름, 직위, 코드, 주식 수, 가격, 거래 후 보유)
  - inst   : 최근 두 분기 13F 집계(보고 기관 수, 합계 주식 수, 상위 10곳과 직전 분기 대비 증감)
- 13F 데이터 세트(분기별 zip, 수십 MB)는 새 세트가 나왔을 때만 다시 계산한다(assets/data/edgar/_13f.json에 기록).
"""
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "data", "edgar")
KST = dt.timezone(dt.timedelta(hours=9))
UA = os.environ.get("SEC_USER_AGENT", "").strip()
INSIDER_N = 30
F13_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
csv.field_size_limit(10**8)


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
            time.sleep(0.25)
            return body if binary else body.decode("utf-8", "replace")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def read_yaml_list(path):
    """watchlist.yml에서 symbol·cusip만 뽑는다(외부 라이브러리 없이)."""
    out, cur = [], None
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\s*-\s*symbol:\s*\"?([^\"#\s]+)", line)
        if m:
            cur = {"symbol": m.group(1)}
            out.append(cur)
            continue
        m = re.match(r"\s+cusip:\s*\"?([0-9A-Za-z]{9})", line)
        if m and cur is not None:
            cur["cusip"] = m.group(1).upper()
    return out


def targets():
    seen, out = set(), []
    for path in ("_data/watchlist.yml", "_data/stock_pages.yml"):
        for s in read_yaml_list(os.path.join(ROOT, path)):
            t = s["symbol"].split(":")[-1].upper()
            if t not in seen and not t.isdigit():
                seen.add(t)
                out.append({"ticker": t, "cusip": s.get("cusip")})
    return out


# ---------- Form 4 ----------

def txt(el, path):
    x = el.find(path)
    return (x.text or "").strip() if x is not None and x.text else ""


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_form4(xml, filed):
    root = ET.fromstring(xml)
    owners = root.findall("reportingOwner")
    name = txt(owners[0], "reportingOwnerId/rptOwnerName") if owners else ""
    rel = owners[0].find("reportingOwnerRelationship") if owners else None
    title = ""
    if rel is not None:
        if txt(rel, "officerTitle"):
            title = txt(rel, "officerTitle")
        elif txt(rel, "isDirector") in ("1", "true"):
            title = "Director"
        elif txt(rel, "isTenPercentOwner") in ("1", "true"):
            title = "10% Owner"
    rows = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        rows.append({
            "d": txt(t, "transactionDate/value")[:10] or filed,
            "filed": filed, "name": name, "title": title,
            "code": txt(t, "transactionCoding/transactionCode"),
            "ad": txt(t, "transactionAmounts/transactionAcquiredDisposedCode/value"),
            "sh": fnum(txt(t, "transactionAmounts/transactionShares/value")),
            "px": fnum(txt(t, "transactionAmounts/transactionPricePerShare/value")),
            "after": fnum(txt(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
        })
    return rows


def insider(cik):
    sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    rec = sub["filings"]["recent"]
    out = []
    for form, acc, filed, doc in zip(rec["form"], rec["accessionNumber"], rec["filingDate"], rec["primaryDocument"]):
        if form != "4":
            continue
        raw = doc.split("/")[-1]  # xslF345X05/파일.xml → 원본 XML
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{raw}"
        try:
            out += parse_form4(get(url), filed)
        except Exception as e:
            print(f"  Form 4 읽기 실패 {acc}: {e}")
        if len(out) >= INSIDER_N:
            break
    out.sort(key=lambda r: (r["d"], r["filed"]), reverse=True)
    return sub, out[:INSIDER_N]


SKIP_FORMS = {"3", "4", "5", "3/A", "4/A", "5/A", "144", "144/A", "SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A"}


def profile(sub):
    """기업 개요: SEC 제출 정보(업종 SIC, 설립 주, 회계연도 말, 본사, 상장 거래소)."""
    biz = (sub.get("addresses") or {}).get("business") or {}
    fy = sub.get("fiscalYearEnd") or ""
    return {
        "name": sub.get("name"), "tickers": sub.get("tickers") or [], "exchanges": sub.get("exchanges") or [],
        "sic": sub.get("sic"), "sic_desc": sub.get("sicDescription"),
        "state": sub.get("stateOfIncorporationDescription") or sub.get("stateOfIncorporation"),
        "fy_end": f"{fy[:2]}/{fy[2:]}" if len(fy) == 4 else fy,
        "city": " ".join(x for x in [(biz.get("city") or "").title(), biz.get("stateOrCountryDescription") or biz.get("stateOrCountry") or ""] if x),
        "phone": sub.get("phone") or "", "website": sub.get("website") or "",
        "former": [f.get("name") for f in (sub.get("formerNames") or [])][:2],
    }


def filings(sub, cik, n=15):
    """최근 SEC 공시(내부자·대량보유 신고 제외): [날짜, 서식, 설명, 8-K 항목, 원문 주소]."""
    rec = sub["filings"]["recent"]
    out = []
    for form, acc, filed, doc, desc, items in zip(rec["form"], rec["accessionNumber"], rec["filingDate"],
                                                  rec["primaryDocument"], rec.get("primaryDocDescription") or [""] * len(rec["form"]),
                                                  rec.get("items") or [""] * len(rec["form"])):
        if form in SKIP_FORMS:
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}" if doc else \
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
        out.append([filed, form, (desc or "").strip(), items or "", url])
        if len(out) >= n:
            break
    return out


# ---------- 13F ----------

def f13_sets():
    """최근 13F 데이터 세트 zip 주소 두 개(최신 먼저)."""
    html = get(F13_PAGE)
    links = re.findall(r'href="([^"]*form13f[^"]*\.zip)"', html, re.I)
    links = [("https://www.sec.gov" + l) if l.startswith("/") else l for l in links]
    uniq = []
    for l in links:
        if l not in uniq:
            uniq.append(l)
    return uniq[:2]


def f13_holdings(url, cusips):
    """zip 하나에서 CUSIP별 {기관 이름: 주식 수}와 보고 기준일."""
    z = zipfile.ZipFile(io.BytesIO(get(url, binary=True)))
    names = {n.split("/")[-1].upper(): n for n in z.namelist()}

    def rows(name):
        with z.open(names[name]) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t")

    sub = {}
    for r in rows("SUBMISSION.TSV"):
        if r.get("SUBMISSIONTYPE") == "13F-HR":
            sub[r["ACCESSION_NUMBER"]] = (r.get("FILER_CIK") or r["ACCESSION_NUMBER"], r.get("PERIODOFREPORT", ""))
    cover = {}
    for r in rows("COVERPAGE.TSV"):
        if r["ACCESSION_NUMBER"] in sub and (r.get("ISAMENDMENT") or "N").upper() != "Y":
            cover[r["ACCESSION_NUMBER"]] = r.get("FILINGMANAGER_NAME", "").strip()
    # 가장 많은 보고서가 쓰는 기준일만 쓴다(늦게 낸 지난 분기 보고서 제외)
    periods = {}
    for acc in cover:
        periods[sub[acc][1]] = periods.get(sub[acc][1], 0) + 1
    period = max(periods, key=periods.get) if periods else ""
    latest = {}  # 같은 기관이 여러 번 냈으면 마지막 것
    for acc in cover:
        cik, p = sub[acc]
        if p == period and acc > latest.get(cik, ""):
            latest[cik] = acc
    use = set(latest.values())
    out = {c: {} for c in cusips}
    issuer = {}
    for r in rows("INFOTABLE.TSV"):
        c = (r.get("CUSIP") or "").upper()
        if c not in out or r["ACCESSION_NUMBER"] not in use:
            continue
        if (r.get("PUTCALL") or "").strip() or (r.get("SSHPRNAMTTYPE") or "SH").upper() != "SH":
            continue
        sh = fnum(r.get("SSHPRNAMT")) or 0
        val = fnum(r.get("VALUE")) or 0
        who = cover[r["ACCESSION_NUMBER"]]
        a = out[c].setdefault(who, [0, 0])
        a[0] += sh
        a[1] += val
        issuer[c] = r.get("NAMEOFISSUER")
    # 단가(평가액 ÷ 주식 수)가 다른 기관들과 3배 넘게 다르면(천 달러 단위 평가액은 허용) 입력 오류로 보고 뺀다
    # (예: 평가액을 주식 수 칸에 적은 보고서)
    clean = {}
    for c in cusips:
        px = sorted(v / s for s, v in out[c].values() if s and v)
        mid = px[len(px) // 2] if px else 0
        ok, bad = {}, []
        for who, (s, v) in out[c].items():
            r = v / s if s else 0
            fine = mid / 3 <= r <= mid * 3 or mid / 3000 <= r <= mid * 3 / 1000  # 평가액을 천 달러 단위로 낸 경우는 주식 수가 맞음
            if mid and s and not fine:
                bad.append(who)
            else:
                ok[who] = s
        clean[c] = ok
        print(f"  13F {period} {c} {issuer.get(c)}: 기관 {len(ok)}곳, 단가 이상치 제외 {len(bad)}곳 {bad[:5]}")
    return period, clean


def to_date(p):
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(p, fmt).date().isoformat()
        except ValueError:
            pass
    return p


def institutional(tgs):
    state_path = os.path.join(OUT, "_13f.json")
    state = load(state_path, {})
    cusips = sorted({t["cusip"] for t in tgs if t.get("cusip")})
    if not cusips:
        return state.get("by_cusip", {})
    sets = f13_sets()
    if len(sets) < 2:
        print("13F 데이터 세트 목록을 찾지 못했습니다.")
        return state.get("by_cusip", {})
    if state.get("sets") == sets and set(state.get("cusips", [])) >= set(cusips):
        print("13F: 새 데이터 세트 없음")
        return state["by_cusip"]
    print("13F 데이터 세트:", *sets)
    p_new, h_new = f13_holdings(sets[0], cusips)
    p_old, h_old = f13_holdings(sets[1], cusips)
    by = {}
    for c in cusips:
        cur, prev = h_new[c], h_old[c]
        top = sorted(cur.items(), key=lambda x: -x[1])[:10]
        by[c] = {
            "period": to_date(p_new), "prev_period": to_date(p_old),
            "holders": len(cur), "prev_holders": len(prev),
            "shares": sum(cur.values()), "prev_shares": sum(prev.values()),
            "top": [{"name": n, "sh": s, "chg": (s - prev[n]) if n in prev else None} for n, s in top],
        }
    save(state_path, {"sets": sets, "cusips": cusips, "by_cusip": by})
    return by


def main():
    if not UA:
        print("SEC_USER_AGENT가 없어 EDGAR 수집을 건너뜁니다.")
        return
    tgs = targets()
    tick = {r["ticker"].upper(): int(r["cik_str"])
            for r in json.loads(get("https://www.sec.gov/files/company_tickers.json")).values()}
    try:
        inst = institutional(tgs)
    except Exception as e:
        print(f"13F 실패(건너뜀): {e}")
        inst = load(os.path.join(OUT, "_13f.json"), {}).get("by_cusip", {})
    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    for t in tgs:
        cik = tick.get(t["ticker"].replace(".", "-")) or tick.get(t["ticker"])
        if not cik:
            print(f"{t['ticker']}: CIK를 찾지 못함")
            continue
        try:
            sub, ins = insider(cik)
        except Exception as e:
            print(f"{t['ticker']}: Form 4 실패 {e}")
            continue
        path = os.path.join(OUT, t["ticker"] + ".json")
        out = {"source": "SEC EDGAR", "ticker": t["ticker"], "cik": cik, "name": sub.get("name"),
               "profile": profile(sub), "filings": filings(sub, cik),
               "insider": ins, "inst": inst.get(t.get("cusip") or "")}
        old = load(path, {})
        if {k: v for k, v in old.items() if k != "updated_at"} != out:
            out["updated_at"] = now
            save(path, out)
        print(f"{t['ticker']}: 내부자 거래 {len(ins)}건, 13F {'있음' if out['inst'] else '없음'}")


if __name__ == "__main__":
    main()
