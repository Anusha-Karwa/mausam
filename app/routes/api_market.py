"""Market pulse: index levels and moves (Yahoo or proxy), news with 'why it matters'."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, request

from .. import cache, db
from ..services import indices, news, returns
from ._util import fail, ok

bp = Blueprint("api_market", __name__, url_prefix="/api/market")

CARDS = [("NIFTY50", "Nifty 50"), ("MIDCAP150", "Nifty Midcap 150"), ("SMALLCAP100", "Nifty Smallcap 100"),
         ("SMALLCAP250", "Nifty Smallcap 250"), ("SP500", "S&P 500 (in rupees)"), ("NASDAQ100", "Nasdaq 100 (in rupees)")]
RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "3Y": 3 * 366, "5Y": 5 * 366}


def index_cards() -> list[dict]:
    """Index cards, cached for 10 minutes (index data changes once a day)."""
    return cache.cached("index_cards", 600, _index_cards)


def _index_cards() -> list[dict]:
    out = []
    start = date.today() - timedelta(days=400)
    for key, label in CARDS:
        s = indices.benchmark_series(key, start)
        rows = s["rows"]
        if not rows:
            out.append({"key": key, "label": label, "available": False})
            continue
        r = returns.compute(rows)
        day = returns.simple_return(rows[-1][1], rows[-2][1]) if len(rows) >= 2 else None
        raw_level = None
        if s["source"] == "yahoo":
            raw = db.one("SELECT close FROM index_history WHERE benchmark_key = %s ORDER BY trade_date DESC LIMIT 1", [key])
            raw_level = raw["close"] if raw else None
        out.append({"key": key, "label": label, "available": True, "level": raw_level if raw_level is not None else rows[-1][1],
                    "as_of": rows[-1][0], "ret_1d": round(day, 2) if day is not None else None,
                    "ret_1w": r["ret_1w"], "ret_1m": r["ret_1m"], "ret_ytd": r["ret_ytd"], "source": s["source"],
                    "note": s["note"], "history_points": len(rows)})
    return out


@bp.get("/indices")
def indices_route():
    key = request.args.get("key")
    if key:
        rng = (request.args.get("range") or "1Y").upper()
        if rng not in RANGES:
            return fail("range must be one of " + ", ".join(RANGES))
        s = indices.benchmark_series(key, date.today() - timedelta(days=RANGES[rng]))
        return ok({"key": key, "source": s["source"], "note": s["note"],
                   "points": [[d.isoformat(), v] for d, v in s["rows"] if d >= date.today() - timedelta(days=RANGES[rng])]})
    return ok(index_cards())


@bp.get("/news")
def news_route():
    limit = min(int(request.args.get("limit", 12) or 12), 50)
    return ok(news.latest(limit))
