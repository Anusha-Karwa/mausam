"""mfapi.in client: India's free mutual fund NAV API (no key).

NAVs are published once per business day after market close. They are NOT real-time.
Dates come as dd-mm-yyyy strings and NAVs as strings; we parse both carefully.
"""
from __future__ import annotations

import time
from datetime import date, datetime

from .. import db
from .http import FetchError, get_json

BASE = "https://api.mfapi.in/mf"

_cache: dict[str, tuple[float, object]] = {}
CACHE_SECONDS = 600


def _cached(key: str, loader):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = loader()
    _cache[key] = (time.time(), value)
    return value


def parse_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d-%m-%Y").date()


def parse_nav(text) -> float | None:
    try:
        value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_rows(data: list[dict]) -> list[tuple[date, float]]:
    """mfapi rows -> [(date, nav)] oldest first, skipping junk rows."""
    out: dict[date, float] = {}
    for row in data or []:
        try:
            d = parse_date(row["date"])
        except (KeyError, ValueError, TypeError, AttributeError):  # missing, malformed, null or non-dict rows
            continue
        nav = parse_nav(row.get("nav"))
        if nav is not None:
            out[d] = nav
    return sorted(out.items())


def search(q: str) -> list[dict]:
    q = (q or "").strip()
    if len(q) < 2:
        return []
    rows = _cached(f"search:{q.lower()}", lambda: get_json(f"{BASE}/search", params={"q": q}))
    return [{"scheme_code": int(r["schemeCode"]), "scheme_name": r["schemeName"]} for r in rows or []]


def all_schemes() -> list[dict]:
    """Every scheme mfapi knows (~38k rows, includes closed ones). Used for peer discovery."""
    rows = _cached("all", lambda: get_json(BASE, timeout=40))
    return [{"scheme_code": int(r["schemeCode"]), "scheme_name": r["schemeName"]} for r in rows or []]


def scheme(code: int, start: date | None = None, end: date | None = None) -> dict:
    """Full (or ranged) history + meta. Raises FetchError if the code is invalid."""
    params = {}
    if start:
        params["startDate"] = start.isoformat()
    if end:
        params["endDate"] = end.isoformat()
    payload = get_json(f"{BASE}/{int(code)}", params=params or None, timeout=20)
    meta = payload.get("meta") or {}
    if not meta or not meta.get("scheme_name"):
        raise FetchError(f"Scheme {code} not found")
    return {"meta": meta, "rows": parse_rows(payload.get("data") or [])}


def latest(code: int) -> dict:
    payload = get_json(f"{BASE}/{int(code)}/latest")
    meta = payload.get("meta") or {}
    rows = parse_rows(payload.get("data") or [])
    if not meta.get("scheme_name") or not rows:
        raise FetchError(f"Scheme {code} has no NAV data")
    return {"meta": meta, "nav_date": rows[-1][0], "nav": rows[-1][1]}


def store_rows(code: int, rows: list[tuple[date, float]], source: str = "mfapi") -> int:
    """Idempotent insert into nav_history. `source` records where the NAV came from (mfapi | amfi)."""
    return db.executemany(
        "INSERT INTO nav_history (scheme_code, nav_date, nav, source) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        [(int(code), d, round(v, 4), source) for d, v in rows],
    )


def last_stored_date(code: int) -> date | None:
    """Latest date we got from mfapi. AMFI fallback rows are ignored here, so when mfapi
    recovers we still re-fetch (and fill) any days that only AMFI covered."""
    return db.scalar("SELECT MAX(nav_date) FROM nav_history WHERE scheme_code = %s AND source IS DISTINCT FROM 'amfi'",
                     [int(code)])


def backfill(code: int) -> dict:
    """Fetch full history and store it. Returns meta and counts."""
    data = scheme(code)
    store_rows(code, data["rows"])
    return {"meta": data["meta"], "rows": len(data["rows"]),
            "first": data["rows"][0][0] if data["rows"] else None,
            "last": data["rows"][-1][0] if data["rows"] else None}


def top_up(code: int) -> dict:
    """Daily refresh: fetch the latest NAV; if there is a gap since our last stored date, fill it."""
    last = last_stored_date(code)
    if last is None:
        info = backfill(code)
        return {"code": code, "filled": info["rows"], "latest": info["last"]}
    lat = latest(code)
    filled = 0
    if lat["nav_date"] > last:
        gap_days = (lat["nav_date"] - last).days
        if gap_days > 1:
            data = scheme(code, start=last, end=lat["nav_date"])
            filled = len(data["rows"])
            store_rows(code, data["rows"])
        store_rows(code, [(lat["nav_date"], lat["nav"])])
        filled = max(filled, 1)
    return {"code": code, "filled": filled, "latest": lat["nav_date"]}


def series(code: int, start: date | None = None) -> list[tuple[date, float]]:
    if start:
        rows = db.query(
            "SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s AND nav_date >= %s ORDER BY nav_date",
            [int(code), start],
        )
    else:
        rows = db.query("SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date", [int(code)])
    return [(r["nav_date"], float(r["nav"])) for r in rows]


def series_many(codes: list[int], start: date | None = None) -> dict[int, list[tuple[date, float]]]:
    codes = sorted({int(c) for c in codes if c})
    out: dict[int, list[tuple[date, float]]] = {c: [] for c in codes}
    if not codes:
        return out
    sql = "SELECT scheme_code, nav_date, nav FROM nav_history WHERE scheme_code = ANY(%s)"
    params: list = [codes]
    if start:
        sql += " AND nav_date >= %s"
        params.append(start)
    for r in db.query(sql + " ORDER BY scheme_code, nav_date", params):
        out[r["scheme_code"]].append((r["nav_date"], float(r["nav"])))
    return out
