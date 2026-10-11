"""Flow 4 — add or remove a fund by scheme code (no code changes), plus NAV/returns for any fund."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, request

from ..services import benchmarks, category, funds, indices, mfapi, returns
from ._util import body, fail, handles_value_errors, ok, user_id

bp = Blueprint("api_funds", __name__, url_prefix="/api/funds")

RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "3Y": 3 * 366, "5Y": 5 * 366, "ALL": None}


@bp.get("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return ok([])
    if q.isdigit():
        try:
            p = funds.preview(int(q))
            return ok([{"scheme_code": p["scheme_code"], "scheme_name": p["scheme_name"]}])
        except funds.FundError:
            return ok([])
    try:
        rows = mfapi.search(q)
    except Exception:  # noqa: BLE001
        return fail("mfapi.in search is not responding right now. Try again in a minute, or enter the scheme code.", 502)
    # Show direct-growth plans first, the recommended version.
    rows.sort(key=lambda r: (0 if ("direct" in r["scheme_name"].lower() and "growth" in r["scheme_name"].lower()) else 1,
                             r["scheme_name"]))
    return ok(rows[:25])


@bp.get("/preview/<int:code>")
def preview(code: int):
    try:
        return ok(funds.preview(code, user_id()))
    except funds.FundError as exc:
        return fail(str(exc), 404)


@bp.post("")
@handles_value_errors
def add():
    b = body()
    code = b.get("scheme_code")
    if not code:
        return fail("Enter a scheme code.")
    sip_amount = b.get("sip_amount")
    sip_day = b.get("sip_day")
    try:
        result = funds.add(
            user_id(), int(code), short_name=b.get("short_name"),
            sip_amount=float(sip_amount) if sip_amount not in (None, "") else None,
            sip_day=int(sip_day) if sip_day not in (None, "") else None,
            benchmark_key=b.get("benchmark_key") or None,
        )
    except funds.FundError as exc:
        return fail(str(exc))
    f = result["fund"]
    return ok({"fund_id": f["id"], "scheme_code": f["scheme_code"], "short_name": f["short_name"],
               "nav_rows": result["nav_rows"], "snapshots": result["snapshots"], "peers_found": result["peers_found"]}, 201)


@bp.patch("/<int:fund_id>")
@handles_value_errors
def update(fund_id: int):
    try:
        row = funds.update(user_id(), fund_id, body())
    except funds.FundError as exc:
        return fail(str(exc))
    return ok({"fund_id": row["id"], "short_name": row["short_name"], "benchmark_key": row["benchmark_key"]})


@bp.delete("/<int:fund_id>")
def remove(fund_id: int):
    try:
        row = funds.remove(user_id(), fund_id)
    except funds.FundError as exc:
        return fail(str(exc), 404)
    return ok({"fund_id": row["id"], "removed": True})


@bp.get("/<int:code>/nav")
def nav(code: int):
    rng = (request.args.get("range") or "1Y").upper()
    if rng not in RANGES:
        return fail("range must be one of " + ", ".join(RANGES))
    days = RANGES[rng]
    series = mfapi.series(code)
    if not series:
        try:
            mfapi.backfill(code)
            series = mfapi.series(code)
        except Exception:  # noqa: BLE001
            return fail("No NAV history for this scheme code.", 404)
    end = series[-1][0]
    start = (end - timedelta(days=days)) if days else series[0][0]
    window = [p for p in series if p[0] >= start]
    data = {"scheme_code": code, "range": rng, "nav": [[d.isoformat(), v] for d, v in window]}

    fund = funds.get_by_code(user_id(), code)
    if fund:
        if fund["benchmark_key"] and fund["benchmark_key"] != "CATEGORY_ONLY":
            b = indices.benchmark_series(fund["benchmark_key"], start)
            rebased = returns.rebase(b["rows"], start, base=window[0][1] if window else 100)
            data["benchmark"] = {"label": benchmarks.BENCHMARK_LABELS.get(fund["benchmark_key"]), "source": b["source"],
                                 "points": [[d.isoformat(), v] for d, v in rebased if d <= end]}
        if fund["peer_group"]:
            cs = category.category_series(fund["peer_group"], start)
            if cs and window:
                base = window[0][1] / 100.0
                data["category"] = {"label": "Category average", "peer_count": len(category.peers(fund["peer_group"])),
                                    "points": [[d.isoformat(), round(v * base, 4)] for d, v in cs if d <= end]}
    return ok(data)


@bp.get("/<int:code>/returns")
def fund_returns(code: int):
    series = mfapi.series(code)
    if not series:
        return fail("No NAV history for this scheme code.", 404)
    r = returns.compute(series)
    out = {"fund": r}
    fund = funds.get_by_code(user_id(), code)
    if fund:
        if fund["benchmark_key"] and fund["benchmark_key"] != "CATEGORY_ONLY":
            b = indices.benchmark_series(fund["benchmark_key"], date.today() - timedelta(days=3 * 366 + 30))
            out["benchmark"] = returns.compute(b["rows"], r["as_of"]) | {"source": b["source"]}
        if fund["peer_group"]:
            out["category"] = category.stats_for(fund["peer_group"], r["as_of"])
    return ok(out)
