"""Weekly snapshots: one row per fund per Friday, stored forever for trend tracking.

Each snapshot holds the fund's returns, its benchmark's and category's returns,
the gaps, the market-wide/fund-specific label, Meera's units/invested/value
that week, and flags (lagging, stale data...). Past weeks can be rebuilt from
NAV history, which is how Trends and the streak logic work from day one.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from .. import db
from . import attribution, category, indices, mfapi, portfolio, returns
from .verdict import is_lagging

DEFAULT_THRESHOLDS = {
    "equity_week_lag_pp": 1.0, "equity_month_lag_pp": 2.0,
    "debt_week_lag_pp": 0.15, "debt_month_lag_pp": 0.4,
    "intl_week_lag_pp": 1.5, "intl_month_lag_pp": 3.0,
    "consecutive_weeks_to_watch": 3, "allocation_drift_pp": 10,
    "market_wide_band_pp": 0.75, "debt_market_wide_band_pp": 0.1, "intl_market_wide_band_pp": 1.0,
}


def thresholds(user_id: int) -> dict:
    row = db.one("SELECT thresholds FROM settings WHERE user_id = %s", [user_id])
    merged = dict(DEFAULT_THRESHOLDS)
    merged.update((row or {}).get("thresholds") or {})
    return merged


def load_context(user_id: int, start: date, funds: list[dict] | None = None) -> dict:
    """Load everything needed to compute snapshots from `start` onwards, in a few queries."""
    funds = funds if funds is not None else portfolio.active_funds(user_id)
    history_from = start - timedelta(days=3 * 366 + 30)
    txns = portfolio.load_transactions(user_id)
    by_fund: dict[int, list[dict]] = defaultdict(list)
    for t in txns:
        by_fund[t["fund_id"]].append(t)
    nav = mfapi.series_many([f["scheme_code"] for f in funds], history_from)
    bench = {}
    for key in {f["benchmark_key"] for f in funds if f["benchmark_key"] and f["benchmark_key"] != "CATEGORY_ONLY"}:
        bench[key] = indices.benchmark_series(key, history_from)
    peers = {}
    for g in {f["peer_group"] for f in funds if f["peer_group"]}:
        codes = [p["scheme_code"] for p in category.peers(g)]
        peers[g] = mfapi.series_many(codes, history_from) if codes else {}
    return {"user_id": user_id, "funds": funds, "txns_by_fund": by_fund, "nav": nav, "bench": bench,
            "peers": peers, "thresholds": thresholds(user_id)}


def _category_returns(ctx: dict, group: str | None, week: date) -> dict:
    empty = {k: None for k in returns.PERIODS} | {"peer_count": 0}
    if not group:
        return empty
    series = ctx["peers"].get(group) or {}
    if series:
        clipped = {c: [p for p in rows if p[0] <= week] for c, rows in series.items()}
        stats = category.average_returns(clipped, week)
        if stats["peer_count"]:
            return stats
    cached = category.latest_stats(group, week)
    if cached and (week - cached["as_of"]).days <= 7:
        return {k: cached[k] for k in returns.PERIODS} | {"peer_count": cached["peer_count"]}
    return empty


def compute_week(ctx: dict, fund: dict, week: date, holdings: dict | None = None) -> dict:
    series = [p for p in ctx["nav"].get(fund["scheme_code"], []) if p[0] <= week]
    r = returns.compute(series, week)
    stale = r["as_of"] is None or (week - r["as_of"]).days > 4
    b = ctx["bench"].get(fund["benchmark_key"] or "", {"rows": [], "source": None})
    b_rows = [p for p in b["rows"] if p[0] <= week]
    br = returns.compute(b_rows, week) if b_rows else {k: None for k in returns.PERIODS}
    if b_rows and (week - b_rows[-1][0]).days > 5:
        br = {k: None for k in returns.PERIODS}
    cr = _category_returns(ctx, fund.get("peer_group"), week)

    delta_b = returns.diff(r["ret_1w"], br.get("ret_1w"))
    delta_c = returns.diff(r["ret_1w"], cr.get("ret_1w"))
    ref = br.get("ret_1w") if br.get("ret_1w") is not None else cr.get("ret_1w")
    band = attribution.band_for(fund["asset_class"], ctx["thresholds"])
    attr = attribution.attribute(r["ret_1w"], ref, band, stale=stale)

    h = (holdings or {}).get(fund["id"]) or {"units": 0.0, "invested": 0.0, "value": 0.0}
    snap = {
        "user_id": ctx["user_id"], "week_ending": week, "fund_id": fund["id"], "scheme_code": fund["scheme_code"],
        "nav": r["value"], "nav_date": r["as_of"],
        "ret_1w": r["ret_1w"], "ret_1m": r["ret_1m"], "ret_ytd": r["ret_ytd"], "ret_1y": r["ret_1y"], "ret_3y_cagr": r["ret_3y_cagr"],
        "bench_ret_1w": br.get("ret_1w"), "bench_ret_1m": br.get("ret_1m"), "bench_ret_ytd": br.get("ret_ytd"),
        "cat_ret_1w": cr.get("ret_1w"), "cat_ret_1m": cr.get("ret_1m"), "cat_ret_ytd": cr.get("ret_ytd"),
        "delta_bench_1w": delta_b, "delta_cat_1w": delta_c, "attribution": attr,
        "units": h["units"], "invested": h["invested"], "value": h["value"],
    }
    flags = []
    if stale:
        flags.append("stale_data")
    if is_lagging(snap, fund["asset_class"], ctx["thresholds"]):
        flags.append("lagging_benchmark")
    if fund["benchmark_key"] in (None, "CATEGORY_ONLY"):
        flags.append("category_only")
    if b.get("source") == "proxy":
        flags.append("benchmark_via_proxy")
    if not cr.get("peer_count"):
        flags.append("no_category_average")
    snap["flags"] = flags
    snap["peer_count"] = cr.get("peer_count", 0)
    snap["bench_source"] = b.get("source")
    return snap


UPSERT = """
INSERT INTO weekly_snapshots (user_id, week_ending, fund_id, scheme_code, nav, nav_date, ret_1w, ret_1m, ret_ytd, ret_1y,
  ret_3y_cagr, bench_ret_1w, bench_ret_1m, bench_ret_ytd, cat_ret_1w, cat_ret_1m, cat_ret_ytd, delta_bench_1w,
  delta_cat_1w, attribution, units, invested, value, flags)
VALUES (%(user_id)s, %(week_ending)s, %(fund_id)s, %(scheme_code)s, %(nav)s, %(nav_date)s, %(ret_1w)s, %(ret_1m)s,
  %(ret_ytd)s, %(ret_1y)s, %(ret_3y_cagr)s, %(bench_ret_1w)s, %(bench_ret_1m)s, %(bench_ret_ytd)s, %(cat_ret_1w)s,
  %(cat_ret_1m)s, %(cat_ret_ytd)s, %(delta_bench_1w)s, %(delta_cat_1w)s, %(attribution)s, %(units)s, %(invested)s,
  %(value)s, %(flags)s)
ON CONFLICT (user_id, week_ending, fund_id) DO UPDATE SET
  nav=EXCLUDED.nav, nav_date=EXCLUDED.nav_date, ret_1w=EXCLUDED.ret_1w, ret_1m=EXCLUDED.ret_1m, ret_ytd=EXCLUDED.ret_ytd,
  ret_1y=EXCLUDED.ret_1y, ret_3y_cagr=EXCLUDED.ret_3y_cagr, bench_ret_1w=EXCLUDED.bench_ret_1w,
  bench_ret_1m=EXCLUDED.bench_ret_1m, bench_ret_ytd=EXCLUDED.bench_ret_ytd, cat_ret_1w=EXCLUDED.cat_ret_1w,
  cat_ret_1m=EXCLUDED.cat_ret_1m, cat_ret_ytd=EXCLUDED.cat_ret_ytd, delta_bench_1w=EXCLUDED.delta_bench_1w,
  delta_cat_1w=EXCLUDED.delta_cat_1w, attribution=EXCLUDED.attribution, units=EXCLUDED.units,
  invested=EXCLUDED.invested, value=EXCLUDED.value, flags=EXCLUDED.flags
"""


def _row_params(snap: dict) -> dict:
    from psycopg.types.json import Jsonb

    params = {k: snap[k] for k in snap if k not in ("peer_count", "bench_source")}
    params["flags"] = Jsonb(snap["flags"], dumps=lambda obj: json.dumps(obj, default=str))
    return params


def save(snaps: list[dict]) -> int:
    if not snaps:
        return 0

    def fn(conn):
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(UPSERT, [_row_params(s) for s in snaps])
        return len(snaps)

    return db._run(fn)


def build_weeks(user_id: int, weeks: list[date], funds: list[dict] | None = None, persist: bool = True) -> list[dict]:
    if not weeks:
        return []
    ctx = load_context(user_id, min(weeks), funds)
    out = []
    for week in sorted(weeks):
        holdings = portfolio.value_on(user_id, week, ctx["funds"], ctx["txns_by_fund"], ctx["nav"])
        for f in ctx["funds"]:
            out.append(compute_week(ctx, f, week, holdings))
    if persist:
        save(out)
    return out


def recent_fridays(n: int, today: date | None = None) -> list[date]:
    last = returns.last_friday(today or date.today())
    return [last - timedelta(weeks=i) for i in range(n)][::-1]


def rebuild(user_id: int, n_weeks: int = 26, funds: list[dict] | None = None) -> int:
    return len(build_weeks(user_id, recent_fridays(n_weeks), funds))


def history(user_id: int, fund_id: int, up_to: date, limit: int = 26) -> list[dict]:
    """Snapshots for one fund, newest first."""
    return db.query("SELECT * FROM weekly_snapshots WHERE user_id = %s AND fund_id = %s AND week_ending <= %s "
                    "ORDER BY week_ending DESC LIMIT %s", [user_id, fund_id, up_to, limit])


def for_week(user_id: int, week: date) -> list[dict]:
    return db.query("""SELECT s.*, f.short_name, f.scheme_name, f.asset_class, f.benchmark_key, f.colour, f.peer_group
                       FROM weekly_snapshots s JOIN funds f ON f.id = s.fund_id
                       WHERE s.user_id = %s AND s.week_ending = %s AND f.is_active ORDER BY f.id""", [user_id, week])


def latest_week(user_id: int) -> date | None:
    return db.scalar("SELECT MAX(week_ending) FROM weekly_snapshots WHERE user_id = %s", [user_id])
