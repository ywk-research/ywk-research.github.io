"""전 거래일 상장 미국 ETF 첫 거래 확인 (Gemini 검색 그라운딩).

_data/new_etf.json의 후보(us.items 중 ld == us.prev_day)를 Gemini에 구글 검색으로 확인시키고,
아래를 모두 통과한 것만 _data/new_etf_confirmed.yml의 D 항목에 적는다("확인된 것만" 원칙).
  1. 모델이 첫 거래일을 D라고 답했고, 근거 종류가 운용사(issuer)·보도자료 배포처(newswire)·거래소(exchange)
  2. 근거 URL이 실제 검색 출처(그라운딩)와 같은 사이트이고, 시세 사이트(Yahoo·etf.com 등)가 아님
  3. 근거 페이지가 열리면 그 안에 티커나 펀드 이름이 있음
D 항목이 이미 checked: true면 아무것도 하지 않는다(다른 작업이 먼저 채움). 예전 날짜 항목은 건드리지 않는다.

사용: python tools/gemini_etf.py [--dry-run]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gemini  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "_data", "new_etf_confirmed.yml")
BANNED = ("yahoo.", "naver.", "etf.com", "etfdb.", "stockanalysis.", "marketwatch.", "investing.com",
          "seekingalpha.", "morningstar.", "zacks.", "finviz.", "tradingview.", "google.", "wikipedia.")
OK_KIND = {"issuer", "newswire", "exchange"}
BATCH = 6

PROMPT = """You verify US ETF listings. Use Google Search.
Target date D = {d} (US Eastern). For EACH candidate below, decide whether the ETF began trading on a US exchange
for the FIRST time exactly on D.

Acceptable evidence ONLY (kind):
- "issuer": the fund sponsor's own website (press release, fund page showing inception/listing date)
- "newswire": the sponsor's press release on Business Wire, PR Newswire, GlobeNewswire, Accesswire etc.
- "exchange": an official exchange listing notice (NYSE, Nasdaq, Cboe)
Do NOT use quote/data sites (Yahoo, etf.com, ETFdb, Morningstar, StockAnalysis, Seeking Alpha) as evidence.
If you cannot find acceptable evidence that trading started on D, set confirmed=false. Never guess.
A mutual fund converting into an ETF that first trades on D counts (note "conversion").
A transfer of an existing ETF to another exchange, or a cross-listing, does NOT count.

Candidates (from SEC Form 8-A filings; ticker may be blank):
{cands}

Answer with ONLY a JSON array, one object per candidate, in this shape:
[{{"i": 1, "confirmed": true, "first_trade_date": "YYYY-MM-DD", "ticker": "ABCD", "name": "official fund name",
  "issuer": "sponsor brand", "exchange": "NYSE Arca|Nasdaq|Cboe|NYSE", "kind": "issuer|newswire|exchange",
  "src": "exact URL of the evidence page", "conversion": false}}]
"""

DISCOVER = """Use Google Search. List US-listed ETFs that began trading for the first time on {d} (US Eastern),
which are NOT in this list: {known}.
Only include a fund if you found the sponsor's press release (own site or Business Wire / PR Newswire / GlobeNewswire)
or an official exchange notice stating it launched/began trading on {d}. Exclude exchange transfers and cross-listings.
If none, answer [].
Answer with ONLY a JSON array: [{{"confirmed": true, "first_trade_date": "YYYY-MM-DD", "ticker": "ABCD",
"name": "official fund name", "issuer": "sponsor brand", "exchange": "NYSE Arca|Nasdaq|Cboe|NYSE",
"kind": "issuer|newswire|exchange", "src": "exact URL", "conversion": false}}]
"""


def date_block(text, d):
    """yml에서 "D": 블록이 있는지, checked: true인지"""
    m = re.search(rf'^"{d}":\n((?:[ #].*\n|\n)*)', text, re.M)
    if not m:
        return None
    return bool(re.search(r"^\s+checked:\s*true", m.group(1), re.M))


def check(r, d, srcs, why):
    """모델 답 한 건을 검증해 통과하면 (항목 dict), 아니면 None(이유를 why에 남김)"""
    label = r.get("ticker") or r.get("name") or "?"
    if not r.get("confirmed"):
        why.append(f"{label}: 근거 없음(모델 미확인)")
        return None
    if r.get("first_trade_date") != d:
        why.append(f"{label}: 첫 거래일 {r.get('first_trade_date')} ≠ {d}")
        return None
    if r.get("kind") not in OK_KIND:
        why.append(f"{label}: 근거 종류 {r.get('kind')}")
        return None
    src = (r.get("src") or "").strip()
    if any(b in gemini.host(src) for b in BANNED):
        why.append(f"{label}: 시세 사이트 근거 {gemini.host(src)}")
        return None
    url = gemini.grounded_url(src, srcs)
    if not url or any(b in gemini.host(url) for b in BANNED):
        why.append(f"{label}: 근거 URL이 검색 출처에 없음 ({gemini.host(src)})")
        return None
    tk = (r.get("ticker") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,6}", tk):
        why.append(f"{label}: 티커 형식 이상")
        return None
    page = gemini.fetch_text(url)
    if page is not None:
        name_hit = r.get("name") and r["name"].lower()[:30] in page.lower()
        if not re.search(rf"\b{tk}\b", page) and not name_hit:
            why.append(f"{label}: 근거 페이지에 티커·이름 없음")
            return None
    e = {"ticker": tk, "name": (r.get("name") or "").strip(), "issuer": (r.get("issuer") or "").strip(),
         "exchange": (r.get("exchange") or "").strip(), "src": url}
    if r.get("conversion"):
        e["note"] = "뮤추얼펀드 전환"
    return e


def q(s):
    s = str(s)
    return json.dumps(s, ensure_ascii=False) if re.search(r"[:#'\"\[\]{}&*!|>%@`]|^\s|\s$", s) else s


def to_yaml(d, etfs, partial=False):
    L = [f'"{d}":', "  checked: true"] + (["  partial: true"] if partial else []) + ["  by: gemini"]
    if not etfs:
        L.append("  etfs: []")
        return "\n".join(L) + "\n"
    L.append("  etfs:")
    for e in etfs:
        L.append(f"    - ticker: {q(e['ticker'])}")
        for k in ("name", "issuer", "exchange", "src", "note"):
            if e.get(k):
                L.append(f"      {k}: {q(e[k])}")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과만 출력")
    a = ap.parse_args()
    p = os.path.join(ROOT, "_data", "new_etf.json")
    if not os.path.exists(p):
        print("new_etf.json 없음 — 끝")
        return 0
    us = json.load(open(p, encoding="utf-8")).get("us", {})
    d = us.get("prev_day")
    if not d:
        print("prev_day 없음 — 끝")
        return 0
    conf = open(CONF, encoding="utf-8").read()
    state = date_block(conf, d)
    if state and not a.dry_run:
        print(f"{d} 이미 확인됨 — 끝")
        return 0
    print(f"전 거래일 D = {d}, 후보 자료 갱신일 {us.get('updated')}")
    cands = [x for x in us.get("items", []) if x.get("ld") == d]
    print(f"후보 {len(cands)}개: " + ", ".join(x.get("ticker") or x["name"] for x in cands))

    found, why, disc_ok = [], [], False
    for i in range(0, len(cands), BATCH):
        part = cands[i:i + BATCH]
        lines = "\n".join(f"{j + 1}. name: {c['name']} | ticker: {c.get('ticker') or '(blank)'} | trust: {c.get('issuer')}"
                          f" | exchange: {c.get('exchange')}" for j, c in enumerate(part))
        try:
            text, srcs = gemini.ask(PROMPT.format(d=d, cands=lines))
            res = gemini.parse_json(text)
        except Exception as e:
            print(f"  확인 실패: {e}")
            why += [f"{c.get('ticker') or c['name']}: Gemini 오류" for c in part]
            continue
        for r in res if isinstance(res, list) else []:
            e = check(r, d, srcs, why)
            if e:
                found.append(e)

    try:
        known = ", ".join(sorted({c.get("ticker") or c["name"] for c in cands} | {e["ticker"] for e in found})) or "(none)"
        text, srcs = gemini.ask(DISCOVER.format(d=d, known=known))
        res = gemini.parse_json(text)
        for r in res if isinstance(res, list) else []:
            if r.get("ticker", "").upper() in {e["ticker"] for e in found}:
                continue
            e = check(r, d, srcs, why)
            if e:
                found.append(e)
        disc_ok = True
    except Exception as e:
        print(f"  추가 검색 실패: {e}")

    print(f"확인 {len(found)}개")
    for e in found:
        print(f"  ✓ {e['ticker']} {e['name']} ({e['issuer']}, {e['exchange']}) {e['src']}")
    for w in why:
        print(f"  ✗ {w}")
    if any("Gemini 오류" in w for w in why) or (not found and not disc_ok):
        # 확인을 끝까지 못 했는데 checked로 적으면 남은 후보가 숨겨지므로 기록하지 않는다(다음 실행이 다시 시도)
        print("Gemini 호출 일부가 실패해 기록하지 않음")
        return 1
    block = to_yaml(d, found)
    if a.dry_run:
        print("--- 기록할 내용(dry-run) ---\n" + block)
        return 0
    conf = open(CONF, encoding="utf-8").read()  # 다시 읽기
    if date_block(conf, d):
        print(f"{d} 그새 확인됨 — 끝")
        return 0
    if date_block(conf, d) is False:  # checked 없이 있는 항목은 그대로 두고 기록하지 않음(손으로 쓴 것)
        print(f"{d} 항목이 checked 없이 있음 — 건드리지 않음")
        return 0
    with open(CONF, "a", encoding="utf-8") as f:
        f.write(("" if conf.endswith("\n") else "\n") + block)
    print(f"기록: {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
