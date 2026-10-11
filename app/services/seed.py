"""Demo data for Meera: five real funds, 3 years of monthly SIPs at real historical NAVs,
reconstructed weekly snapshots, peers and a first report.

Split into steps so it can run in pieces (each step is idempotent):
  benchmarks -> funds -> transactions -> peers -> snapshots -> report
"""
from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

from .. import db
from . import category, funds as fund_service, indices, mfapi, portfolio, returns, snapshots

# Confirmed with Anusha before saving (see docs/DATA_SOURCES.md). Direct Plan - Growth versions.
MEERA_FUNDS = [
    {"code": 120716, "short_name": "Large-cap index fund", "sip": 5000},   # UTI Nifty 50 Index Fund
    {"code": 118989, "short_name": "Mid-cap fund", "sip": 3000},           # HDFC Mid Cap Fund
    {"code": 118778, "short_name": "Small-cap fund", "sip": 2000},         # Nippon India Small Cap Fund
    {"code": 120754, "short_name": "Debt fund", "sip": 3000},              # ICICI Prudential Short Term Fund
    {"code": 149961, "short_name": "International fund", "sip": 2000},     # Invesco EQQQ Nasdaq-100 ETF FoF
]
SIP_DAY = 5
USER_ID = 1


def step_benchmarks() -> dict:
    """Backfill 5 years of index closes and the full NAV history of every proxy fund."""
    idx = indices.refresh("5y")
    proxies = {}
    for b in indices.benchmarks():
        if b["proxy_scheme_code"]:
            try:
                proxies[b["key"]] = mfapi.backfill(b["proxy_scheme_code"])["rows"]
            except Exception as exc:  # noqa: BLE001
                proxies[b["key"]] = f"failed: {exc}"
    return {"indices": idx, "proxies": proxies}


def step_funds(fund_list: list[dict] | None = None) -> dict:
    out = []
    for spec in fund_list or MEERA_FUNDS:
        existing = fund_service.get_by_code(USER_ID, spec["code"])
        if existing and existing["is_active"]:
            mfapi.backfill(spec["code"])
            out.append({"code": spec["code"], "status": "already present"})
            continue
        res = fund_service.add(USER_ID, spec["code"], short_name=spec["short_name"], sip_amount=spec["sip"],
                               sip_day=SIP_DAY, with_peers=False, rebuild_weeks=0)
        out.append({"code": spec["code"], "status": "added", "nav_rows": res["nav_rows"]})
    return {"funds": out}


def sip_dates(today: date, months: int = 36, day: int = SIP_DAY) -> list[date]:
    first = (today.replace(day=1) - relativedelta(months=months))
    return [d for d in (first + relativedelta(months=i, day=day) for i in range(months + 1)) if d <= today]


def step_transactions(today: date | None = None) -> dict:
    """3 years of SIPs on the 5th (next business day's NAV if a holiday), one lump sum, one partial sell."""
    today = today or date.today()
    funds = {f["scheme_code"]: f for f in portfolio.active_funds(USER_ID)}
    db.execute("DELETE FROM transactions WHERE user_id = %s AND note LIKE %s", [USER_ID, "%(demo data)%"])
    rows = []
    for spec in MEERA_FUNDS:
        f = funds.get(spec["code"])
        if not f:
            continue
        for d in sip_dates(today):
            nav = portfolio.nav_for_date(f["scheme_code"], d)
            if not nav or nav["provisional"]:
                continue
            units = round(spec["sip"] / nav["nav"], 4)
            rows.append((USER_ID, f["id"], "sip", d, spec["sip"], nav["nav"], units, "Monthly SIP (demo data)"))
    # One lump-sum buy and one partial sell, for realism.
    large, debt = funds.get(MEERA_FUNDS[0]["code"]), funds.get(MEERA_FUNDS[3]["code"])
    if large:
        d = today - relativedelta(months=18)
        nav = portfolio.nav_for_date(large["scheme_code"], d)
        if nav:
            rows.append((USER_ID, large["id"], "buy", d, 25000, nav["nav"], round(25000 / nav["nav"], 4),
                         "Lump sum from annual bonus (demo data)"))
    db.executemany("""INSERT INTO transactions (user_id, fund_id, txn_type, txn_date, amount, nav, units, note)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    if debt:
        d = today - relativedelta(months=6)
        nav = portfolio.nav_for_date(debt["scheme_code"], d)
        if nav:
            held = portfolio.units_held(USER_ID, debt["id"], up_to=d)
            units = round(min(held * 0.25, 10000 / nav["nav"]), 4)
            if units > 0:
                db.execute("""INSERT INTO transactions (user_id, fund_id, txn_type, txn_date, amount, nav, units, note)
                              VALUES (%s,%s,'sell',%s,%s,%s,%s,%s)""",
                           [USER_ID, debt["id"], d, round(units * nav["nav"], 2), nav["nav"], units,
                            "Partial withdrawal for a trip (demo data)"])
                rows.append(("sell",))
    return {"transactions": len(rows)}


def step_peers(group: str | None = None) -> dict:
    groups = [group] if group else [r["peer_group"] for r in db.query(
        "SELECT DISTINCT peer_group FROM funds WHERE is_active AND peer_group IS NOT NULL")]
    out = {}
    own = {r["scheme_code"] for r in db.query("SELECT scheme_code FROM funds")}
    for g in groups:
        found = category.discover_peers(g, exclude=own)
        if found:
            category.save_peers(g, found)
        stats = category.compute_stats(g, returns.last_friday(date.today()))
        out[g] = {"peers": len(found), "peer_count_in_average": stats.get("peer_count")}
    return out


def step_snapshots(weeks: int = 26) -> dict:
    return {"snapshots": snapshots.rebuild(USER_ID, weeks)}
