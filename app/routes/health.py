"""Health and data freshness. /api/health is cheap (DB only); ?deep=1 also pings every outside service."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from flask import Blueprint, request

from .. import db
from ..config import config
from ..services import amfi, gemini, returns
from ..services.http import BROWSER_UA, get
from ._util import ok

bp = Blueprint("health", __name__, url_prefix="/api")


def _timed(name, fn):
    start = time.time()
    try:
        detail = fn()
        status = "green"
    except Exception as exc:  # noqa: BLE001
        detail, status = str(exc)[:160], "red"
    if isinstance(detail, tuple):
        status, detail = detail
    return {"name": name, "status": status, "detail": detail, "ms": int((time.time() - start) * 1000)}


def _http_check(url, ua=None, expect=200, headers=None):
    resp = get(url, ua=ua or BROWSER_UA, timeout=8, retries=0, headers=headers)
    if resp.status_code != expect:
        return "amber", f"HTTP {resp.status_code}"
    return "green", f"HTTP {resp.status_code}"


def service_checks() -> list[dict]:
    checks = {
        "Neon (database)": lambda: ("green", f"{db.scalar('SELECT COUNT(*) FROM nav_history')} NAV rows"),
        "mfapi.in (NAVs)": lambda: _http_check("https://api.mfapi.in/mf/120716/latest"),
        "AMFI NAVAll.txt (backup NAVs)": lambda: ("green", f"{len(amfi.fetch())} schemes in today's file"),
        "Yahoo Finance (indices)": lambda: _http_check("https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?range=1d&interval=1d"),
        "GNews (news)": lambda: (("amber", "No key set: using RSS feeds") if not config.GNEWS_API_KEY else
                                 _http_check(f"https://gnews.io/api/v4/top-headlines?category=business&lang=en&country=in&max=1&apikey={config.GNEWS_API_KEY}")),
        "RSS feeds (news backup)": lambda: _http_check("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
        "Gemini Flash (AI)": lambda: (("amber", "No key set: fallback templates in use") if not gemini.available() else
                                      ("green", f"{config.GEMINI_MODEL} answered in {gemini.ping()['latency_ms']} ms")),
        "Resend (email)": lambda: (("amber", "No key set: emails off") if not config.RESEND_API_KEY else
                                   _http_check("https://api.resend.com/domains", headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"})),
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda kv: _timed(kv[0], kv[1]), checks.items()))
    return results


@bp.get("/health")
def health():
    out = {"app": "mausam", "config_missing": config.missing_required(), "gemini_model": config.GEMINI_MODEL}
    try:
        db.scalar("SELECT 1")
        out["database"] = "ok"
        latest = db.scalar("""SELECT MAX(n.nav_date) FROM nav_history n JOIN funds f ON f.scheme_code = n.scheme_code
                              WHERE f.is_active""")
        out["latest_nav_date"] = latest.isoformat() if latest else None
        out["stale"] = returns.is_stale(latest, date.today()) if latest else None
        out["tables"] = db.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = current_schema()")
    except Exception as exc:  # noqa: BLE001
        out["database"] = f"error: {str(exc)[:160]}"
    if request.args.get("deep") == "1":
        out["services"] = service_checks()
    healthy = out.get("database") == "ok" and not out["config_missing"]
    return ok(out, 200 if healthy else 503)
