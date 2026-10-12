"""All HTML pages (Flow 5: Meera's daily visit). Each view gathers data with the services and renders a template."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, abort, render_template, request

from .. import db
from ..config import config
from ..services import (alerts, assistant, benchmarks, category, indices, mfapi, news, portfolio, returns,
                        snapshots)
from ..services import report as report_service
from ..services.verdict import weather
from .api_forecasts import forecast_bundle
from .api_market import index_cards
from .api_portfolio import trends_data
from ._util import user_id

bp = Blueprint("pages", __name__)


def _latest_snapshots(uid: int) -> dict[int, dict]:
    week = snapshots.latest_week(uid)
    return {s["fund_id"]: s for s in snapshots.for_week(uid, week)} if week else {}


def _sparklines(codes: list[int], days: int = 30) -> dict[int, list]:
    start = date.today() - timedelta(days=days + 5)
    series = mfapi.series_many(codes, start)
    return {c: [[d.isoformat(), v] for d, v in rows] for c, rows in series.items()}


# ---------- Page 1: Today ----------
@bp.get("/")
def today():
    uid = user_id()
    summ = portfolio.summary(uid)
    rep = report_service.latest(uid)
    snaps = _latest_snapshots(uid)
    # Today is about THIS WEEK only; all-time totals live on My portfolio.
    sparks = _sparklines([f["scheme_code"] for f in summ["funds"]], days=14)
    tiles, week_change, value_week_ago = [], 0.0, 0.0
    for f in summ["funds"]:
        s = snaps.get(f["fund_id"]) or {}
        gap = s.get("delta_bench_1w") if s.get("delta_bench_1w") is not None else s.get("delta_cat_1w")
        rows = sparks.get(f["scheme_code"]) or []
        # Rupee change over the last 7 days for the units she holds: units x (NAV now - NAV a week ago).
        series = [(date.fromisoformat(d), v) for d, v in rows]
        then = returns.value_asof(series, f["nav_date"] - timedelta(days=7)) if f["nav_date"] and series else None
        change = round(f["units"] * (f["nav"] - then[1]), 2) if then and f["nav"] else None
        if change is not None:
            week_change += change
            value_week_ago += f["units"] * then[1]
        tiles.append({**f, "ret_1w": s.get("ret_1w"), "gap": gap,
                      "gap_ref": "benchmark" if s.get("delta_bench_1w") is not None else "category",
                      "attribution": s.get("attribution"), "stale": returns.is_stale(f["nav_date"], date.today()),
                      "week_change": change, "spark": rows})
    with_gap = [t for t in tiles if t["gap"] is not None]
    week = {
        "change": round(week_change, 2) if value_week_ago else None,
        "pct": round(week_change / value_week_ago * 100, 2) if value_week_ago else None,
        "kept_up": sum(1 for t in with_gap if t["gap"] >= -0.25),
        "compared": len(with_gap),
        "market_wide": sum(1 for t in tiles if t["attribution"] == "market_wide"),
        "fund_specific": sum(1 for t in tiles if t["attribution"] == "fund_specific"),
        "best": max(tiles, key=lambda t: t["ret_1w"] if t["ret_1w"] is not None else -999) if tiles else None,
    }
    cards = {c["key"]: c for c in index_cards()}
    strip = [cards.get(k) for k in ("NIFTY50", "MIDCAP150", "SMALLCAP100", "SP500") if cards.get(k)]
    return render_template(
        "today.html", summary=summ, report=rep,
        tip=assistant.latest_tip(), tiles=tiles, week=week, sips=portfolio.next_sips(uid), strip=strip,
        news=news.latest(3), alerts=alerts.recent(uid, limit=3, unread_only=True),
    )


# ---------- Page 2: Portfolio ----------
@bp.get("/portfolio")
def portfolio_page():
    uid = user_id()
    summ = portfolio.summary(uid)
    return render_template("portfolio.html", summary=summ, timeseries=portfolio.timeseries(uid))


# ---------- Page 3: Fund detail ----------
@bp.get("/funds/<int:code>")
def fund_detail(code: int):
    uid = user_id()
    fund = db.one("SELECT * FROM funds WHERE user_id = %s AND scheme_code = %s ORDER BY is_active DESC LIMIT 1", [uid, code])
    series = mfapi.series(code)
    meta = None
    if not series:
        try:
            info = mfapi.backfill(code)
            meta = info["meta"]
            series = mfapi.series(code)
        except Exception:  # noqa: BLE001
            abort(404, description=f"We couldn't find scheme code {code}.")
    if not fund and meta is None:
        try:
            meta = mfapi.latest(code)["meta"]
        except Exception:  # noqa: BLE001
            meta = {"scheme_name": f"Scheme {code}"}
    info = benchmarks.classify((fund or meta or {}).get("scheme_category"), (fund or meta or {}).get("scheme_name"))
    bkey = fund["benchmark_key"] if fund else info["benchmark_key"]
    peer_group = fund["peer_group"] if fund else info["peer_group"]

    r = returns.compute(series)
    bench = indices.benchmark_series(bkey, date.today() - timedelta(days=3 * 366 + 40)) if bkey and bkey != "CATEGORY_ONLY" else {"rows": [], "source": None, "note": ""}
    br = returns.compute(bench["rows"], r["as_of"]) if bench["rows"] else {k: None for k in returns.PERIODS}
    cr = category.stats_for(peer_group, r["as_of"]) if peer_group and r["as_of"] else {k: None for k in returns.PERIODS} | {"peer_count": 0}
    labels = {"ret_1w": "1 week", "ret_1m": "1 month", "ret_ytd": "Year to date", "ret_1y": "1 year", "ret_3y_cagr": "3 years (yearly)"}
    returns_rows = [{"key": k, "label": labels[k], "fund": r[k], "bench": br.get(k), "cat": cr.get(k),
                     "vs_bench": returns.diff(r[k], br.get(k)), "vs_cat": returns.diff(r[k], cr.get(k))} for k in returns.PERIODS]

    holding, txns, gaps, summary_sentences, rep = None, [], [], [], report_service.latest(uid)
    if fund:
        summ = portfolio.summary(uid)
        holding = next((f for f in summ["funds"] if f["fund_id"] == fund["id"]), None)
        txns = portfolio.load_transactions(uid, fund_id=fund["id"], include_simulated=True)[::-1]
        hist = snapshots.history(uid, fund["id"], date.today(), limit=12)[::-1]
        gaps = [{"week": s["week_ending"].isoformat(),
                 "gap": s["delta_bench_1w"] if s["delta_bench_1w"] is not None else s["delta_cat_1w"],
                 "lagging": "lagging_benchmark" in (s["flags"] or []), "attribution": s["attribution"]} for s in hist]
        summary_sentences = report_service.fund_summary(rep, fund["short_name"] or fund["scheme_name"])

    streak = 0
    for g in reversed(gaps):
        if g["lagging"]:
            streak += 1
        else:
            break
    three_years = [p for p in series if p[0] >= series[-1][0] - timedelta(days=3 * 366)] if series else []
    dd = returns.drawdown(three_years)
    fc = forecast_bundle(code)
    return render_template(
        "fund.html", code=code, fund=fund, meta=meta, info=info, bkey=bkey, peer_group=peer_group,
        latest=r, returns_rows=returns_rows, bench=bench, peer_count=cr.get("peer_count", 0),
        holding=holding, txns=txns, gaps=gaps, streak=streak, summary_sentences=summary_sentences, report=rep,
        calendar_years=returns.calendar_year_returns(series)[-8:],
        drawdown=[[d.isoformat(), v] for d, v in dd[::3]], max_drawdown=returns.max_drawdown(three_years),
        vol_fund=returns.annualised_volatility(series), vol_bench=returns.annualised_volatility(bench["rows"]) if bench["rows"] else None,
        forecast=fc, stale=returns.is_stale(r["as_of"], date.today()) if r["as_of"] else True,
        benchmark_choices=benchmarks.SELECTABLE,
    )


# ---------- Page 4: Compare ----------
@bp.get("/compare")
def compare():
    uid = user_id()
    funds = portfolio.active_funds(uid)
    preselect = request.args.get("codes") or ",".join(str(f["scheme_code"]) for f in funds[:3])
    return render_template("compare.html", funds=funds, preselect=preselect)


# ---------- Page 5: Weekly reports ----------
@bp.get("/reports")
def reports():
    return render_template("reports.html", reports=report_service.all_reports(user_id()))


@bp.get("/reports/<week>")
def report_detail(week: str):
    try:
        d = date.fromisoformat(week)
    except ValueError:
        abort(404)
    rep = report_service.get(user_id(), d)
    if not rep:
        abort(404, description="There's no report for that week yet.")
    mc = db.one("SELECT * FROM market_context WHERE week_ending = %s", [d])
    news_why = ((rep["report_json"] or {}).get("_meta") or {}).get("news_why", {})
    ids = [n["id"] for n in rep["facts_json"].get("news", [])]
    news_rows = db.query("SELECT * FROM news_items WHERE id = ANY(%s) ORDER BY published_at DESC NULLS LAST", [ids]) if ids else []
    for n in news_rows:
        n["why_it_matters"] = news_why.get(str(n["id"])) or n.get("why_it_matters")
    outlook = ((rep["report_json"] or {}).get("_meta") or {}).get("outlook", [])
    return render_template("report.html", rep=rep, rj=rep["report_json"], facts=rep["facts_json"], news=news_rows,
                           market=mc, outlook=outlook, sky=weather(rep["verdict"]), week=d,
                           moves=(mc or {}).get("index_moves") or {}, macro=(mc or {}).get("macro_summary"))


# ---------- Page 6: Trends ----------
@bp.get("/trends")
def trends():
    return render_template("trends.html", trends=trends_data(user_id(), 26))


# ---------- Page 7: Market pulse ----------
@bp.get("/market")
def market():
    uid = user_id()
    mc = db.one("SELECT * FROM market_context ORDER BY week_ending DESC LIMIT 1")
    week = snapshots.latest_week(uid)
    return render_template("market.html", cards=index_cards(), market=mc, news=news.latest(12),
                           snaps=snapshots.for_week(uid, week) if week else [], week=week)


# ---------- Page 8: Forecasts ----------
@bp.get("/forecasts")
def forecasts():
    uid = user_id()
    funds = portfolio.active_funds(uid)
    code = request.args.get("code", type=int) or (funds[0]["scheme_code"] if funds else None)
    fund = next((f for f in funds if f["scheme_code"] == code), None)
    bundle = forecast_bundle(code) if code else None
    history = [[d.isoformat(), v] for d, v in mfapi.series(code, date.today() - timedelta(days=190))] if code else []
    units = portfolio.units_held(uid, fund["id"]) if fund else 0.0
    last_run = db.one("SELECT * FROM job_runs WHERE job = 'ml_forecast' ORDER BY started_at DESC LIMIT 1")
    return render_template("forecasts.html", funds=funds, code=code, fund=fund, bundle=bundle, history=history,
                           units=units, last_run=last_run, repo=config.GITHUB_REPO)


# ---------- Page 9: Transactions ----------
@bp.get("/transactions")
def transactions():
    uid = user_id()
    return render_template("transactions.html", funds=portfolio.active_funds(uid), sips=portfolio.next_sips(uid),
                           today=date.today())


# ---------- Page 10: Manage funds ----------
@bp.get("/manage")
def manage():
    uid = user_id()
    funds = db.query("SELECT * FROM funds WHERE user_id = %s ORDER BY is_active DESC, id", [uid])
    for f in funds:
        f["peer_count"] = len(category.peers(f["peer_group"])) if f["peer_group"] else 0
    return render_template("manage.html", funds=funds, benchmark_choices=benchmarks.SELECTABLE,
                           target=portfolio.target_allocation(uid))


# ---------- Page 11: Calculators ----------
@bp.get("/calculators")
def calculators():
    return render_template("calculators.html")


# ---------- Page 12: Ask Mausam ----------
@bp.get("/ask")
def ask():
    return render_template("ask.html", history=assistant.history(user_id()), starters=assistant.STARTERS)


# ---------- Page 14: Alerts ----------
@bp.get("/alerts")
def alerts_page():
    return render_template("alerts.html", alerts=alerts.recent(user_id(), limit=200))


# ---------- Page 15: Settings ----------
@bp.get("/settings")
def settings():
    from .api_settings import get_settings

    return render_template("settings.html", settings=get_settings(user_id()), config_status=config.status())
