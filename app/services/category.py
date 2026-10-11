"""Category averages, computed by us from up to 12 named peer funds.

There is no free "official" category average, so for each peer group we pick up
to 12 similar funds (direct plan, growth option, same mfapi category, still
publishing NAVs), store their NAV history, and take a simple mean of their
returns. The UI always says how many funds the average came from.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import db
from . import mfapi, returns
from .benchmarks import classify, peer_name_filter

MAX_PEERS = 12


def _is_direct_growth(name: str) -> bool:
    n = name.lower()
    return ("direct" in n and "growth" in n
            and not any(w in n for w in ("idcw", "dividend", "bonus", "segregated", "payout", "reinvest")))


def discover_peers(peer_group: str, exclude: set[int] | None = None, max_checks: int = 40) -> list[dict]:
    """Find up to MAX_PEERS live funds in the same peer group. Slow-ish: one mfapi call per candidate."""
    name_ok = peer_name_filter(peer_group)
    exclude = exclude or set()
    candidates = [s for s in mfapi.all_schemes()
                  if _is_direct_growth(s["scheme_name"]) and name_ok(s["scheme_name"]) and s["scheme_code"] not in exclude]
    # newer scheme codes first tends to skip long-dead schemes; but keep old large funds too
    candidates.sort(key=lambda s: -s["scheme_code"])
    peers: list[dict] = []
    checked = 0
    recent_cutoff = date.today() - timedelta(days=10)
    for cand in candidates:
        if len(peers) >= MAX_PEERS or checked >= max_checks:
            break
        checked += 1
        try:
            data = mfapi.scheme(cand["scheme_code"])
        except Exception:  # noqa: BLE001 - skip broken candidates
            continue
        meta, rows = data["meta"], data["rows"]
        if not rows or rows[-1][0] < recent_cutoff or len(rows) < 250:
            continue
        info = classify(meta.get("scheme_category"), meta.get("scheme_name"))
        if info["peer_group"] != peer_group:
            continue
        mfapi.store_rows(cand["scheme_code"], rows)
        peers.append({"scheme_code": cand["scheme_code"], "scheme_name": meta.get("scheme_name")})
    return peers


def save_peers(peer_group: str, peers: list[dict]) -> None:
    db.execute("DELETE FROM category_peers WHERE scheme_category = %s", [peer_group])
    db.executemany(
        "INSERT INTO category_peers (scheme_category, scheme_code, scheme_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        [(peer_group, p["scheme_code"], p["scheme_name"]) for p in peers],
    )


def peers(peer_group: str) -> list[dict]:
    return db.query("SELECT scheme_code, scheme_name FROM category_peers WHERE scheme_category = %s ORDER BY scheme_name", [peer_group])


def average_returns(peer_series: dict[int, list], as_of: date) -> dict:
    """Simple mean of each period's return across peers that have it."""
    per_period: dict[str, list[float]] = {k: [] for k in returns.PERIODS}
    for rows in peer_series.values():
        r = returns.compute(rows, as_of)
        if r["as_of"] is None or (as_of - r["as_of"]).days > 7:
            continue
        for k in returns.PERIODS:
            if r[k] is not None:
                per_period[k].append(r[k])
    out = {k: (round(sum(v) / len(v), 4) if v else None) for k, v in per_period.items()}
    out["peer_count"] = max((len(v) for v in per_period.values()), default=0)
    return out


def compute_stats(peer_group: str, as_of: date, store: bool = True) -> dict:
    codes = [p["scheme_code"] for p in peers(peer_group)]
    if not codes:
        return {k: None for k in returns.PERIODS} | {"peer_count": 0}
    series = mfapi.series_many(codes, as_of - timedelta(days=3 * 366 + 20))
    series = {c: [p for p in rows if p[0] <= as_of] for c, rows in series.items()}
    stats = average_returns(series, as_of)
    if store and stats["peer_count"]:
        db.execute(
            """INSERT INTO category_stats (scheme_category, as_of, ret_1w, ret_1m, ret_ytd, ret_1y, ret_3y_cagr, peer_count)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (scheme_category, as_of) DO UPDATE SET ret_1w=EXCLUDED.ret_1w, ret_1m=EXCLUDED.ret_1m,
               ret_ytd=EXCLUDED.ret_ytd, ret_1y=EXCLUDED.ret_1y, ret_3y_cagr=EXCLUDED.ret_3y_cagr, peer_count=EXCLUDED.peer_count""",
            [peer_group, as_of, stats["ret_1w"], stats["ret_1m"], stats["ret_ytd"], stats["ret_1y"], stats["ret_3y_cagr"], stats["peer_count"]],
        )
    return stats


def latest_stats(peer_group: str, on_or_before: date | None = None) -> dict | None:
    if on_or_before:
        return db.one("SELECT * FROM category_stats WHERE scheme_category = %s AND as_of <= %s ORDER BY as_of DESC LIMIT 1",
                      [peer_group, on_or_before])
    return db.one("SELECT * FROM category_stats WHERE scheme_category = %s ORDER BY as_of DESC LIMIT 1", [peer_group])


def stats_for(peer_group: str, as_of: date) -> dict:
    """Category returns as of a date: computed live from stored peer NAVs, else the cached row."""
    if not peer_group:
        return {k: None for k in returns.PERIODS} | {"peer_count": 0}
    live = compute_stats(peer_group, as_of, store=False)
    if live.get("peer_count"):
        return live
    cached = latest_stats(peer_group, as_of)
    if cached and (as_of - cached["as_of"]).days <= 7:
        return {k: cached[k] for k in returns.PERIODS} | {"peer_count": cached["peer_count"]}
    return {k: None for k in returns.PERIODS} | {"peer_count": 0}


def category_series(peer_group: str, start: date) -> list[tuple[date, float]]:
    """Daily 'average peer' line, rebased to 100 at start (for chart overlays)."""
    codes = [p["scheme_code"] for p in peers(peer_group)]
    if not codes:
        return []
    all_series = mfapi.series_many(codes, start - timedelta(days=10))
    rebased = [dict(returns.rebase(s, start)) for s in all_series.values() if s]
    rebased = [r for r in rebased if r]
    if not rebased:
        return []
    dates = sorted(set().union(*[set(r) for r in rebased]))
    out = []
    last: list[float | None] = [None] * len(rebased)
    for d in dates:
        for i, r in enumerate(rebased):
            if d in r:
                last[i] = r[d]
        vals = [v for v in last if v is not None]
        if len(vals) >= max(1, len(rebased) // 2):
            out.append((d, round(sum(vals) / len(vals), 4)))
    return out


def refresh_all_groups(as_of: date, rediscover: bool = False) -> dict:
    """Make sure every active fund's peer group has peers and fresh stats."""
    groups = [r["peer_group"] for r in db.query(
        "SELECT DISTINCT peer_group FROM funds WHERE is_active AND peer_group IS NOT NULL")]
    result = {}
    for g in groups:
        existing = peers(g)
        if rediscover or len(existing) < 5:
            own = {r["scheme_code"] for r in db.query("SELECT scheme_code FROM funds")}
            found = discover_peers(g, exclude=own)
            if found:
                save_peers(g, found)
        else:
            for p in existing:
                try:
                    mfapi.top_up(p["scheme_code"])
                except Exception:  # noqa: BLE001
                    pass
        stats = compute_stats(g, as_of)
        result[g] = {"peers": len(peers(g)), "stats": stats}
    return result
