"""개장 전 브리핑 글의 "전일 주요 뉴스"와 주요 종목 이유 한 줄을 Gemini(구글 검색 그라운딩)로 채운다.

make_briefing.py가 만든 초안(_posts/P-pre-market-briefing.md)을 고친다. 숫자는 건드리지 않는다.
- 뉴스: 전일 08:00 ~ 당일 07:00(KST), 🇺🇸🇨🇳🇯🇵🇰🇷 순, 전체 최대 6개. 해요체, 기사 문장 복사 금지.
- 종목: "- **이름 <span…>±x.xx%</span> (가격)**" 줄 끝에 ":** 이유" 한 줄.
확인 원칙: 출처 URL이 실제 검색 출처(그라운딩)와 같은 사이트가 아니면 그 항목은 버린다(추정 금지).

사용: python tools/gemini_brief.py [--date YYYY-MM-DD] [--dry-run]
종료 코드: 0 뉴스 3개 이상 채움, 2 일부만 채움(뉴스 3개 미만), 1 파일·키 문제
"""
import argparse
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gemini  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = dt.timezone(dt.timedelta(hours=9))
FLAGS = [("us", "🇺🇸 미국"), ("cn", "🇨🇳 중국"), ("jp", "🇯🇵 일본"), ("kr", "🇰🇷 한국")]
STOCK = re.compile(r'^- \*\*(?P<name>.+?) (?P<chg><span class="(?:up|down|flat)">[^<]+</span>) \((?P<px>[^)]+)\)\*\*\s*$')

NEWS_PROMPT = """너는 한국어 시황 브리핑 편집자야. 구글 검색으로 확인해.
기간: {start} ~ {end} (한국시간). 이 기간에 나온 미국·중국·일본·한국의 시장에 중요한 뉴스를 골라.
대상: 중앙은행·정부 발표(연준·재무부·BLS·BEA, 인민은행·국가통계국, 일본은행·재무성, 한국은행·기재부·금융위),
주요 경제지표 결과, 시장을 움직인 대형 기업 발표(실적·공시·보도자료).

규칙
- 전체 5~6개. 나라별로 정말 중요한 게 있을 때만(없는 나라는 빼).
- 각 항목은 한국어 해요체 1~2문장, 짧고 단정하게("…했어요.", "…이에요."). 기사 문장을 번역·복사하지 말고 사실만 네 말로 다시 써.
- 숫자는 출처에서 직접 확인한 것만. 추측·전망·루머는 넣지 마.
- 기사 시각, 매체 이름은 문장에 쓰지 마.
- src는 그 사실을 확인한 페이지 URL. 공식 1차 자료(기관·회사 IR/보도자료)가 있으면 그걸, 없으면 주요 통신사·경제매체 기사.
- date는 그 일이 발표·발생한 한국 날짜.

JSON 배열만 답해:
[{{"c": "us|cn|jp|kr", "text": "…", "date": "YYYY-MM-DD", "src": "URL", "kind": "official|company|news"}}]
"""

STOCK_PROMPT = """구글 검색으로 확인해. 아래 종목이 표시한 날짜(현지 거래일)에 왜 그만큼 움직였는지 각각 이유를 찾아.
{rows}

규칙
- 이유는 한국어 해요체 한 문장, 45자 이내("…에 올랐어요.", "…로 내렸어요."). 기사 문장을 복사하지 마.
- 그날 그 종목의 움직임을 직접 설명한 기사·공시에서 확인한 것만. 못 찾으면 reason을 null로.
- 등락률·주가 숫자는 문장에 쓰지 마(이미 표시돼 있어).
JSON 배열만 답해: [{{"i": 1, "reason": "… 또는 null", "src": "URL"}}]
"""


def front(text, key):
    m = re.search(rf'^{key}:\s*"?([^"\n]+)"?\s*$', text, re.M)
    return m.group(1) if m else None


def clean(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    s = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", s)  # [1] 같은 인용 표시 제거
    return s.replace("**", "").replace("<", "").replace(">", "").strip()


def fill_news(pub, srcs_log):
    start = dt.datetime.combine(pub - dt.timedelta(days=1), dt.time(8, 0))
    end = dt.datetime.combine(pub, dt.time(7, 0))
    text, srcs = gemini.ask(NEWS_PROMPT.format(start=f"{start:%Y-%m-%d %H:%M}", end=f"{end:%Y-%m-%d %H:%M}"))
    items = gemini.parse_json(text)
    ok_days = {(pub - dt.timedelta(days=i)).isoformat() for i in (0, 1)}
    # 미국 장 시간 뉴스는 현지 날짜로 적는 경우가 있어 하루 더 허용(주말 뒤 월요일 발간이면 금요일까지)
    ok_days |= {(pub - dt.timedelta(days=i)).isoformat() for i in (2, 3)} if pub.weekday() == 0 else {(pub - dt.timedelta(days=2)).isoformat()}
    out = {c: [] for c, _ in FLAGS}
    for it in items if isinstance(items, list) else []:
        c, body = it.get("c"), clean(it.get("text"))
        if c not in out or not body:
            continue
        if it.get("date") not in ok_days:
            srcs_log.append(f"뉴스 제외(날짜 {it.get('date')}): {body[:30]}")
            continue
        url = gemini.grounded_url((it.get("src") or "").strip(), srcs)
        if not url:
            srcs_log.append(f"뉴스 제외(출처 미확인 {gemini.host(it.get('src') or '')}): {body[:30]}")
            continue
        if not re.search(r"(요|다)\.?$", body):
            srcs_log.append(f"뉴스 제외(문체): {body[:30]}")
            continue
        out[c].append(body)
        srcs_log.append(f"뉴스 ✓ [{c}] {url}")
    n, L = 0, ["## 전일 주요 뉴스", ""]
    for c, label in FLAGS:
        lst = out[c][:max(0, 6 - n)]
        if not lst:
            continue
        L += [f"**{label}**"] + [f"- {x}" for x in lst] + [""]
        n += len(lst)
    return ("\n".join(L).rstrip() + "\n") if n else "", n


def fill_stocks(lines, us_d, kr_d, srcs_log):
    idx = [(i, STOCK.match(l)) for i, l in enumerate(lines)]
    idx = [(i, m) for i, m in idx if m]
    if not idx:
        return 0
    rows = []
    for j, (i, m) in enumerate(idx):
        kr = m.group("px").endswith("원")
        pct = re.sub(r"<[^>]+>", "", m.group("chg")).replace("−", "-")
        rows.append(f"{j + 1}. {m.group('name')} ({'한국' if kr else '미국'} 상장) {pct}, 날짜 {kr_d if kr else us_d}")
    text, srcs = gemini.ask(STOCK_PROMPT.format(rows="\n".join(rows)))
    res = gemini.parse_json(text)
    n = 0
    for r in res if isinstance(res, list) else []:
        try:
            j = int(r.get("i")) - 1
        except Exception:
            continue
        reason = clean(r.get("reason") or "")
        if not (0 <= j < len(idx)) or not reason or reason.lower() == "null":
            continue
        if len(reason) > 70 or re.search(r"\d+(\.\d+)?%", reason):
            srcs_log.append(f"종목 제외(형식): {reason[:30]}")
            continue
        url = gemini.grounded_url((r.get("src") or "").strip(), srcs)
        if not url:
            srcs_log.append(f"종목 제외(출처 미확인): {idx[j][1].group('name')}")
            continue
        i, m = idx[j]
        lines[i] = f"- **{m.group('name')} {m.group('chg')} ({m.group('px')}):** {reason}"
        srcs_log.append(f"종목 ✓ {m.group('name')} {url}")
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--note-if-empty", action="store_true", help="뉴스를 하나도 못 채우면 '자동 정리…' 안내 한 줄을 붙임")
    a = ap.parse_args()
    pub = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(KST).date()
    path = os.path.join(ROOT, "_posts", f"{pub.isoformat()}-pre-market-briefing.md")
    if not os.path.exists(path):
        print(f"글 없음: {path}")
        return 1
    text = open(path, encoding="utf-8").read()
    us_d, kr_d = front(text, "us_day"), front(text, "kr_day")
    log, n_news, n_stock = [], 0, 0

    try:
        news, n_news = fill_news(pub, log)
    except Exception as e:
        print(f"뉴스 실패: {e}")
        news = ""
    if "<!-- NEWS -->" in text:
        text = text.replace("<!-- NEWS -->\n", news if news else "", 1)

    lines = text.split("\n")
    try:
        n_stock = fill_stocks(lines, us_d, kr_d, log)
    except Exception as e:
        print(f"종목 이유 실패: {e}")
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if a.note_if_empty and not n_news and "class=\"note\">※ 자동 정리" not in text:
        note = ('<p class="note">※ 자동 정리 단계가 시간 안에 끝나지 않아 공식 자료로 받은 수치만 먼저 올렸어요. '
                '뉴스는 확인되는 대로 추가해요.</p>\n\n')
        text = text.replace("## 전 거래일 신규 상장 ETF", note + "## 전 거래일 신규 상장 ETF", 1)

    for x in log:
        print("  " + x)
    print(f"뉴스 {n_news}개, 종목 이유 {n_stock}개")
    if a.dry_run:
        print("--- 결과(dry-run) ---\n" + text)
    else:
        open(path, "w", encoding="utf-8").write(text)
    return 0 if n_news >= 3 else 2


if __name__ == "__main__":
    sys.exit(main())
