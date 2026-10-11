"""Flow 5 — portfolio maths for the daily visit, plus Compare and Trends data."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, request

from .. import db
from ..services import mfapi, portfolio, returns
from ._util import fail, ok, parse_date, user_id

bp = Blueprint("api_portfolio", __name__, url_prefix="/api")


def _summary_json(s: dict) -> dict:
    s = dict(s)
    s["funds"] = [{k: v for k, v in f.items() if k != "fund"} for f in s["funds"]]
    return s


@bp.get("/portfolio/summary")
def summary():
    include_sim = request.args.get("include_simulated") == "1"
    return ok(_summary_json(portfolio.summary(user_id(), include_simulated=include_sim)))


@bp.get("/portfolio/timeseries")
def timeseries():
    include_sim = request.args.get("include_simulated") == "1"
    return ok(portfolio.timeseries(user_id(), include_simulated=include_sim))


def compare_data(codes: list[int], start: date | None) -> dict:
    series = mfapi.series_many(codes)
    names = {}
    for c in codes:
        if not series.get(c):
            try:
                info = mfapi.backfill(c)
                series[c] = mfapi.series(c)
                names[c] = info["meta"].get("scheme_name")
            except Exception:  # noqa: BLE001
                series[c] = []
    for r in db.query("SELECT scheme_code, COALESCE(short_name, scheme_name) AS name, colour, scheme_category, benchmark_key "
                      "FROM funds WHERE user_id = %s AND scheme_code = ANY(%s) ORDER BY is_active DESC", [user_id(), codes]):
        names.setdefault(r["scheme_code"], r["name"])
    for c in codes:
        if c not in names:
            row = db.one("SELECT scheme_name FROM category_peers WHERE scheme_code = %s LIMIT 1", [c])
            names[c] = row["scheme_name"] if row else f"Scheme {c}"

    available = [s for s in series.values() if s]
    if not available:
        return {"funds": [], "start": None}
    common_start = max(s[0][0] for s in available)
    start = max(start or (date.today() - timedelta(days=366 * 3)), common_start)
    out = []
    for c in codes:
        s = series.get(c) or []
        if not s:
            out.append({"scheme_code": c, "name": names.get(c), "error": "No data"})
            continue
        window = [p for p in s if p[0] >= start]
        growth = returns.rebase(s, start, base=10000.0)
        monthly = returns.monthly_returns(window)
        r = returns.compute(s)
        thin = growth[::2]
        if growth and thin[-1] != growth[-1]:
            thin.append(growth[-1])
        out.append({
            "scheme_code": c, "name": names.get(c),
            "growth": [[d.isoformat(), v] for d, v in thin],
            "final_value": growth[-1][1] if growth else None,
            "returns": {k: r[k] for k in returns.PERIODS},
            "volatility_1y": returns.annualised_volatility(s),
            "max_drawdown": returns.max_drawdown(window),
            "best_month": max(monthly, key=lambda x: x[1]) if monthly else None,
            "worst_month": min(monthly, key=lambda x: x[1]) if monthly else None,
            "rolling_1y": [[d.isoformat(), v] for d, v in returns.rolling_returns(s, 1, 14) if d >= start - timedelta(days=366 * 2)],
        })
    corr = []
    for i, a in enumerate(codes):
        row = []
        for j, b in enumerate(codes):
            row.append(1.0 if i == j else returns.correlation(series.get(a) or [], series.get(b) or [], date.today() - timedelta(days=366)))
        corr.append(row)
    valid = [f for f in out if "error" not in f and f["returns"]["ret_1y"] is not None]
    summary = ""
    if len(valid) >= 2:
        best = max(valid, key=lambda f: f["returns"]["ret_1y"])
        calm = min((f for f in valid if f["volatility_1y"] is not None), key=lambda f: f["volatility_1y"], default=None)
        summary = f"Over 1 year, {best['name']} grew the most ({best['returns']['ret_1y']:+.2f}%)."
        if calm:
            summary += f" {calm['name']} moved the least (volatility {calm['volatility_1y']:.2f}% a year)."
    return {"funds": out, "start": start.isoformat(), "correlation": corr, "summary": summary}


@bp.get("/compare")
def compare():
    raw = request.args.get("codes", "")
    try:
        codes = [int(c) for c in raw.split(",") if c.strip()][:5]
    except ValueError:
        return fail("codes must be scheme codes separated by commas")
    if len(codes) < 2:
        return fail("Pick at least 2 funds to compare.")
    start = None
    if request.args.get("from"):
        try:
            start = parse_date(request.args["from"], "from")
        except ValueError as exc:
            return fail(str(exc))
    return ok(compare_data(codes, start))


def trends_data(uid: int, weeks: int = 26) -> dict:
    rows = db.query("""SELECT s.week_ending, s.fund_id, COALESCE(f.short_name, f.scheme_name) AS name, f.colour, f.asset_class,
                              s.ret_1w, s.bench_ret_1w, s.cat_ret_1w, s.delta_bench_1w, s.delta_cat_1w, s.attribution, s.value
                       FROM weekly_snapshots s JOIN funds f ON f.id = s.fund_id
                       WHERE s.user_id = %s AND f.is_active AND s.week_ending > (SELECT MAX(week_ending) FROM weekly_snapshots WHERE user_id = %s) - make_interval(weeks => %s)
                       ORDER BY s.week_ending, f.id""", [uid, uid, weeks])
    weeks_list = sorted({r["week_ending"] for r in rows})
    funds = {}
    for r in rows:
        f = funds.setdefault(r["fund_id"], {"fund_id": r["fund_id"], "name": r["name"], "colour": r["colour"],
                                            "asset_class": r["asset_class"], "weeks": {}})
        gap = r["delta_bench_1w"] if r["delta_bench_1w"] is not None else r["delta_cat_1w"]
        f["weeks"][r["week_ending"].isoformat()] = {"gap": gap, "ret": r["ret_1w"], "ref": r["bench_ret_1w"] if r["bench_ret_1w"] is not None else r["cat_ret_1w"],
                                                    "attribution": r["attribution"], "value": r["value"]}
    for f in funds.values():
        cum, line = 0.0, []
        mix = {"market_wide": 0, "fund_specific": 0, "mixed": 0, "insufficient_data": 0}
        for w in weeks_list:
            cell = f["weeks"].get(w.isoformat())
            if cell and cell["gap"] is not None:
                cum += cell["gap"]
            line.append(round(cum, 3))
            if cell:
                mix[cell["attribution"] or "insufficient_data"] = mix.get(cell["attribution"] or "insufficient_data", 0) + 1
        f["cumulative_gap"] = line
        f["attribution_mix"] = mix
    value_by_week = []
    for w in weeks_list:
        value_by_week.append(round(sum((f["weeks"].get(w.isoformat()) or {}).get("value") or 0 for f in funds.values()), 2))
    verdicts = {r["week_ending"].isoformat(): r["verdict"] for r in db.query(
        "SELECT week_ending, verdict FROM reports WHERE user_id = %s", [uid])}
    return {"weeks": [w.isoformat() for w in weeks_list], "funds": list(funds.values()),
            "portfolio_value": value_by_week, "verdicts": verdicts}


@bp.get("/trends")
def trends():
    weeks = min(max(int(request.args.get("weeks", 26) or 26), 4), 52)
    return ok(trends_data(user_id(), weeks))
