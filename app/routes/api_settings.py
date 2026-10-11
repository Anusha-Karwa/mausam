"""Settings, data export, test email and the rate-limited manual NAV refresh."""
from __future__ import annotations

import re

from flask import Blueprint, Response

from .. import db, jobs
from ..config import config
from ..services import email, snapshots
from ._util import body, fail, ok, user_id

bp = Blueprint("api_settings", __name__, url_prefix="/api")

RISK = ("conservative", "moderate", "aggressive")


def get_settings(uid: int) -> dict:
    row = db.one("SELECT * FROM settings WHERE user_id = %s", [uid]) or {}
    row["thresholds"] = snapshots.thresholds(uid)
    row["thresholds_default"] = snapshots.DEFAULT_THRESHOLDS
    row["effective_report_email"] = row.get("report_email") or config.REPORT_EMAIL_TO
    return row


@bp.get("/settings")
def get():
    return ok(get_settings(user_id()))


@bp.put("/settings")
def put():
    b = body()
    sets, params = [], []
    if "report_email" in b:
        e = (b["report_email"] or "").strip()
        if e and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
            return fail("That email address doesn't look right.")
        sets.append("report_email = %s"); params.append(e or None)
    for flag in ("report_enabled", "daily_tip_enabled"):
        if flag in b:
            sets.append(f"{flag} = %s"); params.append(bool(b[flag]))
    if "risk_profile" in b:
        if b["risk_profile"] not in RISK:
            return fail("Risk profile must be conservative, moderate or aggressive.")
        sets.append("risk_profile = %s"); params.append(b["risk_profile"])
    if "target_allocation" in b:
        t = b["target_allocation"] or {}
        try:
            t = {k: round(float(t.get(k, 0)), 1) for k in ("equity", "debt", "international")}
        except (TypeError, ValueError):
            return fail("Target allocation must be numbers.")
        if any(v < 0 or v > 100 for v in t.values()) or abs(sum(t.values()) - 100) > 0.01:
            return fail(f"Target allocation must add up to 100% (it adds up to {sum(t.values()):g}%).")
        sets.append("target_allocation = %s"); params.append(t)
    if "thresholds" in b:
        th = b["thresholds"]
        if th == "reset":
            th = dict(snapshots.DEFAULT_THRESHOLDS)
        clean = {}
        for k, default in snapshots.DEFAULT_THRESHOLDS.items():
            v = th.get(k, default) if isinstance(th, dict) else default
            try:
                v = float(v)
            except (TypeError, ValueError):
                return fail(f"{k} must be a number.")
            if v < 0 or v > 100:
                return fail(f"{k} is out of range.")
            clean[k] = int(v) if k == "consecutive_weeks_to_watch" else v
        if clean["consecutive_weeks_to_watch"] < 1:
            return fail("Weeks to watch must be at least 1.")
        sets.append("thresholds = %s"); params.append(clean)
    if not sets:
        return fail("Nothing to update.")
    db.execute(f"UPDATE settings SET {', '.join(sets)}, updated_at = now() WHERE user_id = %s", params + [user_id()])
    return ok(get_settings(user_id()))


@bp.post("/settings/test-email")
def test_email():
    s = get_settings(user_id())
    to = s.get("effective_report_email")
    html = ("<div style='font-family:Arial,sans-serif;padding:20px'><h2>☀️ Mausam test email</h2>"
            "<p>If you can read this, your Saturday weather report will arrive here.</p></div>")
    try:
        email.send(to, "Mausam test email", html)
    except Exception as exc:  # noqa: BLE001
        return fail(f"Couldn't send: {str(exc)[:200]}")
    return ok({"sent_to": to})


@bp.get("/export")
def export():
    """Everything Mausam stores for this user, as JSON."""
    uid = user_id()
    data = {
        "settings": get_settings(uid),
        "funds": db.query("SELECT * FROM funds WHERE user_id = %s ORDER BY id", [uid]),
        "transactions": db.query("SELECT * FROM transactions WHERE user_id = %s ORDER BY txn_date, id", [uid]),
        "weekly_snapshots": db.query("SELECT * FROM weekly_snapshots WHERE user_id = %s ORDER BY week_ending, fund_id", [uid]),
        "reports": db.query("SELECT week_ending, verdict, verdict_reason, facts_json, report_json, model, used_fallback, generated_at "
                            "FROM reports WHERE user_id = %s ORDER BY week_ending", [uid]),
        "alerts": db.query("SELECT * FROM alerts WHERE user_id = %s ORDER BY created_at", [uid]),
        "chat_messages": db.query("SELECT role, content, created_at FROM chat_messages WHERE user_id = %s ORDER BY id", [uid]),
    }
    from flask import current_app

    return Response(current_app.json.dumps({"ok": True, "data": data, "error": None}), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=mausam-export.json"})


@bp.post("/refresh")
def refresh():
    last = jobs.recently_ran(["manual_refresh"])
    if last:
        return fail("NAVs were refreshed in the last 10 minutes. mfapi.in only updates once a day, so there's nothing new yet.", 429)
    try:
        details = jobs.daily_nav(trigger="manual")
    except Exception as exc:  # noqa: BLE001
        return fail(f"Refresh failed: {str(exc)[:200]}", 500)
    return ok({"navs": details.get("navs"), "fallbacks": details.get("fallbacks")})
