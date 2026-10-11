"""Forecast ranges written by the ML job (GitHub Actions). Read-only here."""
from __future__ import annotations

from flask import Blueprint

from .. import db
from ._util import fail, ok

bp = Blueprint("api_forecasts", __name__, url_prefix="/api/forecasts")


def forecast_bundle(code: int) -> dict | None:
    run = db.scalar("SELECT MAX(run_date) FROM forecasts WHERE scheme_code = %s", [code])
    if not run:
        return None
    fc = db.query("SELECT * FROM forecasts WHERE scheme_code = %s AND run_date = %s ORDER BY horizon_days", [code, run])
    metrics = db.query("SELECT * FROM forecast_metrics WHERE scheme_code = %s AND run_date = %s ORDER BY horizon_days, model",
                       [code, run])
    return {"scheme_code": code, "run_date": run, "forecasts": fc, "metrics": metrics}


@bp.get("/<int:code>")
def get(code: int):
    data = forecast_bundle(code)
    if not data:
        return fail("No forecast yet for this fund. The ML job runs every Friday night (or manually from GitHub Actions).", 404)
    return ok(data)
