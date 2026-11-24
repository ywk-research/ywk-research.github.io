---
layout: default
title: 데이터·방법론
title_en: Data & Methodology
permalink: /methodology/
description: Desk Notes가 쓰는 데이터의 출처, 자동 수집 방식, 가공 방법과 원칙
---

<div class="ko-only doc-table" markdown="1">

# 데이터·방법론

이 사이트의 숫자가 어디서 오고 어떻게 가공되는지 정리했어요. 코드는 모두 [GitHub 저장소](https://github.com/ywk-research/ywk-research.github.io)에 공개돼 있어요.

## 원칙

- **공식·합법 출처만 씁니다.** 거래소·중앙은행·정부 기관의 Open API와 공개 파일을 쓰고, 포털 사이트 시세를 긁어 오는 방식(스크래핑)은 쓰지 않아요.
- **전 거래일 종가 기준입니다.** 실시간 시세를 공개 사이트에 다시 보여 주려면 거래소와 유료 계약이 필요해서, 직접 수집하는 데이터는 모두 마감 값이에요.
- **약관을 먼저 확인합니다.** 무료 플랜이 '내부 비표시 용도'만 허용하는 상용 데이터(예: Twelve Data 무료 플랜)는 검토 후 쓰지 않기로 했어요.
- **출처와 기준일을 카드마다 적습니다.** 기준일이 다른 지표와 다르면 '지연'으로 표시해요.

## 자동 수집

GitHub Actions가 평일 한국시간 **07:00, 08:30, 18:30**에 수집 스크립트(`tools/`)를 실행하고, 바뀐 데이터(`_data/`)를 커밋한 뒤 사이트를 다시 빌드해요. 한 출처가 실패해도 나머지는 계속 진행되고, 빠진 날짜는 다음 실행 때 채워요. API 키는 저장소 Secrets에만 두고 로그에도 출력하지 않아요.

## 데이터 출처

| 영역 | 데이터 | 출처 |
|---|---|---|
| 국내 주식 | 코스피·코스닥·업종 지수, VKOSPI, 종목 시세·거래대금, ETF, 금 현물 | 한국거래소(KRX) Open API |
| 금리 | 미 국채 수익률 곡선, 실질금리(TIPS) | 미 재무부(Treasury) |
| 금리 | 국고채 수익률, 기준금리, 콜·CD·CP | 한국은행 ECOS |
| 금리 | 미 연방기금 목표 상단 | FRED(연준) |
| 환율 | 원/달러·원/엔·원/위안·원/유로 매매기준율 | 한국은행 ECOS |
| 변동성 | VIX | Cboe |
| 원자재 | WTI·브렌트·천연가스 현물 | 미 에너지정보청(EIA), FRED 경유 |
| 원자재 | 월간 원자재 가격 | 세계은행(World Bank) Pink Sheet |
| 경제지표 | 한국 월간 지표 | 한국은행 ECOS |
| 일정 | 미 GDP·PCE 발표 일정 | 미 경제분석국(BEA) |
| 일정 | 기타 주요국 발표·금리 결정 일정 | 각 기관 공개 일정을 직접 정리 |
| 참고 위젯 | 해외 지수·섹터·뉴스·경제 캘린더 | TradingView 무료 위젯(지연 시세, 표시만) |

KRX 데이터의 저작권은 한국거래소에 있어요.

## 가공 방법

- **종목 순위:** 코스피 시가총액 1조 원 이상, 코스닥 3,000억 원 이상. 우선주와 스팩은 뺐어요.
- **업종:** KRX 지수 목록에서 업종 지수만 골라(코스피 24개, 코스닥 22개) 전일 대비 등락률로 정렬해요.
- **이동평균 크로스:** 20·60일, 50·200일 골든·데드크로스를 최근 5거래일 기준으로 찾아요. 과거 종가는 매일 쌓아 두고, 액면분할·병합·무상증자·유상증자 권리락은 KRX 기준가(종가 − 전일 대비)와 직전 종가를 비교해 보정해요. 배당락은 보정하지 않아요.
- **52주 신고가·신저가:** 같은 보정 종가로 종목별 52주 범위를 계산해 매일 개수를 세요.
- **백테스트:** 정해 둔 비중으로 들고 있었을 때의 가격 수익률이에요. 배당·거래비용·세금은 넣지 않았어요. 월간 리밸런싱은 매월 첫 종가에 비중을 맞추고, 샤프 비율은 (연환산 수익률 − CD 91일물 금리) ÷ 연환산 변동성이에요.
- **금리 변동**은 bp, **가격 변동**은 전일 대비 %로 표시해요.

## 색 규칙

한국어 화면은 한국식(상승 빨강·하락 파랑), 영어 화면은 미국식(상승 초록·하락 빨강)이에요. TradingView 위젯은 색을 바꿀 수 없어 항상 미국식이에요.

## 면책

공개 자료를 정리한 정보 제공용 사이트이며 투자 권유가 아니에요. 자료에 오류가 있을 수 있어요.

</div>

<div class="en-only doc-table" markdown="1">

# Data & Methodology

Where the numbers on this site come from and how they are processed. All code is public in the [GitHub repository](https://github.com/ywk-research/ywk-research.github.io).

## Principles

- **Official and licensed sources only.** Open APIs and public files from exchanges, central banks and government agencies. No scraping of portal sites.
- **Previous close only.** Redistributing real-time quotes on a public site requires a paid exchange agreement, so every series collected here is an end-of-day value.
- **Terms checked first.** Commercial data whose free plan allows internal, non-display use only (e.g. Twelve Data Basic) was reviewed and not used.
- **Source and date on every card.** A series whose date is behind the others is marked "lagged".

## Automated collection

GitHub Actions runs the collectors in `tools/` on weekdays at **07:00, 08:30 and 18:30 KST**, commits changed data in `_data/` and rebuilds the site. A failing source does not stop the others, and missing days are filled on the next run. API keys live only in repository secrets and are never printed.

## Sources

| Area | Data | Source |
|---|---|---|
| Korean equities | KOSPI, KOSDAQ and sector indices, VKOSPI, stock prices and turnover, ETFs, spot gold | Korea Exchange (KRX) Open API |
| Rates | US Treasury yield curve, real yields (TIPS) | U.S. Treasury |
| Rates | Korea Treasury yields, policy rate, call, CD and CP rates | Bank of Korea ECOS |
| Rates | Fed funds target upper bound | FRED (Federal Reserve) |
| FX | KRW reference rates vs USD, JPY, CNY, EUR | Bank of Korea ECOS |
| Volatility | VIX | Cboe |
| Commodities | WTI, Brent, natural gas spot | U.S. EIA via FRED |
| Commodities | Monthly commodity prices | World Bank Pink Sheet |
| Indicators | Korean monthly indicators | Bank of Korea ECOS |
| Calendar | US GDP and PCE release dates | U.S. BEA |
| Calendar | Other releases and rate decisions | Compiled from each agency's published schedule |
| Reference widgets | Global indices, sectors, news, economic calendar | TradingView free widgets (delayed, display only) |

KRX data is copyrighted by the Korea Exchange.

## Processing

- **Stock rankings:** KOSPI market cap of KRW 1tn or more, KOSDAQ KRW 300bn or more, excluding preferred shares and SPACs.
- **Sectors:** sector indices only (24 on KOSPI, 22 on KOSDAQ), sorted by daily change.
- **Moving-average crosses:** 20/60-day and 50/200-day golden and death crosses within the last 5 trading days. Closes are stored daily; splits, reverse splits, bonus and rights issues are adjusted by comparing the KRX base price (close − change) with the previous close. Dividends are not adjusted.
- **52-week highs and lows:** counted daily from the same adjusted closes.
- **Backtest:** price returns of fixed-weight portfolios, excluding dividends, costs and taxes. Monthly rebalancing resets weights at the first close of each month; the Sharpe ratio is (annualized return − 91-day CD rate) ÷ annualized volatility.
- **Rate changes** are in bp, **price changes** in % from the previous close.

## Colors

Korean pages use the Korean convention (red up, blue down); English pages use the US convention (green up, red down). TradingView widgets always use the US convention.

## Disclaimer

For information only, compiled from public sources; not investment advice. Errors are possible.

</div>
