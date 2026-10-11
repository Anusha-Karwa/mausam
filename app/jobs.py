"""Scheduled jobs. Function names match the component map:

  Flow 1  jobs.daily_nav()      Vercel Cron 18:00 UTC (~23:30 IST) daily
  Flow 3  jobs.weekly_report()  Vercel Cron 02:00 UTC Saturday (~07:30 IST), or the "Generate now" button

Every job writes a job_runs row (start, end, status, details incl. fallbacks used),
is idempotent (safe to re-run) and degrades gracefully when an outside service fails.
"""
from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import config
from .services import (alerts, amfi, assistant, email, indices, mfapi, news, portfolio, report, returns, snapshots)

MANUAL_COOLDOWN_MIN = 10


def today_local() -> date:
    return datetime.now(ZoneInfo(config.TZ_DISPLAY)).date()


@contextmanager
def job_run(name: str):
    row = db.one("INSERT INTO job_runs (job, status, details) VALUES (%s, 'running', %s) RETURNING id", [name, {}])
    details: dict = {"fallbacks": []}
    started = time.time()
    status = "success"
    try:
        yield details
        status = details.pop("_status", "success")
    except Exception as exc:
        status = "failed"
        details["error"] = f"{type(exc).__name__}: {exc}"[:500]
        details["trace"] = traceback.format_exc()[-1500:]
        try:
            alerts.create(None, "job_failed", "action", f"The {name} job failed: {str(exc)[:160]}", link="/reports")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        details["duration_s"] = round(time.time() - started, 1)
        db.execute("UPDATE job_runs SET finished_at = now(), status = %s, details = %s WHERE id = %s",
                   [status, details, row["id"]])


def recently_ran(job_names: list[str], minutes: int = MANUAL_COOLDOWN_MIN) -> datetime | None:
    """For rate-limiting manual triggers via job_runs."""
    return db.scalar("SELECT MAX(started_at) FROM job_runs WHERE job = ANY(%s) AND started_at > now() - make_interval(mins => %s)",
                     [job_names, minutes])


# ---------- shared steps ----------

def tracked_codes() -> dict[str, list[int]]:
    funds = [r["scheme_code"] for r in db.query("SELECT DISTINCT scheme_code FROM funds WHERE is_active")]
    proxies = [r["proxy_scheme_code"] for r in db.query("SELECT proxy_scheme_code FROM benchmarks WHERE proxy_scheme_code IS NOT NULL")]
    return {"funds": funds, "proxies": [p for p in proxies if p not in funds]}


def apply_amfi_fallback(codes: list[int], mfapi_latest: dict[int, date | None], details: dict) -> dict[int, str]:
    """Fill in from AMFI's official NAVAll.txt where mfapi.in failed or is behind.

    Returns {code: source} where source is 'mfapi', 'amfi' or 'none'. Never overwrites an existing NAV.
    """
    sources = {c: ("mfapi" if mfapi_latest.get(c) else "none") for c in codes}
    try:
        navall = amfi.fetch()
    except Exception as exc:  # noqa: BLE001
        details["amfi"] = {"ok": False, "error": str(exc)[:200]}
        return sources
    used = []
    for c in codes:
        row = navall.get(int(c))
        if not row:
            continue
        stored = db.scalar("SELECT MAX(nav_date) FROM nav_history WHERE scheme_code = %s", [c])
        if (mfapi_latest.get(c) is None or row["nav_date"] > mfapi_latest[c]) and (stored is None or row["nav_date"] > stored):
            mfapi.store_rows(c, [(row["nav_date"], row["nav"])], source="amfi")
            sources[c] = "amfi"
            used.append(c)
    details["amfi"] = {"ok": True, "schemes_in_file": len(navall), "used_for": used,
                       "file_date": max((r["nav_date"] for r in navall.values()), default=None)}
    if used:
        details["fallbacks"].append(f"NAVs: mfapi.in failed or was behind for {len(used)} scheme(s); used AMFI NAVAll.txt")
    return sources


def refresh_navs(details: dict) -> dict:
    """Fetch latest NAVs for active funds and benchmark proxies (filling gaps), plus 5-day index closes.

    mfapi.in first; AMFI NAVAll.txt automatically fills in where mfapi fails or is behind.
    """
    codes = tracked_codes()
    all_codes = codes["funds"] + codes["proxies"]
    results, errors = {}, {}

    def work(code):
        try:
            return code, mfapi.top_up(code), None
        except Exception as exc:  # noqa: BLE001
            return code, None, str(exc)[:160]

    with ThreadPoolExecutor(max_workers=6) as pool:
        for code, res, err in pool.map(work, all_codes):
            if err:
                errors[code] = err
            else:
                results[code] = {"filled": res["filled"], "latest": res["latest"].isoformat() if res["latest"] else None}
    mfapi_latest = {c: (date.fromisoformat(results[c]["latest"]) if c in results and results[c]["latest"] else None)
                    for c in all_codes}
    sources = apply_amfi_fallback(all_codes, mfapi_latest, details)
    details["nav_sources"] = {str(c): s for c, s in sources.items()}
    details["navs"] = {"updated": len(results), "failed": errors,
                       "by_source": {s: sum(1 for v in sources.values() if v == s) for s in ("mfapi", "amfi", "none")}}
    errors = {c: e for c, e in errors.items() if sources.get(c) != "amfi"}
    idx = indices.refresh("5d")
    details["indices"] = idx
    for key, st in idx.items():
        if not st["ok"]:
            details["fallbacks"].append(f"{key}: Yahoo failed, using proxy fund" if st.get("source") == "proxy"
                                        else f"{key}: Yahoo failed, no proxy")
    if errors:
        details["_status"] = "partial"
    return {"navs": results, "nav_errors": errors, "indices": idx}


# ---------- Flow 1: daily NAV job ----------

def daily_nav(trigger: str = "cron") -> dict:
    job = "daily_nav" if trigger == "cron" else "manual_refresh"
    with job_run(job) as details:
        details["trigger"] = trigger
        refresh_navs(details)
        today = today_local()
        users = db.query("SELECT u.id, s.daily_tip_enabled FROM users u LEFT JOIN settings s ON s.user_id = u.id ORDER BY u.id")
        created = 0
        for u in users:
            funds = portfolio.active_funds(u["id"])
            navs = portfolio.latest_navs([f["scheme_code"] for f in funds])
            created += alerts.daily_checks(u["id"], funds, navs, today)
        details["alerts_created"] = created

        if trigger == "cron":
            try:
                got = news.gather(today, daily=True)
                stored = news.store(got["items"], news.week_of(today))
                details["news"] = {"source": got["source"], "stored": len(stored)}
                if got["source"] == "rss":
                    details["fallbacks"].append(f"news: GNews unavailable ({got['error']}), used RSS")
            except Exception as exc:  # noqa: BLE001
                details["news"] = {"error": str(exc)[:200]}

        if users and (users[0]["daily_tip_enabled"] is not False) and (trigger == "cron" or not assistant.latest_tip()):
            tip = assistant.daily_tip(users[0]["id"], today)
            details["tip"] = {"used_fallback": tip["used_fallback"], "llm": tip.get("meta")}
            if tip["used_fallback"]:
                details["fallbacks"].append("daily tip: Gemini unavailable or failed checks, used glossary tip")
        alerts.prune()
        return details


# ---------- Flow 3: Saturday weather report ----------

def report_week(today: date | None = None) -> date:
    """The Friday the report is about: yesterday on Saturday, the last Friday otherwise."""
    return returns.last_friday(today or today_local())


def weekly_report(trigger: str = "cron", user_id: int = 1, week: date | None = None, send_email: bool = True) -> dict:
    job = "weekly_report" if trigger == "cron" else "manual_report"
    with job_run(job) as details:
        details["trigger"] = trigger
        # 1. Friday's NAV may have arrived late: re-fetch.
        refresh_navs(details)
        week = week or report_week()
        details["week_ending"] = week.isoformat()

        # 2. Snapshots for this week (and fill any missing recent weeks so streaks work).
        have = {r["week_ending"] for r in db.query(
            "SELECT DISTINCT week_ending FROM weekly_snapshots WHERE user_id = %s AND week_ending > %s", [user_id, week - timedelta(weeks=5)])}
        weeks = [week - timedelta(weeks=i) for i in range(4) if i == 0 or (week - timedelta(weeks=i)) not in have]
        snaps = snapshots.build_weeks(user_id, weeks)
        details["snapshots"] = len(snaps)

        # 3. Index moves for the market context.
        moves = report.index_moves(week)

        # 4. News: GNews, falling back to RSS.
        try:
            got = news.gather(week)
            news_rows = news.store(got["items"], week)
            details["news"] = {"source": got["source"], "count": len(news_rows)}
            if got["source"] == "rss":
                details["fallbacks"].append(f"news: GNews unavailable ({got['error']}), used RSS")
        except Exception as exc:  # noqa: BLE001
            news_rows = report.news_rows_for(week)
            details["news"] = {"error": str(exc)[:200], "count": len(news_rows)}
            details["fallbacks"].append("news: all sources failed; report says no major news found")

        # 5-6. Attribution + verdict (code), facts JSON, Gemini market summary + report.
        facts, extra = report.build_facts(user_id, week, news_rows, moves)
        market = report.market_summary(week, moves, news_rows)
        report.store_whys(market["news"])
        report.store_market_context(week, moves, market["macro_summary"])
        if market.get("used_fallback"):
            details["fallbacks"].append("market summary: deterministic template")
        result = report.generate(facts, extra["decision"])
        details["report"] = {"verdict": facts["verdict"], "used_fallback": result["used_fallback"],
                             "model": result["model"], "guardrail_errors": result["errors"], "llm": result.get("meta")}
        if result["used_fallback"]:
            details["fallbacks"].append("weekly report: deterministic fallback template")

        # 7. Render + store + alerts.
        from flask import render_template

        outlook = report.outlook(user_id)
        news_view = report.news_for_report(news_rows, market["news"])
        html = render_template("partials/report_body.html", rj=result["report"], facts=facts, news=news_view,
                               outlook=outlook, week=week, moves=moves, macro=market["macro_summary"])
        saved = report.save(user_id, week, facts, result, html, trigger,
                            {"outlook": outlook, "news_why": {str(k): v for k, v in market["news"].items()},
                             "macro_used_fallback": market.get("used_fallback")})
        for w in extra["decision"]["watch"]:
            fund = next((s for s in extra["snapshots"] if report.fund_name(s) == w["fund"]), None)
            alerts.create(user_id, "lagging_streak", "watch",
                          f"{w['fund']} is on the watch list: behind its benchmark for {w['lag_streak_weeks']} week(s) in a row.",
                          fund["fund_id"] if fund else None, f"/reports/{week.isoformat()}", dedupe_days=6)
        if extra["decision"]["verdict"] == "Consider reviewing your allocation" and extra["decision"]["drift_pp"] > 0:
            alerts.create(user_id, "allocation_drift", "action",
                          f"Your mix is {extra['decision']['drift_pp']:.1f} points away from your target in "
                          f"{extra['decision']['drift_class']}. Worth a look, or a chat with an adviser.",
                          None, "/portfolio", dedupe_days=6)

        # 8. Email.
        settings = db.one("SELECT * FROM settings WHERE user_id = %s", [user_id]) or {}
        if send_email and settings.get("report_enabled", True):
            to = settings.get("report_email") or config.REPORT_EMAIL_TO
            try:
                email.send(to, email.subject_for(saved), email.report_email_html(saved))
                db.execute("UPDATE reports SET emailed_at = now() WHERE id = %s", [saved["id"]])
                details["email"] = "sent"
            except Exception as exc:  # noqa: BLE001
                details["email"] = f"not sent: {str(exc)[:200]}"
                details["_status"] = "partial"
        else:
            details["email"] = "disabled"
        return {"report_id": saved["id"], "week": week.isoformat(), **details}


def email_report(user_id: int, week: date) -> dict:
    rep = report.get(user_id, week)
    if not rep:
        raise ValueError("No report for that week")
    settings = db.one("SELECT * FROM settings WHERE user_id = %s", [user_id]) or {}
    to = settings.get("report_email") or config.REPORT_EMAIL_TO
    email.send(to, email.subject_for(rep), email.report_email_html(rep))
    db.execute("UPDATE reports SET emailed_at = now() WHERE id = %s", [rep["id"]])
    return {"sent_to": to}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
