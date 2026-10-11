"""Add, preview, edit and remove funds (Manage funds page). No code changes needed for a new fund."""
from __future__ import annotations

from datetime import date

from .. import db
from . import category, mfapi, snapshots
from .benchmarks import BENCHMARK_LABELS, SELECTABLE, classify

# Fixed, accessible series palette. Each fund keeps its colour on every chart.
PALETTE = ["#0E7C7B", "#D9822B", "#4C5FD5", "#8E4585", "#2A9DCC", "#6E8B3D", "#D1495B", "#5C6B80"]


class FundError(ValueError):
    pass


def friendly_name(scheme_name: str, info: dict) -> str:
    group = info["peer_group"]
    if group.startswith("Index funds · "):
        return f"{group.split('· ', 1)[1]} index fund"
    return {"Mid cap fund": "Mid-cap fund", "Small cap fund": "Small-cap fund", "Large cap fund": "Large-cap fund",
            "Short duration fund": "Short-duration debt fund", "International fund of funds": "International fund"}.get(
        group, scheme_name.split(" - ")[0][:40])


def preview(code: int, user_id: int | None = None) -> dict:
    """Look a scheme code up on mfapi and describe what Mausam will do with it."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        raise FundError("A scheme code is a number, usually 6 digits (for example 120716).")
    try:
        lat = mfapi.latest(code)
    except Exception:
        raise FundError(f"We couldn't find scheme code {code} on mfapi.in, or it has no NAV data. Check the code and try again.")
    meta = lat["meta"]
    info = classify(meta.get("scheme_category"), meta.get("scheme_name"))
    duplicate = False
    if user_id:
        duplicate = bool(db.scalar("SELECT 1 FROM funds WHERE user_id = %s AND scheme_code = %s AND is_active", [user_id, code]))
    name = meta.get("scheme_name", "")
    warnings = []
    if "direct" not in name.lower():
        warnings.append("This looks like a Regular plan. Direct plans have lower fees; check you picked the right version.")
    if "growth" not in name.lower():
        warnings.append("This doesn't look like a Growth option. Mausam works best with Growth versions.")
    if (date.today() - lat["nav_date"]).days > 10:
        warnings.append(f"Its latest NAV is from {lat['nav_date'].strftime('%d %b %Y')}; the fund may be closed.")
    return {
        "scheme_code": code, "scheme_name": name, "fund_house": meta.get("fund_house"),
        "scheme_category": meta.get("scheme_category"), "nav": lat["nav"], "nav_date": lat["nav_date"].isoformat(),
        "asset_class": info["asset_class"], "benchmark_key": info["benchmark_key"],
        "benchmark_label": BENCHMARK_LABELS.get(info["benchmark_key"]), "peer_group": info["peer_group"],
        "benchmark_why": info["why"], "needs_confirmation": info["needs_confirmation"],
        "suggested_name": friendly_name(name, info), "duplicate": duplicate, "warnings": warnings,
    }


def next_colour(user_id: int) -> str:
    used = [r["colour"] for r in db.query("SELECT colour FROM funds WHERE user_id = %s AND is_active", [user_id])]
    for c in PALETTE:
        if c not in used:
            return c
    return PALETTE[len(used) % len(PALETTE)]


def add(user_id: int, code: int, short_name: str | None = None, sip_amount: float | None = None,
        sip_day: int | None = None, benchmark_key: str | None = None, with_peers: bool = True,
        rebuild_weeks: int = 26) -> dict:
    p = preview(code, user_id)
    if p["duplicate"]:
        raise FundError("This fund is already in your list.")
    if sip_day is not None and not 1 <= int(sip_day) <= 28:
        raise FundError("SIP day must be between 1 and 28.")
    if sip_amount is not None and float(sip_amount) < 0:
        raise FundError("SIP amount can't be negative.")
    override = bool(benchmark_key and benchmark_key != p["benchmark_key"])
    if benchmark_key and benchmark_key not in SELECTABLE:
        raise FundError("Unknown benchmark.")
    bkey = benchmark_key or p["benchmark_key"]

    backfill = mfapi.backfill(code)
    row = db.one(
        """INSERT INTO funds (user_id, scheme_code, scheme_name, short_name, fund_house, scheme_category, peer_group,
                              asset_class, benchmark_key, benchmark_is_override, sip_amount, sip_day, colour, is_active,
                              removed_at, added_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,NULL,now())
           ON CONFLICT (user_id, scheme_code) DO UPDATE SET is_active = true, removed_at = NULL,
             short_name = EXCLUDED.short_name, sip_amount = EXCLUDED.sip_amount, sip_day = EXCLUDED.sip_day,
             benchmark_key = EXCLUDED.benchmark_key, benchmark_is_override = EXCLUDED.benchmark_is_override,
             peer_group = EXCLUDED.peer_group, colour = EXCLUDED.colour
           RETURNING *""",
        [user_id, code, p["scheme_name"], (short_name or p["suggested_name"]).strip()[:60], p["fund_house"],
         p["scheme_category"], p["peer_group"], p["asset_class"], bkey, override,
         sip_amount, sip_day, next_colour(user_id)],
    )
    peers_info = None
    if with_peers and len(category.peers(p["peer_group"])) < 3:
        own = {r["scheme_code"] for r in db.query("SELECT scheme_code FROM funds")}
        found = category.discover_peers(p["peer_group"], exclude=own, max_checks=20)
        if found:
            category.save_peers(p["peer_group"], found)
        peers_info = len(found)
    rebuilt = snapshots.rebuild(user_id, rebuild_weeks, funds=[row]) if rebuild_weeks else 0
    return {"fund": row, "nav_rows": backfill["rows"], "peers_found": peers_info, "snapshots": rebuilt}


def update(user_id: int, fund_id: int, changes: dict) -> dict:
    fund = db.one("SELECT * FROM funds WHERE id = %s AND user_id = %s", [fund_id, user_id])
    if not fund:
        raise FundError("Fund not found.")
    sets, params = [], []
    if "short_name" in changes:
        name = (changes["short_name"] or "").strip()
        if not name:
            raise FundError("Give the fund a short friendly name.")
        sets.append("short_name = %s"); params.append(name[:60])
    if "benchmark_key" in changes:
        key = changes["benchmark_key"]
        if key not in SELECTABLE:
            raise FundError("Unknown benchmark.")
        default = classify(fund["scheme_category"], fund["scheme_name"])["benchmark_key"]
        sets.append("benchmark_key = %s"); params.append(key)
        sets.append("benchmark_is_override = %s"); params.append(key != default)
    if "sip_amount" in changes:
        amt = changes["sip_amount"]
        amt = None if amt in (None, "") else float(amt)
        if amt is not None and amt < 0:
            raise FundError("SIP amount can't be negative.")
        sets.append("sip_amount = %s"); params.append(amt)
    if "sip_day" in changes:
        day = changes["sip_day"]
        day = None if day in (None, "") else int(day)
        if day is not None and not 1 <= day <= 28:
            raise FundError("SIP day must be between 1 and 28.")
        sets.append("sip_day = %s"); params.append(day)
    if not sets:
        return fund
    row = db.one(f"UPDATE funds SET {', '.join(sets)} WHERE id = %s RETURNING *", params + [fund_id])
    if "benchmark_key" in changes:
        snapshots.rebuild(user_id, 26, funds=[row])
    return row


def remove(user_id: int, fund_id: int) -> dict:
    """Soft delete: the fund disappears from pages and reports, but its history and transactions are kept."""
    row = db.one("UPDATE funds SET is_active = false, removed_at = now() WHERE id = %s AND user_id = %s RETURNING *",
                 [fund_id, user_id])
    if not row:
        raise FundError("Fund not found.")
    return row


def get_by_code(user_id: int, code: int) -> dict | None:
    return db.one("SELECT * FROM funds WHERE user_id = %s AND scheme_code = %s ORDER BY is_active DESC LIMIT 1", [user_id, code])


def list_funds(user_id: int, include_removed: bool = False) -> list[dict]:
    sql = "SELECT * FROM funds WHERE user_id = %s"
    if not include_removed:
        sql += " AND is_active"
    return db.query(sql + " ORDER BY is_active DESC, id", [user_id])
