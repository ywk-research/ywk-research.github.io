"""Gemini API(구글 검색 그라운딩) 호출 도우미. 표준 라이브러리만 쓴다.

- 키: 환경변수 GEMINI_API_KEY (GitHub Secrets). 키는 출력하지 않는다.
- 모델: 환경변수 GEMINI_MODELS(쉼표 구분, 앞에서부터 시도). 기본 gemini-2.5-flash → gemini-2.5-flash-lite
- ask()는 (답 텍스트, 검색에 실제로 쓰인 출처 목록)을 돌려준다.
  출처 목록은 그라운딩 메타데이터의 링크를 실제 주소로 풀어 둔 것이라,
  모델이 쓴 출처가 이 목록(같은 사이트)에 없으면 "검색으로 확인하지 않은 것"으로 보고 버릴 수 있다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
UA = "Mozilla/5.0 (compatible; desk-notes-bot/1.0; +https://ywk-research.github.io)"


def models():
    return [m.strip() for m in os.environ.get("GEMINI_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite").split(",") if m.strip()]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def resolve(uri, timeout=10):
    """그라운딩 링크(vertexaisearch.cloud.google.com/grounding-api-redirect/…)를 실제 주소로"""
    if "grounding-api-redirect" not in uri:
        return uri
    op = urllib.request.build_opener(_NoRedirect)
    try:
        op.open(urllib.request.Request(uri, method="HEAD", headers={"User-Agent": UA}), timeout=timeout)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        if loc:
            return loc
    except Exception:
        pass
    return None


def host(url):
    try:
        h = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""
    return h.lower().removeprefix("www.")


def same_site(a, b):
    """도메인 끝 두 마디(예: prnewswire.com)가 같으면 같은 사이트"""
    ha, hb = host(a) or a.lower(), host(b) or b.lower()
    if not ha or not hb:
        return False
    if ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha):
        return True
    return ha.split(".")[-2:] == hb.split(".")[-2:] and len(ha.split(".")[-2]) > 3


def fetch_text(url, timeout=15, limit=600_000):
    """페이지 본문(태그 제거). 막히면 None"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status >= 400:
                return None
            raw = r.read(limit).decode("utf-8", "replace")
    except Exception:
        return None
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))


def ask(prompt, temperature=0.2, timeout=120):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY 없음")
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": temperature},
    }).encode()
    last = None
    for model in models():
        for attempt in range(3):
            req = urllib.request.Request(API.format(model=model), data=body, method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    res = json.load(r)
            except urllib.error.HTTPError as e:
                last = f"{model} HTTP {e.code}"
                print(f"  Gemini {last}")
                if e.code in (429, 500, 503) and attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                break  # 다음 모델
            except Exception as e:
                last = f"{model} {type(e).__name__}"
                print(f"  Gemini {last}")
                time.sleep(5)
                continue
            cand = (res.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
            gm = cand.get("groundingMetadata") or {}
            srcs = []
            for ch in gm.get("groundingChunks", []):
                w = ch.get("web") or {}
                url = resolve(w.get("uri", "")) if w.get("uri") else None
                srcs.append({"title": w.get("title", ""), "url": url})
            print(f"  Gemini {model}: 검색 {len(gm.get('webSearchQueries', []))}회, 출처 {len(srcs)}곳")
            if not text.strip():
                last = f"{model} 빈 응답"
                break
            return text, srcs
    raise RuntimeError(f"Gemini 호출 실패: {last}")


def parse_json(text):
    """답에서 JSON 덩어리를 꺼낸다(```json … ``` 또는 맨 바깥 {…}/[…])"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    s = m.group(1) if m else text
    i = min([x for x in (s.find("{"), s.find("[")) if x >= 0], default=-1)
    if i < 0:
        raise ValueError("JSON 없음")
    end = s.rfind("}" if s[i] == "{" else "]")
    return json.loads(s[i:end + 1])


def grounded_url(src, srcs):
    """모델이 적은 출처 URL을 검증한다. 검색 출처 중 같은 사이트가 있어야 하고,
    URL이 열리면 그대로, 안 열리면 같은 사이트의 검색 출처 URL로 바꾼다. 검증 실패면 None"""
    if not src or not src.startswith("http"):
        return None
    same = [s["url"] for s in srcs if s.get("url") and same_site(src, s["url"])]
    if not same:
        # 출처 제목(도메인)만 있고 주소를 못 푼 경우
        if not any(s.get("title") and same_site(src, s["title"]) for s in srcs):
            return None
    for u in same:
        if u.split("#")[0].rstrip("/") == src.split("#")[0].rstrip("/"):
            return u
    if fetch_text(src) is not None:
        return src
    if not same:
        return None
    words = lambda u: set(re.findall(r"[a-z0-9]{3,}", urllib.parse.urlparse(u).path.lower()))
    return max(same, key=lambda u: len(words(u) & words(src)))
