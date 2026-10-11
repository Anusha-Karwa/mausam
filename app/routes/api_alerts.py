from __future__ import annotations

from flask import Blueprint, request

from ..services import alerts
from ._util import ok, user_id

bp = Blueprint("api_alerts", __name__, url_prefix="/api/alerts")


@bp.get("")
def list_alerts():
    return ok(alerts.recent(user_id(), unread_only=request.args.get("unread") == "1"))


@bp.post("/<int:alert_id>/read")
def read(alert_id: int):
    return ok({"updated": alerts.mark_read(user_id(), alert_id)})


@bp.post("/read-all")
def read_all():
    return ok({"updated": alerts.mark_read(user_id())})
