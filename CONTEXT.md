# YWK Research — 작업 맥락 정리 (2026-11-24 기준)

새 채팅에서 이 프로젝트를 이어 갈 때 읽는 인수인계 문서다. 사이트 빌드에서는 제외된다(`_config.yml` exclude).

## 1. 목표와 확정된 결정

- **목적:** 수익이 아니라 **개인 포트폴리오**용 시황 블로그. 금융권 지원 시 보여 줄 수 있게, 데이터 출처가 공식·합법적인 것을 원칙으로 한다.
- **사이트:** https://ywk-research.github.io (GitHub Pages, 저장소 `ywk-research/ywk-research.github.io`, `main` 브랜치 루트 배포, Jekyll)
- **발행 방식:** 매일 자동 생성되는 정리본 + 주 1회 사용자가 직접 쓰는 코멘트
- **매일 글 형식:** 제목 "개장 전 브리핑 | 전일 시황 종합 (YYYY.MM.DD 요일)", 한국시간 07:30~08:00 발행, 모든 수치는 **전 거래일 종가** 기준
  1. 미국 증시: S&P500, 나스닥, 다우, SOX, VIX
  2. 아시아 증시: 코스피, 닛케이, 상하이, 항셍 (기준일 열 표시, 휴장 시 주석)
  3. 금리·환율·원자재: 미 10년물, 달러인덱스, 원/달러, WTI, 금
  4. 주요 종목
  5. 전일 주요 뉴스: 🇺🇸🇨🇳🇯🇵🇰🇷 국가별로 나누고 전체 5~6개 불릿. 기사 시간·출처는 넣지 않음. 국가별 최소 1개는 중요한 게 있을 때만
  6. 오늘 일정 (한국시간): 미국은 주요 일정, 한·일·중은 "진짜 중요한 것만". 없으면 그 나라는 생략
- **뉴스 범위:** 전일 08:00 ~ 당일 07:00 (KST)
- **문체:** 해요체, 짧고 단정하게. 상승 `<span class="up">`, 하락 `<span class="down">`, 보합 `<span class="flat">`, 부호는 `+`, `−`(유니코드 마이너스)
- 글 예시: `_posts/2026-11-24-pre-market-briefing.md`. 앞부분(front matter)에 `title`, `date`(07:30 +0900), `summary`(목록에 보이는 한 줄 요약)

## 2. 사이트 구조

| 경로 | 역할 |
|---|---|
| `_config.yml` | 제목, permalink `/briefing/:year/:month/:day/`, kramdown GFM, `tools/` 등 빌드 제외 |
| `_layouts/default.html` | 헤더(브리핑·소개), 면책 문구 푸터, Pretendard 폰트, CSS 캐시 무효화 쿼리(`?v=site.time`). `page.wide: true`면 넓은 폭 |
| `_layouts/post.html` | 개별 브리핑(좁은 폭 760px), 이전·다음 글 |
| `index.html` | 첫 화면. `wide: true`. 데스크톱은 **왼쪽 데이터 / 오른쪽 브리핑(최신 + 지난 목록, 붙어서 따라옴)**. 1000px 이하에서는 최신 브리핑 → 데이터 → 지난 목록 |
| `_includes/global-index.html` | 해외 지수·금리·원자재 카드 9개 + 3개월 미니 차트(JS 렌더링, 다른 지표보다 기준일이 늦으면 '지연' 표시) |
| `_includes/krx-index.html` | 코스피·코스닥·VKOSPI 카드 + 차트(1개월/3개월/1년), 아래에 업종·종목 include |
| `_includes/krx-sectors.html` | 업종별 등락 가로 막대(코스피/코스닥 탭) |
| `_includes/krx-movers.html` | 종목 등락 상위·하위·거래대금 상위 5(탭) |
| `assets/css/style.css` | 상승 `--up #d93a3a`, 하락 `--down #2563eb`, 다크모드 지원 |
| `about.md` | 소개. **운영자 소개 부분은 아직 비어 있음** |
| Chart.js | `index.html`의 데이터 칸 맨 앞에서 CDN으로 1번만 불러옴 |

## 3. 데이터 자동 업데이트

**워크플로:** `.github/workflows/krx-index.yml` (이름: "시장 데이터 업데이트")
- 평일 한국시간 08:30, 18:30 + 수동 실행(Actions → Run workflow, 또는 `gh api -X POST repos/ywk-research/ywk-research.github.io/actions/workflows/krx-index.yml/dispatches -f ref=main`)
- 순서: `tools/fetch_krx.py` → `tools/fetch_global.py`(실패해도 계속) → `_data/` 변경분 커밋 → Pages 재빌드 요청

**GitHub Secrets (키는 채팅에 절대 붙이지 않음, 스크립트도 키를 출력하지 않음)**
- `KRX_API_KEY`, `FRED_API_KEY`, `TWELVEDATA_API_KEY`

**KRX Open API** (`https://data-dbg.krx.co.kr/svc/apis/{경로}?basDd=YYYYMMDD`, 헤더 `AUTH_KEY`)

| 경로 | 쓰는 곳 | 승인 |
|---|---|---|
| `idx/kospi_dd_trd`, `idx/kosdaq_dd_trd` | 코스피·코스닥 지수 + 업종지수(`krx_sectors.json`) | ✅ |
| `idx/drvprod_dd_trd` | VKOSPI("코스피 200 변동성지수") | ✅ |
| `sto/stk_bydd_trd`, `sto/ksq_bydd_trd` | 종목 등락·거래대금(`krx_movers.json`). 순위 조건: 코스피 시총 1조↑, 코스닥 3천억↑, 우선주(코드 끝자리≠0)·스팩 제외 | ✅ |
| `drv/fut_bydd_trd` | 코스피200 선물. **아직 사이트에 안 붙임** (아래 5번) | ✅ |
| 투자자별 거래실적 | 외국인·기관 수급. **엔드포인트·출력 항목 미확인** | 신청 여부 확인 필요 |

- 저작권은 KRX에 있으므로 출처 표기 "자료: 한국거래소(KRX) Open API" 필수
- 업종 판별은 `fetch_krx.py`의 `NON_SECTOR` 단어 목록으로 함(코스피 24개, 코스닥 22개 업종이 잡힘)

**해외 (`tools/fetch_global.py` → `_data/global_index.json`)**

| 지표 | 출처 | 비고 |
|---|---|---|
| VIX | Cboe 공식 CSV (키 불필요) | 최신 |
| 미 10년물 | 미 재무부 공식 CSV (키 불필요) | 최신 |
| 다우 | FRED `DJIA` | 최신 |
| S&P500, 나스닥 | FRED `SP500`, `NASDAQCOM` | **1~3일 늦음** |
| 닛케이225 | FRED `NIKKEI225` | 대체로 최신 |
| WTI | FRED `DCOILWTICO` | **약 1주 늦음** (미 에너지정보청 자료) |
| 금, 원/달러 | Twelve Data `XAU/USD`, `USD/KRW` | ⚠️ **약관 문제, 빼야 함** (아래) |

## 4. 데이터 권리 관련 정리 (중요)

- **Twelve Data 무료(Basic) 플랜은 내부 비표시 용도만 허용.** 공개 사이트에 보여 주는 건 약관 위반이라 금·원/달러 카드를 빼야 함. 지수는 무료 플랜에서 안 나오고, ETF로 대신 보여 주는 것도 같은 이유로 불가
- **대체안:** 원/달러는 한국은행 ECOS Open API 매매기준율(키 발급 필요), 금은 KRX "일반상품 → 금시장 일별매매정보"(KRX 키로 신청)
- **네이버 금융·야후 파이낸스:** 공식 API가 없음. 수집이나 공개 재게시는 약관 위반이라 쓰지 않기로 함. 참고로 비교했던 fx-desk-dashboard.onrender.com은 Render 서버에서 네이버·yfinance를 긁어 오는 방식
- **실시간 시세:** 공개 사이트에 띄우려면 거래소 시세 이용 계약(유료)이 필요. 그래서 전 거래일 종가만 다룸
- **Naver는 Claude 작업 환경(웹 조회, 내장 브라우저, Chrome 확장 모두)에서 안전 제한으로 접근이 막혀 있음.** 우회하지 않음
- 무료 API 검토 결과: 구글 파이낸스(API 없음), Stooq(2026년 3월부터 CAPTCHA 키 필요, 불안정), NYSE·S&P DJI·나스닥(유료 라이선스)

## 5. 남은 작업 / 결정 대기

1. **[바로] 금·원/달러(Twelve Data) 카드 제거** → `fetch_global.py`의 SERIES에서 `gold`, `usdkrw` 삭제 (사용자 확인 대기 중이었음)
2. **원/달러, 금 공식 출처로 재부착:** 사용자가 ECOS 키(`ECOS_API_KEY`)를 발급하고 KRX 금시장 서비스를 신청한 뒤
3. **코스피200 선물 섹션:** `drv/fut_bydd_trd` 응답 확인 완료
   - 필드: `PROD_NM`("코스피200 선물"), `MKT_NM`("정규"/"야간"), `ISU_NM`(예: "코스피200 F 202612 (주간)"), `TDD_CLSPRC`, `CMPPREVDD_PRC`, `SPOT_PRC`, `SETL_PRC`, `ACC_TRDVOL`, `ACC_OPNINT_QTY`
   - 야간장은 **다음 거래일 날짜로** 기록됨. 예: '11/23 야간' = 금요일 11/20 밤 세션. 그런데 basDd=당일 데이터는 오후까지도 0건이라, 아침 8:30에는 '어젯밤 야간선물'을 받을 수 없음
   - 제안(결정 대기): 사이트에는 **정규장 최근월물 종가 + 베이시스(선물−현물) + 미결제약정**, 어젯밤 야간선물은 브리핑 본문에 한 줄로
4. **투자자별 수급(외국인·기관·개인):** 사용자가 "투자자별 거래실적" 서비스의 영문 코드와 출력 항목을 보내 주면 붙임. 계획: 전일 순매수 막대 + 20일 누적 추이. 위치는 업종별 등락 위
5. **S&P500·나스닥·WTI 지연:** 무료·공식으로는 해결책이 없음. 현재 '지연' 표시로 둠
6. **매일 자동 발행:** 예약 작업으로 매일 07:30 전에 글을 생성해 커밋. 결정할 것: 원/달러 기준(서울 종가 vs 역외 포함 종가. 11/24 글은 서울 종가, 11/16~11/23 백필은 Investing.com 역외 포함 종가), 뉴스·해외 시세를 어디서 읽을지(Investing.com은 사용자 PC의 내장 브라우저가 필요해 PC가 켜져 있어야 함)
7. `about.md` 운영자 소개 작성
8. (선택) 나만 보는 실시간 대시보드: 증권사 API(한국투자 KIS 등)로 PC에서 로컬 실행

## 6. 작업 요령

- 저장소 클론: `git clone https://github.com/ywk-research/ywk-research.github.io` (세션에 저장소를 push 권한으로 연결해야 함). 푸시는 `git -c push.negotiate=true push`
- 커밋 메시지는 한국어. 세션이 요구하는 공동 작성자 표기를 끝에 붙임
- **로컬 Jekyll 빌드 불가**(작업 환경에서 rubygems 차단). 푸시 → Pages 빌드(약 1~2분) → 내장 브라우저로 `https://ywk-research.github.io/?v=숫자`를 열어 확인
- **Liquid 주의:** `{% for x in "a,b" | split: "," %}`처럼 for 문 안에서 필터는 동작하지 않음. 먼저 `assign`
- KRX·FRED 등 외부 API는 작업 환경에서 직접 접속이 막혀 있음. 확인이 필요하면 GitHub Actions에 일회성 점검 워크플로를 올려 실행하고 `gh api .../actions/jobs/{id}/logs`로 로그를 읽은 뒤 삭제
- `gh` CLI는 GraphQL이 막혀 있으니 REST(`gh api`)만 씀. Pages 관련 API 경로는 프록시에서 막힘
- 백필 생성 스크립트 `make_backfill.py`는 저장소 밖(이전 세션 작업 폴더)에 있었음. 필요하면 새로 작성
- 이전 시장조사 결과(시황 블로그 25곳): Sheets 아티팩트 https://claude.ai/artifact/8qrEMKPJPuKTKQqASq47gF

## 7. 사용자 선호

- 한국어, 짧고 바로 쓸 수 있게. 확인이 필요한 건 질문 하나로
- 계정 설정, 키 등록, 서비스 신청은 사용자가 직접 함. 키는 채팅에 붙이지 않음
- 데이터는 공식·합법 출처 우선. 약관 위반 소지가 있으면 먼저 알려 줌
