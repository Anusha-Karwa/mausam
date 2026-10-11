"""Alerts: stale data, big daily moves, lagging streaks, allocation drift, failed jobs."""
from __future__ import annotations

from datetime import date

from .. import db

BIG_MOVE = {"equity": 3.0, "international": 3.0, "debt": 0.5}


def create(user_id: int | None, kind: str, severity: str, message: str, fund_id: int | None = None,
           link: str | None = None, dedupe_days: int = 1) -> bool:
    """Insert an alert unless the same one was raised recently."""
    exists = db.scalar(
        """SELECT 1 FROM alerts WHERE COALESCE(user_id, 0) = COALESCE(%s, 0) AND kind = %s
           AND COALESCE(fund_id, 0) = COALESCE(%s, 0) AND message = %s AND created_at > now() - make_interval(days => %s)""",
        [user_id, kind, fund_id, message, dedupe_days])
    if exists:
        return False
    db.execute("INSERT INTO alerts (user_id, fund_id, kind, severity, message, link) VALUES (%s, %s, %s, %s, %s, %s)",
               [user_id, fund_id, kind, severity, message, link])
    return True


def daily_checks(user_id: int, funds: list[dict], navs: dict, today: date) -> int:
    from .returns import is_stale

    created = 0
    for f in funds:
        n = navs.get(f["scheme_code"]) or {}
        name = f["short_name"] or f["scheme_name"]
        if is_stale(n.get("nav_date"), today):
            when = n["nav_date"].strftime("%d %b") if n.get("nav_date") else "never"
            created += create(user_id, "stale_data", "watch",
                              f"{name}: latest NAV is from {when}. mfapi.in may be late; numbers may be out of date.",
                              f["id"], f"/funds/{f['scheme_code']}")
        if n.get("nav") and n.get("prev_nav"):
            move = (n["nav"] / n["prev_nav"] - 1) * 100
            if abs(move) >= BIG_MOVE.get(f["asset_class"], 3.0):
                word = "rose" if move > 0 else "fell"
                created += create(user_id, "big_move", "info",
                                  f"{name} {word} {abs(move):.2f}% on {n['nav_date'].strftime('%d %b')}. Big daily moves are normal now and then.",
                                  f["id"], f"/funds/{f['scheme_code']}")
    return created


def unread_count(user_id: int) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM alerts WHERE (user_id = %s OR user_id IS NULL) AND read_at IS NULL", [user_id]) or 0)


def recent(user_id: int, limit: int = 50, unread_only: bool = False) -> list[dict]:
    sql = """SELECT a.*, f.short_name, f.scheme_code FROM alerts a LEFT JOIN funds f ON f.id = a.fund_id
             WHERE (a.user_id = %s OR a.user_id IS NULL)"""
    if unread_only:
        sql += " AND a.read_at IS NULL"
    return db.query(sql + " ORDER BY a.created_at DESC LIMIT %s", [user_id, limit])


def mark_read(user_id: int, alert_id: int | None = None) -> int:
    if alert_id:
        return db.execute("UPDATE alerts SET read_at = now() WHERE id = %s AND (user_id = %s OR user_id IS NULL) AND read_at IS NULL",
                          [alert_id, user_id])
    return db.execute("UPDATE alerts SET read_at = now() WHERE (user_id = %s OR user_id IS NULL) AND read_at IS NULL", [user_id])


def prune(days: int = 120) -> None:
    db.execute("DELETE FROM alerts WHERE created_at < now() - make_interval(days => %s)", [days])


