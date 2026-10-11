"""Flow 6 — logging a buy, sell or SIP. Mausam records only; it never moves money."""
from __future__ import annotations

import csv
import io
from datetime import date

from flask import Blueprint, Response, request

from .. import db
from ..services import portfolio
from ._util import body, fail, handles_value_errors, ok, parse_date, user_id

bp = Blueprint("api_transactions", __name__, url_prefix="/api/transactions")

TYPES = ("sip", "buy", "sell")


def _fund(fund_id) -> dict:
    try:
        fund_id = int(fund_id)
    except (TypeError, ValueError):
        raise ValueError("Pick a fund.")
    f = db.one("SELECT * FROM funds WHERE id = %s AND user_id = %s", [fund_id, user_id()])
    if not f:
        raise ValueError("That fund isn't in your list.")
    return f


def prepare(b: dict, existing_id: int | None = None) -> dict:
    """Validate a transaction and work out the NAV and units. Used for preview and save."""
    f = _fund(b.get("fund_id"))
    ttype = (b.get("txn_type") or "").lower()
    if ttype not in TYPES:
        raise ValueError("Type must be SIP, buy or sell.")
    d = parse_date(b.get("txn_date"), "Date")
    if d > date.today():
        raise ValueError("The date can't be in the future. Log transactions after they happen.")
    nav = portfolio.nav_for_date(f["scheme_code"], d)
    if not nav:
        raise ValueError("We don't have NAV data for this fund yet. Try again after the next refresh.")
    amount = b.get("amount")
    units = b.get("units")
    if ttype == "sell" and units not in (None, ""):
        units = float(units)
        if units <= 0:
            raise ValueError("Units to sell must be more than zero.")
        amount = round(units * nav["nav"], 2)
    else:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise ValueError("Enter an amount in rupees.")
        if amount <= 0:
            raise ValueError("Amount must be more than zero.")
        if amount > 1e8:
            raise ValueError("That amount looks too large. Check for extra zeros.")
        units = round(amount / nav["nav"], 4)
    simulated = bool(b.get("is_simulated"))
    held_now = portfolio.units_held(user_id(), f["id"])
    held_then = portfolio.units_held(user_id(), f["id"], up_to=d)
    if existing_id:
        old = db.one("SELECT * FROM transactions WHERE id = %s", [existing_id])
        if old and not old["is_simulated"]:
            sign = -1 if old["txn_type"] == "sell" else 1
            held_now -= sign * float(old["units"])
            if old["txn_date"] <= d:
                held_then -= sign * float(old["units"])
    if ttype == "sell" and not simulated and units > min(held_now, held_then) + 1e-6:
        raise ValueError(f"You can't sell more than you hold. You held {min(held_now, held_then):,.4f} units on that date.")
    note_parts = []
    if nav["shifted"] and not nav["provisional"]:
        note_parts.append(f"{d.strftime('%d %b')} wasn't a business day, so we used the NAV of {nav['nav_date'].strftime('%d %b %Y')}.")
    if nav["provisional"]:
        note_parts.append("Today's NAV isn't published yet, so we used the latest available NAV. It will be close but not exact.")
    return {"fund_id": f["id"], "fund_name": f["short_name"] or f["scheme_name"], "txn_type": ttype, "txn_date": d,
            "amount": round(amount, 2), "nav": nav["nav"], "nav_date": nav["nav_date"], "units": units,
            "is_simulated": simulated, "note": (b.get("note") or "").strip()[:300] or None,
            "nav_note": " ".join(note_parts), "units_held_after": round(held_now + (-units if ttype == "sell" else units), 4)}


@bp.get("")
def list_txns():
    sql = """SELECT t.*, COALESCE(f.short_name, f.scheme_name) AS fund_name, f.colour, f.is_active AS fund_active
             FROM transactions t JOIN funds f ON f.id = t.fund_id WHERE t.user_id = %s"""
    params: list = [user_id()]
    if request.args.get("fund_id"):
        sql += " AND t.fund_id = %s"
        params.append(int(request.args["fund_id"]))
    if request.args.get("type") in TYPES:
        sql += " AND t.txn_type = %s"
        params.append(request.args["type"])
    for arg, op in (("from", ">="), ("to", "<=")):
        if request.args.get(arg):
            try:
                params.append(parse_date(request.args[arg], arg))
            except ValueError as exc:
                return fail(str(exc))
            sql += f" AND t.txn_date {op} %s"
    if request.args.get("simulated") == "only":
        sql += " AND t.is_simulated"
    elif request.args.get("simulated") != "include":
        sql += " AND NOT t.is_simulated"
    return ok(db.query(sql + " ORDER BY t.txn_date DESC, t.id DESC LIMIT 1000", params))


@bp.post("")
@handles_value_errors
def create():
    b = body()
    t = prepare(b)
    if b.get("dry_run"):
        return ok(t)
    row = db.one("""INSERT INTO transactions (user_id, fund_id, txn_type, txn_date, amount, nav, units, is_simulated, note)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                 [user_id(), t["fund_id"], t["txn_type"], t["txn_date"], t["amount"], t["nav"], t["units"],
                  t["is_simulated"], t["note"]])
    return ok({**row, "nav_note": t["nav_note"]}, 201)


@bp.patch("/<int:txn_id>")
@handles_value_errors
def update(txn_id: int):
    old = db.one("SELECT * FROM transactions WHERE id = %s AND user_id = %s", [txn_id, user_id()])
    if not old:
        return fail("Transaction not found.", 404)
    merged = {"fund_id": old["fund_id"], "txn_type": old["txn_type"], "txn_date": old["txn_date"].isoformat(),
              "amount": old["amount"], "is_simulated": old["is_simulated"], "note": old["note"]}
    merged.update(body())
    t = prepare(merged, existing_id=txn_id)
    row = db.one("""UPDATE transactions SET fund_id=%s, txn_type=%s, txn_date=%s, amount=%s, nav=%s, units=%s,
                    is_simulated=%s, note=%s WHERE id = %s RETURNING *""",
                 [t["fund_id"], t["txn_type"], t["txn_date"], t["amount"], t["nav"], t["units"], t["is_simulated"],
                  t["note"], txn_id])
    return ok(row)


@bp.delete("/<int:txn_id>")
def delete(txn_id: int):
    old = db.one("SELECT * FROM transactions WHERE id = %s AND user_id = %s", [txn_id, user_id()])
    if not old:
        return fail("Transaction not found.", 404)
    if old["txn_type"] != "sell" and not old["is_simulated"]:
        held = portfolio.units_held(user_id(), old["fund_id"])
        if held - float(old["units"]) < -1e-6:
            return fail("Deleting this would leave you with negative units, because later sells used these units. Delete the sell first.")
    db.execute("DELETE FROM transactions WHERE id = %s", [txn_id])
    return ok({"deleted": txn_id})


@bp.get("/export.csv")
def export_csv():
    rows = db.query("""SELECT t.txn_date, COALESCE(f.short_name, f.scheme_name) AS fund, f.scheme_code, t.txn_type, t.amount,
                              t.nav, t.units, t.is_simulated, t.note
                       FROM transactions t JOIN funds f ON f.id = t.fund_id WHERE t.user_id = %s ORDER BY t.txn_date, t.id""",
                    [user_id()])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "fund", "scheme_code", "type", "amount_inr", "nav", "units", "simulated", "note"])
    for r in rows:
        w.writerow([r["txn_date"], r["fund"], r["scheme_code"], r["txn_type"], r["amount"], r["nav"], r["units"],
                    r["is_simulated"], r["note"] or ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=mausam-transactions.csv"})


@bp.post("/simulate")
@handles_value_errors
def simulate():
    """'What if I had invested ₹X extra in fund Y on date Z?' — computed, not saved (unless save=true)."""
    b = body()
    b["txn_type"] = "buy"
    b["is_simulated"] = True
    t = prepare(b)
    f = _fund(b.get("fund_id"))
    latest = portfolio.latest_navs([f["scheme_code"]]).get(f["scheme_code"]) or {}
    if not latest.get("nav"):
        return fail("No latest NAV for this fund.")
    value_today = round(t["units"] * latest["nav"], 2)
    gain = round(value_today - t["amount"], 2)
    days = (latest["nav_date"] - t["nav_date"]).days
    from ..services.xirr import xirr

    rate = xirr([(t["nav_date"], -t["amount"]), (latest["nav_date"], value_today)]) if days > 0 else None
    result = {**t, "value_today": value_today, "gain": gain, "gain_pct": round(gain / t["amount"] * 100, 2),
              "latest_nav": latest["nav"], "latest_nav_date": latest["nav_date"],
              "yearly_return": round(rate * 100, 2) if rate is not None and days >= 365 else None, "saved": False}
    if b.get("save"):
        row = db.one("""INSERT INTO transactions (user_id, fund_id, txn_type, txn_date, amount, nav, units, is_simulated, note)
                        VALUES (%s,%s,'buy',%s,%s,%s,%s,true,%s) RETURNING id""",
                     [user_id(), t["fund_id"], t["txn_date"], t["amount"], t["nav"], t["units"], t["note"] or "What-if simulation"])
        result["saved"] = True
        result["id"] = row["id"]
    return ok(result)
