# Mausam — your money weather

**Mausam** ("weather" in Hindi) is a personal mutual fund tracker that gives a non-expert investor a plain-language **weather report** on her money every Saturday.

Meera is 28 and has five monthly SIPs: a large-cap index fund, a mid-cap fund, a small-cap fund, a short-duration debt fund and an international fund of funds. Her fund app shows only NAVs and absolute returns, so once a month she screenshots them to a CA friend asking "is this okay?". Mausam answers that question for her, every week, without jargon.

- **Live app:** https://mausam-alpha-azure.vercel.app
- **Component map:** [docs/Mausam_Components_Map.png](docs/Mausam_Components_Map.png)

> Mausam is an educational tool, not investment advice. Mutual fund investments are subject to market risks.

## What it does

- Fetches every fund's latest NAV **daily** (mfapi.in, with AMFI's official NAVAll.txt as automatic fallback).
- Computes week-on-week, month-on-month, year-to-date, 1-year and 3-year returns, and compares each fund with its **benchmark index** and its **category average** (computed from up to 12 named similar funds).
- Labels every weekly move **"moved with the market"** or **"fund-specific"**.
- Every **Saturday morning**, fetches the week's market moves (Nifty 50, Midcap 150, Smallcap 100/250, S&P 500 and Nasdaq 100 in rupees) and news, and has **Gemini Flash** write a report: three sentences per fund plus exactly one verdict — **No action needed** (clear skies), **One fund worth watching** (passing clouds) or **Consider reviewing your allocation** (storm watch). The verdict is decided by rules in code; Gemini only explains it, and code checks every number it writes.
- Stores **weekly snapshots** forever for multi-week trends.
- Add or remove funds by **scheme code** from a web form — no code changes.
- **Hybrid ARIMA + Ridge Regression** forecasts give a likely NAV *range* for 7 and 30 days, with honest accuracy against a "stays the same" guess.
- Pages in plain words, grouped in the sidebar: **This week** (Today, Markets and news; the Saturday report opens from Today), **My money** (My portfolio, Manage funds, SIPs and payments), **Look ahead** (Past weeks, Next month, Compare funds, Calculators). Alerts and Settings sit in the top bar; every fund has its own detail page.
- **Weather UI:** the whole screen shows this week's verdict as a live sky: sun for Clear skies, drifting clouds for Passing clouds, dark clouds with full-screen rain and lightning for Storm watch. Moon and stars at night; **night mode turns on automatically from 6 pm to 6 am**.
- **Today** shows only this week (₹ change, funds that kept up with their market, why they moved, next SIP); **My portfolio** shows all-time totals.
- **Ask Mausam** is a floating chat bubble on every page: a sun by day, a moon by night.

## Architecture

| Part | Choice |
|---|---|
| Web | Flask + Jinja2 templates, hand-written CSS, vanilla JS, Chart.js, Lucide |
| Hosting | Vercel (one Python function, `index.py`, Singapore region next to the database) + Vercel Cron |
| Database | Neon serverless PostgreSQL (pooled connection) |
| AI | Gemini Flash only (`GEMINI_MODEL`, default `gemini-3.8-flash`) |
| ML | Python in GitHub Actions (pandas, statsmodels, scikit-learn) |
| Email | Resend |
| News | GNews, RSS fallback |

**The seven flows** (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)):

| Flow | Trigger | Code |
|---|---|---|
| 1 Daily NAV job | Vercel Cron 18:00 UTC | `app/jobs.py: daily_nav()` |
| 2 Forecasts + category averages | GitHub Actions Fri 19:30 UTC | `ml/backfill.py`, `ml/peers.py`, `ml/forecast.py` |
| 3 Saturday weather report | Vercel Cron Sat 02:00 UTC or "Generate now" | `app/jobs.py: weekly_report()` |
| 4 Add/remove a fund | Manage funds page | `app/routes/api_funds.py`, `app/services/funds.py` |
| 5 Daily visit | Any page | `app/routes/pages.py`, `app/services/portfolio.py` |
| 6 Log a buy/sell/SIP | Transactions page | `app/routes/api_transactions.py` |
| 7 Ask Mausam chat | Chat page | `app/routes/api_chat.py`, `app/services/assistant.py` |

### Why Neon, not Supabase

The problem statement mentions Supabase; we deliberately chose Neon. Both are hosted PostgreSQL. Neon gives serverless Postgres with a native Vercel integration, branching, and auto-suspend on the free plan; the schema is plain SQL and portable to Supabase in minutes.

### Gemini model

`gemini-2.5-flash` (the brief's default) now returns *"no longer available to new users"* for new API keys, so Mausam defaults to `gemini-3.8-flash`. Change it any time with `GEMINI_MODEL`. More decisions: [docs/DECISIONS.md](docs/DECISIONS.md).

## Setup

### 1. Accounts and environment variables

| Variable | Required | What |
|---|---|---|
| `DATABASE_URL` | yes | Neon **pooled** connection string (`...-pooler...?sslmode=require`) |
| `CRON_SECRET` | yes | Long random string; Vercel Cron sends it as `Authorization: Bearer ...` |
| `GEMINI_API_KEY` | – | Google AI Studio key (without it, reports use the plain template) |
| `GEMINI_MODEL` | – | default `gemini-3.8-flash` |
| `GNEWS_API_KEY` | – | gnews.io key (without it, news comes from RSS) |
| `RESEND_API_KEY` | – | Resend key (without it, reports aren't emailed) |
| `REPORT_EMAIL_TO` / `REPORT_EMAIL_FROM` | – | Report recipient / sender (`Mausam <onboarding@resend.dev>`) |
| `APP_BASE_URL` | – | Public URL, used in email links |
| `INVESTING_PLATFORM_URL` | – | default `https://www.mfcentral.com` |
| `TZ_DISPLAY` | – | default `Asia/Kolkata` |

Copy `.env.example` to `.env` for local runs. Never commit `.env`. If a required variable is missing, the app shows a setup page instead of crashing.

For the ML workflow, add `DATABASE_URL` and `GEMINI_API_KEY` in GitHub → Settings → Secrets and variables → Actions.

### 2. Deploy

1. Push to GitHub, import the repo in Vercel (framework: Other), add the env vars (Production + Preview), deploy.
2. Check **Vercel → Settings → Cron Jobs** shows `/api/cron/daily` and `/api/cron/weekly`.

### 3. Create tables and demo data — no local Python needed

```bash
curl -X POST -H "Authorization: Bearer $CRON_SECRET" https://<app>/api/admin/migrate
for s in benchmarks funds transactions peers snapshots report tip; do
  curl -X POST -H "Authorization: Bearer $CRON_SECRET" https://<app>/api/admin/seed/$s; done
```

Or run the **Set up database** workflow in GitHub Actions. With local Python: `python db/migrate.py` then `python scripts/seed_meera.py`.

### 4. Run the ML job once

GitHub → Actions → **ML forecasts (Friday night)** → Run workflow.

### Run locally

```bash
pip install -r requirements.txt
flask --app index run
```

## How the jobs work

- **Daily (18:00 UTC ≈ 23:30 IST):** NAVs (mfapi → AMFI fallback), 5-day index closes, stale/big-move alerts, daily tip, a news refresh.
- **Saturday (02:00 UTC ≈ 07:30 IST):** re-fetch NAVs, snapshots, verdict, Gemini report with guardrails, email.
- **Friday night (GitHub Actions):** NAV/index backfill, category averages, forecasts and backtests.
- Hobby-plan crons run once a day and may fire anywhere within the scheduled hour. Every run is logged in `job_runs` and shown under **Settings → Behind the scenes**, including which backup source was used. "Refresh NAVs now" and "Generate this week's report now" buttons run the same code on demand (rate-limited to once per 10 minutes).

## Data sources and limits

See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md). In short: NAVs are daily, not real-time; benchmarks are mapped from the fund's category (with manual override); category averages are computed from named peers; index returns are price returns while NAVs include dividends; Yahoo data falls back to index-fund proxies; Nifty Smallcap 100 has no free history, so small-cap funds are compared with the Smallcap 250. Resend only delivers to the account owner's address until a domain is verified.

## Tests

```bash
pip install -r requirements.txt -r ml/requirements.txt pytest
pytest -q
python scripts/smoke_test.py https://<app>
```

CI runs the tests on every push. Covered: returns, YTD dates, attribution, verdict rules, FIFO, XIRR, guardrails (numbers, banned phrases, sentence count, verdict mismatch), report retry/fallback with Gemini mocked, benchmark mapping, news dedupe, AMFI parser, ML features (no look-ahead), backtest windows and calibration.

## Docs

[Architecture](docs/ARCHITECTURE.md) · [Data sources](docs/DATA_SOURCES.md) · [Decisions](docs/DECISIONS.md) · [Risk register](docs/RISK_REGISTER.md) · [Nine checks](docs/NINE_CHECKS.md)
