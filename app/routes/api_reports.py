"""Weekly reports API, including the rate-limited "Generate this week's report now" button."""
from __future__ import annotations

from flask import Blueprint

from .. import jobs
from ..services import report
from ._util import fail, ok, parse_date, user_id

bp = Blueprint("api_reports", __name__, url_prefix="/api")


@bp.get("/reports")
def list_reports():
    return ok(report.all_reports(user_id()))


@bp.get("/reports/<week>")
def get_report(week: str):
    try:
        d = parse_date(week, "week")
    except ValueError as exc:
        return fail(str(exc))
    row = report.get(user_id(), d)
    if not row:
        return fail("No report for that week.", 404)
    return ok(row)


@bp.post("/reports/generate")
def generate():
    last = jobs.recently_ran(["manual_report", "weekly_report"])
    if last:
        return fail("A report was generated in the last 10 minutes. Please wait a little before generating another.", 429)
    try:
        result = jobs.weekly_report(trigger="manual", user_id=user_id())
    except Exception as exc:  # noqa: BLE001
        return fail(f"Report generation failed: {str(exc)[:200]}. Details are on the System status page.", 500)
    return ok({"week": result["week"], "verdict": result["report"]["verdict"],
               "used_fallback": result["report"]["used_fallback"], "email": result.get("email"),
               "fallbacks": result.get("fallbacks")})


@bp.post("/reports/<week>/email")
def email(week: str):
    try:
        d = parse_date(week, "week")
        return ok(jobs.email_report(user_id(), d))
    except Exception as exc:  # noqa: BLE001
        return fail(f"Couldn't send the email: {str(exc)[:200]}")
