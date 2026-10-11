"""Vercel Cron entry points. Vercel sends 'Authorization: Bearer <CRON_SECRET>'."""
from __future__ import annotations

import secrets

from flask import Blueprint, request

from .. import jobs
from ..config import config
from ._util import fail, ok

bp = Blueprint("cron", __name__, url_prefix="/api/cron")


def authorised() -> bool:
    header = request.headers.get("Authorization", "")
    expected = f"Bearer {config.CRON_SECRET}"
    return bool(config.CRON_SECRET) and secrets.compare_digest(header, expected)


@bp.get("/daily")
def daily():
    if not authorised():
        return fail("Unauthorised", 401)
    try:
        return ok(jobs.daily_nav(trigger="cron"))
    except Exception as exc:  # noqa: BLE001 - already logged in job_runs
        return fail(f"daily_nav failed: {exc}", 500)


@bp.get("/weekly")
def weekly():
    if not authorised():
        return fail("Unauthorised", 401)
    try:
        result = jobs.weekly_report(trigger="cron")
        return ok({k: v for k, v in result.items() if k != "trace"})
    except Exception as exc:  # noqa: BLE001
        return fail(f"weekly_report failed: {exc}", 500)
