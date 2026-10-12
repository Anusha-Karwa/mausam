# Risk register

| Risk | Impact | Mitigation | Where in code |
|---|---|---|---|
| Gemini invents numbers or news | Wrong picture of her money | Numbers computed in code; number-check guardrail; news cited by ID only; fallback template | `services/report.py`, `services/guardrails.py` |
| AI gives buy/sell advice | Bad financial decision | System rules + banned-phrase filter + fixed 3-verdict set validated by code + disclaimer | `guardrails.REPORT_BANNED`, `CHAT_INSTRUCTION_PATTERNS`, `services/verdict.py` |
| User treats forecast as a promise | Acting on a guess | Ranges only, accuracy vs naive shown, "estimate, not a promise" labels, forecast excluded from verdict | `ml/forecast.py`, `templates/forecasts.html` |
| mfapi.in down or late | Stale numbers | Retries; **AMFI NAVAll.txt fallback**; stale badges; report flags stale funds; Saturday job re-fetches | `services/mfapi.py`, `services/amfi.py`, `jobs.refresh_navs` |
| Yahoo endpoint blocked/changed | No benchmark data | Index-fund NAV proxy fallback, labelled in UI | `services/indices.benchmark_series` |
| News API quota/failure | No market context | RSS fallback; report says "no major news found" rather than guessing | `services/news.gather` |
| Wrong benchmark for a fund | Misleading comparison | Category mapping + visible "how we chose this" + manual override | `services/benchmarks.py`, Manage funds |
| Category average not official | Imprecise comparison | Computed from N named peers, disclosed with peer count | `services/category.py` |
| Price index vs NAV (dividends) | Small systematic gap | Disclosed in tooltip and README | Fund detail, README |
| Cron timing drift / missed run | Late report | Job logging, Settings → Behind the scenes, manual "Generate now" and "Refresh NAVs now" buttons, idempotent jobs | `jobs.job_run`, `/settings` |
| Gemini free-tier rate limits | Report fails | Single batched call, retry with backoff, deterministic fallback | `services/gemini.py`, `report.generate` |
| Free-tier data use by Google | Privacy | No names/emails sent; disclosed in README | `assistant.chat_context` |
| Neon cold start | Slow first load | Pooled connection, short queries, skeleton loaders | `app/db.py` |
| Secrets leak | Account abuse | Env vars only, `.env` in `.gitignore`, CRON_SECRET on job and admin routes | `config.py`, `routes/cron.py`, `routes/admin.py` |
| Over-trading from weekly checks | Emotional decisions | Calm copy, long-term charts beside weekly numbers, "No action needed" is the default | UI copy, `verdict.decide` |
| **Public demo has no login** | Anyone with the URL can edit the demo data | Single-user demo by design; CSRF on writes; rate limits on Gemini and refresh; data is fictional demo data | `app/__init__.py` |
| **Smallcap 100 has no free index history** | Weekly move missing | Zerodha Nifty Smallcap 100 ETF as stand-in; small-cap funds still compared with the Smallcap 250 (their official benchmark) | `db/seed.sql`, `benchmarks.classify` |
| **AMFI certificate chain** (Sectigo R46) | Fallback download fails on old CA bundles | `certifi>=2025.1.31` pinned | `requirements.txt` |
