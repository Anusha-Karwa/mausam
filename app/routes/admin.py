"""Setup endpoints, protected by CRON_SECRET. They let the database be migrated and seeded
from anywhere (curl) without a local Python install. Each seed step is idempotent."""
from __future__ import annotations

from flask import Blueprint, request

from .. import jobs
from ..services import seed
from ._util import fail, ok
from .cron import authorised

bp = Blueprint("admin", __name__, url_prefix="/api/admin")

STEPS = {
    "benchmarks": seed.step_benchmarks,
    "funds": seed.step_funds,
    "transactions": seed.step_transactions,
    "peers": lambda: seed.step_peers(request.args.get("group")),
    "snapshots": lambda: seed.step_snapshots(int(request.args.get("weeks", 26))),
    "report": lambda: {k: v for k, v in jobs.weekly_report(trigger="manual", send_email=request.args.get("email") == "1").items()
                       if k != "trace"},
    "tip": lambda: jobs.daily_nav(trigger="manual"),
}


@bp.post("/migrate")
def migrate():
    if not authorised():
        return fail("Unauthorised", 401)
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from db.migrate import migrate as run

    return ok(run())


@bp.post("/seed/<step>")
def seed_step(step: str):
    if not authorised():
        return fail("Unauthorised", 401)
    if step not in STEPS:
        return fail(f"Unknown step. Use one of: {', '.join(STEPS)}")
    with jobs.job_run(f"seed_{step}") as details:
        result = STEPS[step]()
        details["result_summary"] = str(result)[:1500]
    return ok(result)
