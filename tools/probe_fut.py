"""일회성 점검: KRX 선물 일별매매정보 응답 구조 확인. 키는 출력하지 않는다."""
import json, os, urllib.request, urllib.error
key = os.environ["KRX_API_KEY"].strip()
for ep in ["drv/fut_bydd_trd"]:
    for d in ["20261123", "20261120"]:
        req = urllib.request.Request(f"https://data-dbg.krx.co.kr/svc/apis/{ep}?basDd={d}", headers={"AUTH_KEY": key})
        try:
            rows = json.loads(urllib.request.urlopen(req, timeout=30).read().decode()).get("OutBlock_1", [])
        except urllib.error.HTTPError as e:
            print(ep, d, "HTTP", e.code, e.read().decode()[:200]); continue
        print(ep, d, "rows:", len(rows))
        if not rows: continue
        print("FIELDS:", list(rows[0].keys()))
        for k in rows[0]:
            vals = sorted({str(r.get(k)) for r in rows})
            if len(vals) <= 12: print("  ", k, vals)
        k200 = [r for r in rows if "코스피" in str(r) and "200" in str(r) and "미니" not in str(r)]
        print("KOSPI200 rows:", len(k200))
        for r in k200[:6]: print("  ", json.dumps(r, ensure_ascii=False))
