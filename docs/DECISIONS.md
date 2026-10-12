# Decisions log

Each entry: what we decided, why, and where it lives in the code. Dated 11 Oct 2026 unless noted.

1. **Neon instead of Supabase.** The problem statement mentions Supabase; we chose Neon. Both are hosted PostgreSQL. Neon gives serverless Postgres with a native Vercel integration, branching, and auto-suspend on the free plan; the schema (`db/schema.sql`) is plain SQL and portable to Supabase in minutes.

2. **Gemini model: `gemini-3.8-flash`, not `gemini-2.5-flash`.** The prompt's default `gemini-2.5-flash` now returns *404 "no longer available to new users"* for new API keys. `gemini-3.8-flash` is the current Flash model and supports JSON mode with a response schema. It is set by `GEMINI_MODEL`, so it can be changed without code. Gemini 3 models "think" before answering; we ask for `thinkingLevel: low` to keep latency down and fall back automatically if a model rejects that setting (`app/services/gemini.py`).

3. **Small-cap benchmark: Nifty Smallcap 250.** Yahoo's `^CNXSC` (Smallcap 100) returns only the latest day, no history, and no index fund tracks the Smallcap 100. The Smallcap 250 (`NIFTYSMLCAP250.NS`, proxy fund 147623) has full history and is the official benchmark of most small-cap funds. Smallcap 100 is still shown in the market strip and report table (its weekly move fills in as the daily job collects closes).

4. **Midcap ticker:** `NIFTYMIDCAP150.NS` works; `^NIFMDCP150` returns 404.

5. **One source per benchmark series.** An index level (~22,000) and a fund NAV (~158) can't be joined, so `indices.benchmark_series()` picks Yahoo *or* the proxy fund for a whole series, never mixing them. The UI labels proxy data.

6. **AMFI NAVAll.txt as automatic NAV fallback.** The daily job downloads the official file once and uses it for any scheme where mfapi.in failed or is behind. Each `nav_history` row records `source` (mfapi | amfi); `job_runs.details.nav_sources` records the source per scheme; Settings → Behind the scenes shows it. The live file has 8 columns (Plan and Option were added), so the parser maps columns by header name. AMFI's TLS chain ends at *Sectigo Public Server Authentication Root R46*, which only recent CA bundles include, so `certifi>=2025.1.31` is pinned.

7. **News: Moneycontrol RSS dropped.** Its feeds stopped updating in April 2024. RSS fallback uses Economic Times Markets, Business Standard Markets and ET Mutual Funds. NewsAPI.org is not used (free plan only works on localhost).

8. **Debt funds compare with the category average** (`CATEGORY_ONLY`), because CRISIL debt indices aren't free.

9. **Category averages are computed by us** from up to 12 named peers (direct, growth, same category, still publishing). Peer NAV histories are stored, so past weeks' category returns can be rebuilt for snapshots.

10. **Attribution bands per asset class**: equity 0.75, debt 0.1, international 1.0 points (`market_wide_band_pp`, `debt_market_wide_band_pp`, `intl_market_wide_band_pp` in settings). One equity-sized band would label every debt week "market-wide".

11. **Setup without local Python.** `POST /api/admin/migrate` and `POST /api/admin/seed/<step>` (Bearer `CRON_SECRET`) run the migration and seed steps on Vercel; the "Set up database" GitHub workflow does the same in Actions.

12. **Database connection.** One reused psycopg connection per warm function, `prepare_threshold=None` (Neon's pooled endpoint runs PgBouncer in transaction mode, which breaks prepared statements), reconnect-once on a dropped connection. NUMERIC is loaded as float.

13. **ML runs only in GitHub Actions.** pandas/statsmodels/scikit-learn are far too big for a Vercel function. The app only reads the `forecasts` and `forecast_metrics` tables.

14. **Vercel `maxDuration` 300 s.** The weekly job makes two Gemini calls plus data fetches; Fluid compute allows up to 300 s on Hobby. Hobby crons run once a day and may fire anywhere within the scheduled hour; that is acceptable and logged in `job_runs`.

15. **CSRF:** double-submit cookie (`mausam_csrf` cookie + `X-CSRF-Token` header) on every write API. Cron and admin routes use the Bearer secret instead.

16. **No login.** Single-user demo for examiners. Risk recorded in `RISK_REGISTER.md`; every table already has `user_id` for multi-user later.

18. **Pages simplified (12 Oct 2026).** Learn, System status and About were removed to keep the menu small; page names were rewritten in plain words and grouped into three sidebar sections. The job log moved into Settings → Behind the scenes, and Ask Mausam became a floating chat bubble on every page.

19. **Today vs My portfolio.** Today shows only this week's numbers; all-time totals live only on My portfolio, so no number appears twice.

20. **Automatic night mode.** Dark from 6 pm to 6 am by the viewer's clock. The top-bar toggle overrides it for the rest of the day; Settings can fix Light or Dark permanently.

21. **App region moved to Singapore (sin1).** The function ran in Washington while Neon is in Singapore; every query crossed the Pacific. Moving it, merging the top-bar queries and caching index cards for 10 minutes cut page times from 1.3 to 10 s down to about 0.2 to 0.5 s.

22. **Nifty Smallcap 100 via an ETF stand-in.** Yahoo returns no history for `^CNXSC` and NSE's site blocks automated requests, so the Zerodha Nifty Smallcap 100 ETF (scheme 153837, about a year of daily NAVs) is its proxy, labelled in the UI. Small-cap funds are still compared with the Smallcap 250, their official benchmark.

17. **SIP calculator maths** uses monthly rate `(1+r)^(1/12) − 1` with SIPs at the start of each month, so ₹5,000/month for 10 years at 12% ≈ ₹11.2 lakh (the "₹11.6 lakh" example in the brief assumes a flat 1% a month).
