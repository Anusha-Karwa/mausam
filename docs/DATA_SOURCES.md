# Data sources

All free, all live. Verified on 11 Oct 2026.

## NAVs

**mfapi.in** (primary, no key) — `app/services/mfapi.py`
- Search: `GET https://api.mfapi.in/mf/search?q=...`
- Full history + meta: `GET https://api.mfapi.in/mf/{code}` (also `?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD`)
- Latest: `GET https://api.mfapi.in/mf/{code}/latest`
- All schemes (used to discover category peers): `GET https://api.mfapi.in/mf`
- Dates are `dd-mm-yyyy`, NAVs are strings. An unknown code returns empty `meta`/`data`.
- 10 s timeout, 2 retries with backoff, `User-Agent: Mausam/1.0 (student project)`.

**AMFI NAVAll.txt** (official fallback, no key) — `app/services/amfi.py`
- `GET https://www.amfiindia.com/spages/NAVAll.txt` (~1.5 MB, ~14,000 schemes, latest NAV only).
- Header today: `Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date` (older files had 6 columns; the parser maps by header name). Section lines carry the category (`Open Ended Schemes(Debt Scheme - Short Duration Fund)`) and fund house. Dates look like `09-Oct-2026`; "N.A." NAVs are skipped.
- Used automatically in the daily job when mfapi fails or is behind; the source is recorded per NAV row and per job run.

NAVs are published once per business day after market close. **They are not real-time.** A fund is *stale* if its latest NAV is more than 4 calendar days old on a weekday.

## Index levels

**Yahoo Finance chart endpoint** (unofficial, no key) — `app/services/indices.py`
`GET https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1d` with a browser User-Agent.

| Key | Index | Ticker | Status (11 Oct 2026) | Proxy fund (mfapi) |
|---|---|---|---|---|
| NIFTY50 | Nifty 50 | `^NSEI` | works | 119063 HDFC Nifty 50 Index Fund |
| MIDCAP150 | Nifty Midcap 150 | `NIFTYMIDCAP150.NS` (`^NIFMDCP150` is 404) | works | 147622 Motilal Oswal Nifty Midcap 150 Index |
| SMALLCAP100 | Nifty Smallcap 100 | `^CNXSC` | latest day only, no history | none exists |
| SMALLCAP250 | Nifty Smallcap 250 | `NIFTYSMLCAP250.NS` | works | 147623 Motilal Oswal Nifty Smallcap 250 Index |
| SP500 | S&P 500 (USD) | `^GSPC` | works | 148381 Motilal Oswal S&P 500 Index |
| NASDAQ100 | Nasdaq 100 (USD) | `^NDX` | works | 149219 ICICI Prudential NASDAQ 100 Index |
| USDINR | US dollar in rupees | `USDINR=X` | works | – |

US indices are converted to rupees on each date: INR return = (1 + r_index) × (1 + r_usdinr) − 1. Proxy funds are rupee funds already. Index returns are *price* returns; fund NAVs include reinvested dividends (a small known gap; total-return indices aren't free). Re-check with `python scripts/verify_tickers.py`.

## News

- **GNews** (primary, `GNEWS_API_KEY`): `/api/v4/search` with the queries "Nifty OR Sensex", "RBI OR repo rate OR inflation", "FII OR rupee OR crude", "US Fed OR Nasdaq" (weekly: 4 calls), `/api/v4/top-headlines?category=business&country=in` (daily: 1 call). Free tier ≈100 requests/day.
- **RSS fallback** via feedparser: Economic Times Markets `https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms`, Business Standard Markets `https://www.business-standard.com/rss/markets-106.rss`, ET Mutual Funds `https://economictimes.indiatimes.com/mf/rssfeeds/359241701.cms`. Moneycontrol RSS is stale since April 2024, so not used. NewsAPI.org is not used.
- Deduplicated by normalised title, 8–12 most relevant kept. Only headline, source, date, link and our own one-line "why it matters" are shown.

## Meera's five funds (Direct Plan – Growth)

| Role | Code | Scheme | mfapi category | History from |
|---|---|---|---|---|
| Large-cap index | 120716 | UTI Nifty 50 Index Fund | Other Scheme - Index Funds | 2013 |
| Mid-cap | 118989 | HDFC Mid Cap Fund | Equity Scheme - Mid Cap Fund | 2013 |
| Small-cap | 118778 | Nippon India Small Cap Fund | Equity Scheme - Small Cap Fund | 2013 |
| Short-duration debt | 120754 | ICICI Prudential Short Term Fund | Debt Scheme - Short Duration Fund | 2013 |
| International FoF | 149961 | Invesco India – Invesco EQQQ Nasdaq-100 ETF FoF | Overseas Fund of Funds | Apr 2022 |

## AI and email

- **Gemini Flash** (`generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent`, header `x-goog-api-key`), JSON mode with a response schema. Only fund nicknames and numbers are sent — no names or emails.
- **Resend** (`POST https://api.resend.com/emails`). Without a verified domain, Resend only delivers to the account owner's address.
