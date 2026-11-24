"""일회성 점검: 보정비율 이벤트마다 직전 거래일·당일 상장주식수 등을 출력 (점검 후 삭제)"""
import json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
import fetch_krx as F, krx_cross as K
key = os.environ["KRX_API_KEY"]
days = K._load_all(); ds = sorted(days)
last = {}; ev = []
for d in ds:
    for c, r in days[d].get("a", {}).items():
        ev.append((d, c, r, last.get(c)))
    for c in days[d]["c"]:
        last[c] = d
need = sorted({e[0] for e in ev} | {e[3] for e in ev if e[3]} | {ds[-1]})
rows = {}
for d in need:
    st = F.fetch_stocks(key, dt.date.fromisoformat(d))
    for mkt, rs in st.items():
        for r in rs:
            rows[(d, r["ISU_CD"])] = (mkt, r)
keys = ["ISU_NM", "TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_RT", "ACC_TRDVOL", "LIST_SHRS", "MKTCAP", "SECT_TP_NM"]
print("FIELDS", list(next(iter(rows.values()))[1].keys()))
uni = []
for mkt, (ep, cap) in F.STOCKS.items():
    for code, (m, r) in [(k[1], v) for k, v in rows.items() if k[0] == ds[-1] and v[0] == mkt]:
        if F.is_common_stock(r) and (F.num(r.get("MKTCAP")) or 0) >= cap and F.num(r.get("FLUC_RT")) is not None:
            uni.append(code)
print("UNIVERSE", json.dumps(uni))
for d, c, r, p in ev:
    a = rows.get((d, c), (None, {}))[1]; b = rows.get((p, c), (None, {}))[1] if p else {}
    print("EV", json.dumps({"d": d, "c": c, "r": r, "p": p, "now": {k: a.get(k) for k in keys}, "prev": {k: b.get(k) for k in keys if k != "SECT_TP_NM"}}, ensure_ascii=False))
