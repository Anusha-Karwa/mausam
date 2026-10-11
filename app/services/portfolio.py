"""Portfolio maths: units, FIFO cost, value, gain, allocation, drift, XIRR.

Mausam only RECORDS investments. Real buying and selling happens in Meera's
investing app; she logs it here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .. import db
from . import mfapi, returns
from .xirr import xirr

ASSET_CLASSES = ("equity", "debt", "international")


# ---------- transactions ----------

def load_transactions(user_id: int, include_simulated: bool = False, fund_id: int | None = None,
                      up_to: date | None = None) -> list[dict]:
    sql = """SELECT t.*, f.scheme_code, f.short_name, f.scheme_name, f.asset_class
             FROM transactions t JOIN funds f ON f.id = t.fund_id
             WHERE t.user_id = %s"""
    params: list = [user_id]
    if not include_simulated:
        sql += " AND NOT t.is_simulated"
    if fund_id:
        sql += " AND t.fund_id = %s"
        params.append(fund_id)
    if up_to:
        sql += " AND t.txn_date <= %s"
        params.append(up_to)
    return db.query(sql + " ORDER BY t.txn_date, t.id", params)


def nav_for_date(scheme_code: int, d: date) -> dict | None:
    """NAV on d, or the next business day's NAV if d was a holiday/weekend.

    If no NAV exists on/after d yet (e.g. today, before NAVs are published), we use
    the latest available NAV and mark it provisional.
    """
    row = db.one("SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s AND nav_date >= %s ORDER BY nav_date LIMIT 1",
                 [scheme_code, d])
    if row:
        return {"nav_date": row["nav_date"], "nav": float(row["nav"]), "provisional": False,
                "shifted": row["nav_date"] != d}
    row = db.one("SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date DESC LIMIT 1", [scheme_code])
    if row:
        return {"nav_date": row["nav_date"], "nav": float(row["nav"]), "provisional": True, "shifted": True}
    return None


# ---------- FIFO holdings ----------

def fifo(txns: list[dict]) -> dict:
    """Replay one fund's transactions in date order. Returns units, invested (cost of units still held),
    realised gain, average cost NAV and remaining lots."""
    lots: list[list[float]] = []  # [units, cost_per_unit]
    realised = 0.0
    total_bought = 0.0
    for t in sorted(txns, key=lambda x: (x["txn_date"], x.get("id") or 0)):
        units = float(t["units"])
        if t["txn_type"] in ("sip", "buy"):
            lots.append([units, float(t["amount"]) / units if units else 0.0])
            total_bought += float(t["amount"])
        else:  # sell
            to_sell = units
            proceeds = float(t["amount"])
            cost = 0.0
            while to_sell > 1e-9 and lots:
                take = min(lots[0][0], to_sell)
                cost += take * lots[0][1]
                lots[0][0] -= take
                to_sell -= take
                if lots[0][0] <= 1e-9:
                    lots.pop(0)
            realised += proceeds - cost
    units_held = sum(l[0] for l in lots)
    invested = sum(l[0] * l[1] for l in lots)
    return {
        "units": round(units_held, 4),
        "invested": round(invested, 2),
        "realised": round(realised, 2),
        "total_bought": round(total_bought, 2),
        "avg_cost_nav": round(invested / units_held, 4) if units_held > 1e-9 else None,
    }


def units_held(user_id: int, fund_id: int, up_to: date | None = None) -> float:
    return fifo(load_transactions(user_id, fund_id=fund_id, up_to=up_to))["units"]


def cash_flows(txns: list[dict]) -> list[tuple[date, float]]:
    flows = []
    for t in txns:
        amount = float(t["amount"])
        flows.append((t["txn_date"], -amount if t["txn_type"] in ("sip", "buy") else amount))
    return flows


# ---------- summaries ----------

def latest_navs(codes: list[int]) -> dict[int, dict]:
    """Latest and previous NAV per scheme code."""
    if not codes:
        return {}
    rows = db.query(
        """SELECT scheme_code, nav_date, nav FROM (
             SELECT scheme_code, nav_date, nav, ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) AS rn
             FROM nav_history WHERE scheme_code = ANY(%s)) x WHERE rn <= 2 ORDER BY scheme_code, nav_date DESC""",
        [list({int(c) for c in codes})],
    )
    out: dict[int, dict] = {}
    for r in rows:
        entry = out.setdefault(r["scheme_code"], {"nav": None, "nav_date": None, "prev_nav": None, "prev_date": None})
        if entry["nav"] is None:
            entry["nav"], entry["nav_date"] = float(r["nav"]), r["nav_date"]
        else:
            entry["prev_nav"], entry["prev_date"] = float(r["nav"]), r["nav_date"]
    return out


def active_funds(user_id: int) -> list[dict]:
    return db.query("SELECT * FROM funds WHERE user_id = %s AND is_active ORDER BY id", [user_id])


def summary(user_id: int, include_simulated: bool = False, today: date | None = None) -> dict:
    today = today or date.today()
    funds = active_funds(user_id)
    txns = load_transactions(user_id, include_simulated=include_simulated)
    by_fund: dict[int, list[dict]] = defaultdict(list)
    for t in txns:
        by_fund[t["fund_id"]].append(t)
    navs = latest_navs([f["scheme_code"] for f in funds])

    rows = []
    totals = {"invested": 0.0, "value": 0.0, "day_change": 0.0, "realised": 0.0}
    all_flows: list[tuple[date, float]] = []
    for f in funds:
        h = fifo(by_fund.get(f["id"], []))
        n = navs.get(f["scheme_code"], {})
        nav = n.get("nav")
        value = round(h["units"] * nav, 2) if nav else 0.0
        day_change = round(h["units"] * (nav - n["prev_nav"]), 2) if nav and n.get("prev_nav") else 0.0
        flows = cash_flows(by_fund.get(f["id"], []))
        f_xirr = xirr(flows + [(n.get("nav_date") or today, value)]) if value > 0 and flows else None
        gain = round(value - h["invested"], 2)
        rows.append({
            "fund": f, "fund_id": f["id"], "scheme_code": f["scheme_code"],
            "name": f["short_name"] or f["scheme_name"], "asset_class": f["asset_class"],
            "units": h["units"], "avg_cost_nav": h["avg_cost_nav"], "nav": nav, "nav_date": n.get("nav_date"),
            "prev_nav": n.get("prev_nav"), "invested": h["invested"], "value": value, "gain": gain,
            "gain_pct": round(gain / h["invested"] * 100, 2) if h["invested"] else None,
            "realised": h["realised"], "xirr": round(f_xirr * 100, 2) if f_xirr is not None else None,
            "day_change": day_change,
            "day_change_pct": round((nav / n["prev_nav"] - 1) * 100, 2) if nav and n.get("prev_nav") else None,
            "colour": f["colour"],
        })
        totals["invested"] += h["invested"]
        totals["value"] += value
        totals["day_change"] += day_change
        totals["realised"] += h["realised"]
        all_flows += flows

    latest_date = max((r["nav_date"] for r in rows if r["nav_date"]), default=None)
    total_value = round(totals["value"], 2)
    for r in rows:
        r["weight"] = round(r["value"] / total_value * 100, 2) if total_value else 0.0
    overall_xirr = xirr(all_flows + [(latest_date or today, total_value)]) if total_value > 0 and all_flows else None
    gain = round(totals["value"] - totals["invested"], 2)
    prev_value = total_value - totals["day_change"]

    alloc = allocation(rows)
    target = target_allocation(user_id)
    drift = {k: round(alloc.get(k, 0.0) - float(target.get(k, 0)), 2) for k in ASSET_CLASSES}
    return {
        "funds": rows,
        "invested": round(totals["invested"], 2),
        "value": total_value,
        "gain": gain,
        "gain_pct": round(gain / totals["invested"] * 100, 2) if totals["invested"] else None,
        "realised": round(totals["realised"], 2),
        "day_change": round(totals["day_change"], 2),
        "day_change_pct": round(totals["day_change"] / prev_value * 100, 2) if prev_value else None,
        "xirr": round(overall_xirr * 100, 2) if overall_xirr is not None else None,
        "as_of": latest_date,
        "allocation": alloc,
        "target": target,
        "drift": drift,
        "max_drift": max((abs(v) for v in drift.values()), default=0.0),
        "has_transactions": bool(txns),
    }


def allocation(rows: list[dict]) -> dict:
    total = sum(r["value"] for r in rows)
    out = {k: 0.0 for k in ASSET_CLASSES}
    if not total:
        return out
    for r in rows:
        out[r["asset_class"]] = out.get(r["asset_class"], 0.0) + r["value"]
    return {k: round(v / total * 100, 2) for k, v in out.items()}


def target_allocation(user_id: int) -> dict:
    row = db.one("SELECT target_allocation FROM settings WHERE user_id = %s", [user_id])
    return (row or {}).get("target_allocation") or {"equity": 70, "debt": 20, "international": 10}


def value_on(user_id: int, d: date, funds: list[dict], txns_by_fund: dict, series_by_code: dict) -> dict:
    """Units, invested and value of each fund on a past date (for snapshots and charts)."""
    out = {}
    for f in funds:
        txns = [t for t in txns_by_fund.get(f["id"], []) if t["txn_date"] <= d]
        h = fifo(txns)
        point = returns.value_asof(series_by_code.get(f["scheme_code"], []), d)
        nav = point[1] if point else None
        out[f["id"]] = {"units": h["units"], "invested": h["invested"], "value": round(h["units"] * nav, 2) if nav else 0.0}
    return out


def timeseries(user_id: int, include_simulated: bool = False, step_days: int = 7) -> list[dict]:
    """Invested vs value over time, reconstructed from transactions x NAV history (weekly points + today)."""
    funds = db.query("SELECT * FROM funds WHERE user_id = %s ORDER BY id", [user_id])
    txns = load_transactions(user_id, include_simulated=include_simulated)
    if not txns:
        return []
    by_fund: dict[int, list[dict]] = defaultdict(list)
    for t in txns:
        by_fund[t["fund_id"]].append(t)
    start = min(t["txn_date"] for t in txns)
    series = mfapi.series_many([f["scheme_code"] for f in funds], start - timedelta(days=7))
    end = max((s[-1][0] for s in series.values() if s), default=date.today())
    points = []
    d = start
    while d <= end:
        v = value_on(user_id, d, funds, by_fund, series)
        points.append({"date": d.isoformat(), "invested": round(sum(x["invested"] for x in v.values()), 2),
                       "value": round(sum(x["value"] for x in v.values()), 2)})
        d += timedelta(days=step_days)
    if not points or points[-1]["date"] != end.isoformat():
        v = value_on(user_id, end, funds, by_fund, series)
        points.append({"date": end.isoformat(), "invested": round(sum(x["invested"] for x in v.values()), 2),
                       "value": round(sum(x["value"] for x in v.values()), 2)})
    return points


def next_sips(user_id: int, today: date | None = None) -> list[dict]:
    """Upcoming SIP dates for each fund with a SIP set, soonest first."""
    today = today or date.today()
    out = []
    for f in active_funds(user_id):
        if not f["sip_amount"] or not f["sip_day"]:
            continue
        day = int(f["sip_day"])
        candidate = date(today.year, today.month, min(day, 28))
        if candidate < today:
            y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            candidate = date(y, m, min(day, 28))
        out.append({"fund_id": f["id"], "name": f["short_name"] or f["scheme_name"], "amount": float(f["sip_amount"]),
                    "date": candidate, "days": (candidate - today).days, "colour": f["colour"]})
    return sorted(out, key=lambda x: x["days"])
