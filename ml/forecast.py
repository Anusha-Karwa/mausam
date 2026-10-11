"""Flow 2: Friday night forecasts and category averages -- the forecasting step.

Runs in GitHub Actions (never on Vercel) after ml/backfill.py and ml/peers.py:

  1. Load every active fund's NAV history from nav_history (real NAVs only, never invented),
     plus its benchmark (Yahoo index or proxy fund) and category-average line.
  2. Skip funds with fewer than 300 NAVs ("not enough history").
  3. Choose the ARIMA order by AIC (ml/models.py).
  4. Walk-forward backtest vs the naive "stays the same" guess (ml/backtest.py): this
     also picks the hybrid weight and calibrates the interval width.
  5. This week's hybrid ARIMA + Ridge forecast for 7 and 30 trading days, with 80% and
     95% ranges and a 30-day daily path for the chart.
  6. Gemini Flash writes a 2-sentence plain-English explanation; guardrails check it
     (ranges only, no advice, must say "estimate, not a promise"); otherwise a fixed template.
  7. Save forecasts + forecast_metrics; log a job_runs row (job = 'ml_forecast').

The forecast is educational. It never feeds the weekly verdict.

Usage (from the repo root):  python ml/forecast.py [--codes 120716,118989]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

if __name__ == "__main__":  # allow `python ml/forecast.py` from the repo root
    _HERE = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _HERE]
    if str(_HERE.parent) not in sys.path:
        sys.path.insert(0, str(_HERE.parent))

import numpy as np
import pandas as pd

from app import db
from app.jobs import job_run, today_local
from app.services import category, gemini, guardrails, indices, mfapi
from ml import backtest, models
from ml.common import clean, num
from ml.features import build_features, make_target, to_series

MIN_OBS = 300            # fewer NAVs than this -> skip with "not enough history"
HISTORY_YEARS = 6        # how much NAV history to load (ARIMA uses the last ~3 years, Ridge up to ~5)
MIN_SIDE_SERIES = 100    # benchmark / category series shorter than this are ignored
PATH_DAYS = 30
MUST_INCLUDE = "estimate, not a promise"

FORECAST_SQL = """
INSERT INTO forecasts (scheme_code, run_date, horizon_days, target_date, point, lo80, hi80, lo95, hi95, path,
                       model, arima_order, hybrid_weight_arima, explanation)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
ON CONFLICT (scheme_code, run_date, horizon_days) DO UPDATE SET
  target_date = EXCLUDED.target_date, point = EXCLUDED.point,
  lo80 = EXCLUDED.lo80, hi80 = EXCLUDED.hi80, lo95 = EXCLUDED.lo95, hi95 = EXCLUDED.hi95,
  path = EXCLUDED.path, model = EXCLUDED.model, arima_order = EXCLUDED.arima_order,
  hybrid_weight_arima = EXCLUDED.hybrid_weight_arima, explanation = EXCLUDED.explanation
"""

EXPLAIN_SYSTEM = """You explain a mutual fund's forecast range to a beginner investor in India.
Write exactly 2 short, warm, plain-English sentences (at most 60 words in total). Refer to the reader as "you".
Sentence 1: what the range means - the NAV could land between range_low_80 and range_high_80 about
horizon_trading_days trading days from now in roughly 8 out of 10 cases. Include the exact phrase "estimate, not a promise".
Sentence 2: how reliable past ranges were, using past_coverage_80_pct and skill_vs_naive_pct from the facts.
If skill_vs_naive_pct is 0 or below (or missing), say plainly that the model has not beaten a simple
"stays the same" guess, so the range is a rough idea of how much the NAV can move.
Rules: use only numbers that appear in the facts, copied exactly (rupee values with the rupee sign and two decimals).
Never tell the reader to buy, sell, switch, redeem, invest more, start, stop or pause anything. Never use the words
"guarantee" or "target price". Never predict a single exact price. No jargon."""

EXPLAIN_SCHEMA = {
    "type": "OBJECT",
    "properties": {"explanation": {"type": "STRING"}},
    "required": ["explanation"],
}

ADVICE_RE = re.compile(r"\byou (should|must|need to|ought to)\b|\b(recommend|advis|suggest)\w*|\b(buy|invest more|exit)\b",
                       re.I)


# ---------------------------------------------------------------- loading data

def load_funds(codes: list[int] | None = None) -> list[dict]:
    """Active funds across all users, one row per scheme code."""
    rows = db.query(
        """SELECT DISTINCT ON (scheme_code) scheme_code, COALESCE(short_name, scheme_name) AS name, scheme_name,
                  asset_class, benchmark_key, peer_group
           FROM funds WHERE is_active ORDER BY scheme_code, id""")
    if codes:
        rows = [r for r in rows if r["scheme_code"] in set(codes)]
    return rows


def fund_by_code(code: int) -> dict:
    """The fund row for a scheme code (active or not), or a bare description if we don't track it."""
    row = db.one(
        """SELECT scheme_code, COALESCE(short_name, scheme_name) AS name, scheme_name, asset_class, benchmark_key,
                  peer_group FROM funds WHERE scheme_code = %s ORDER BY is_active DESC, id LIMIT 1""", [int(code)])
    return row or {"scheme_code": int(code), "name": f"Scheme {code}", "asset_class": None,
                   "benchmark_key": None, "peer_group": None}


def load_inputs(fund: dict) -> dict:
    """NAV series, benchmark and category series, and the feature matrix for one fund."""
    start = date.today() - timedelta(days=int(365.25 * HISTORY_YEARS))
    nav = to_series(mfapi.series(int(fund["scheme_code"]), start))

    bench, bench_source = None, None
    key = fund.get("benchmark_key")
    if key and key != "CATEGORY_ONLY":
        try:
            got = indices.benchmark_series(key, start)
            s = to_series(got["rows"])
            if len(s) >= MIN_SIDE_SERIES:
                bench, bench_source = s, got["source"]
        except Exception:  # noqa: BLE001 - the benchmark is optional for the model
            pass

    cat = None
    if fund.get("peer_group"):
        try:
            s = to_series(category.category_series(fund["peer_group"], start))
            if len(s) >= MIN_SIDE_SERIES:
                cat = s
        except Exception:  # noqa: BLE001 - the category average is optional too
            pass

    X = build_features(nav, bench, cat) if len(nav) else pd.DataFrame()
    return {"nav": nav, "bench": bench, "bench_source": bench_source, "category": cat, "X": X}


# ---------------------------------------------------------------- this week's forecast

def business_day(last: pd.Timestamp, k: int) -> date:
    """The k-th business day (Mon-Fri) after `last`. Indian market holidays are not removed."""
    return (pd.Timestamp(last) + pd.offsets.BDay(k)).date()


def final_forecast(nav: pd.Series, X: pd.DataFrame, order, bt: dict, horizons=backtest.HORIZONS) -> dict:
    """Hybrid forecasts for each horizon plus a daily path, using all data up to the last NAV."""
    values = nav.to_numpy(dtype=float)
    log_nav = np.log(values)
    window = log_nav[-models.ARIMA_WINDOW:]
    path_days = max(max(horizons), PATH_DAYS)
    res = models.fit_arima(window, order)
    a_cum, a_se = models.arima_forecast(res, window[-1], path_days)
    last_nav, last_date, last_pos = float(values[-1]), nav.index[-1], len(nav) - 1

    out = {}
    for h in horizons:
        hz = bt["horizons"].get(h) or {}
        weight = float(hz.get("weight_arima", 1.0))
        mult = float(hz.get("multiplier", 1.0))
        rid = models.ridge_forecast_at(X, make_target(nav, h), last_pos, h)
        if rid is None:           # no Ridge forecast today -> pure ARIMA
            weight_used, ridge_val = 1.0, float(a_cum[h - 1])
        else:
            weight_used, ridge_val = weight, float(rid["cum"])
        cum = float(models.hybrid_cum(weight_used, a_cum[h - 1], ridge_val))
        se = float(a_se[h - 1])
        lo80, hi80 = models.band(last_nav, cum, se, mult, models.Z80)
        lo95, hi95 = models.band(last_nav, cum, se, mult, models.Z95)
        out[h] = {"target_date": business_day(last_date, h), "cum": cum, "point": last_nav * float(np.exp(cum)),
                  "lo80": float(lo80), "hi80": float(hi80), "lo95": float(lo95), "hi95": float(hi95),
                  "weight_arima": weight_used, "multiplier": mult, "arima_cum": float(a_cum[h - 1]),
                  "ridge_cum": None if rid is None else float(rid["cum"]),
                  "ridge_alpha": None if rid is None else rid["alpha"]}

    # Daily path for the chart, from the longest horizon: the hybrid point is interpolated
    # linearly in log space from 0 (today) to its h-day value; ARIMA's own per-day
    # standard errors (scaled by the calibrated multiplier) give the band on each day.
    h_path = max(horizons)
    cum_end, mult = out[h_path]["cum"], out[h_path]["multiplier"]
    path = []
    for k in range(1, path_days + 1):
        cum_k = cum_end * min(k, h_path) / h_path
        lo80, hi80 = models.band(last_nav, cum_k, a_se[k - 1], mult, models.Z80)
        lo95, hi95 = models.band(last_nav, cum_k, a_se[k - 1], mult, models.Z95)
        path.append({"date": business_day(last_date, k).isoformat(), "median": num(last_nav * np.exp(cum_k), 4),
                     "lo80": num(lo80, 4), "hi80": num(hi80, 4), "lo95": num(lo95, 4), "hi95": num(hi95, 4)})
    return {"last_nav": last_nav, "last_date": last_date.date(), "horizons": out, "path": path}


def write_forecasts(code: int, run_date: date, final: dict, order, explanation: str) -> int:
    rows = []
    for h, f in final["horizons"].items():
        path = final["path"][:h]   # the h=7 row carries the first 7 days, the h=30 row all 30
        rows.append((int(code), run_date, int(h), f["target_date"], num(f["point"], 4), num(f["lo80"], 4),
                     num(f["hi80"], 4), num(f["lo95"], 4), num(f["hi95"], 4), json.dumps(clean(path)), "hybrid",
                     models.order_text(order), num(f["weight_arima"], 3), explanation))
    return db.executemany(FORECAST_SQL, rows)


# ---------------------------------------------------------------- explanation (Gemini + guardrails)

def explanation_facts(fund: dict, final: dict, bt: dict) -> dict:
    """The only numbers Gemini may use (also the input to the guardrail number check)."""
    h = max(final["horizons"])
    f = final["horizons"][h]
    hz = bt["horizons"].get(h) or {}
    hyb = (hz.get("metrics") or {}).get("hybrid") or {}
    skill = hyb.get("skill_vs_naive")
    cov = hyb.get("coverage80")
    return {
        "asset_class": fund.get("asset_class") or "unknown",
        "last_nav": round(final["last_nav"], 2),
        "nav_date": final["last_date"].isoformat(),
        "horizon_trading_days": int(h),
        "range_level_pct": 80,
        "range_low_80": round(f["lo80"], 2),
        "range_high_80": round(f["hi80"], 2),
        "skill_vs_naive_pct": None if skill is None else round(skill * 100, 1),
        "past_coverage_80_pct": None if cov is None else int(round(cov * 100)),
        "backtest_origins": int(hz.get("n_origins") or 0),
    }


def fallback_explanation(facts: dict) -> str:
    """Deterministic 2-sentence text used when Gemini is unavailable or fails the checks."""
    s1 = (f"The NAV, now ₹{facts['last_nav']:.2f}, could be between ₹{facts['range_low_80']:.2f} and "
          f"₹{facts['range_high_80']:.2f} in about {facts['horizon_trading_days']} trading days in roughly "
          f"8 of 10 cases; this is an estimate, not a promise.")
    cov, skill, n = facts["past_coverage_80_pct"], facts["skill_vs_naive_pct"], facts["backtest_origins"]
    if cov is None:
        s2 = "There was not enough history to test how often past ranges held."
    elif skill is not None and skill > 0:
        s2 = (f"In {n} past tests the range caught the actual NAV {cov}% of the time, and its middle was "
              f"{skill}% closer than a simple 'stays the same' guess.")
    else:
        s2 = (f"In {n} past tests the range caught the actual NAV {cov}% of the time, but its middle did not "
              f"beat a simple 'stays the same' guess, so treat it as a rough guide to how much it can move.")
    return f"{s1} {s2}"


def check_explanation(text: str, facts: dict) -> list[str]:
    """Guardrails: 2 sentences, short, numbers from the facts only, no advice, must say 'estimate, not a promise'."""
    errors = guardrails.validate_short_text(text, facts, max_sentences=2, max_words=70, mode="report",
                                            must_include=[MUST_INCLUDE])
    if ADVICE_RE.search(text or ""):
        errors.append("Advice-like wording (should / recommend / buy / invest more)")
    return errors


def explain(fund: dict, final: dict, bt: dict) -> tuple[str, dict]:
    """Gemini explanation with one retry; deterministic fallback otherwise. Returns (text, info)."""
    facts = explanation_facts(fund, final, bt)
    fallback = fallback_explanation(facts)
    if not gemini.available():
        return fallback, {"source": "fallback", "reason": "GEMINI_API_KEY not set"}
    prompt = "Facts (JSON):\n" + json.dumps(facts)
    seen: list[list[str]] = []
    for _attempt in range(2):
        try:
            data, meta = gemini.generate_json(EXPLAIN_SYSTEM, prompt, EXPLAIN_SCHEMA, temperature=0.3)
        except Exception as exc:  # noqa: BLE001 - network, quota, bad JSON: all fall back
            return fallback, {"source": "fallback", "reason": f"Gemini error: {str(exc)[:200]}"}
        text = " ".join(str((data or {}).get("explanation", "")).split())
        errors = check_explanation(text, facts)
        if not errors:
            return text, {"source": "gemini", "model": meta.get("model"), "latency_ms": meta.get("latency_ms"),
                          "tokens_total": meta.get("tokens_total"), "retries": len(seen)}
        seen.append(errors)
        prompt += ("\n\nYour previous answer failed these checks. Fix every one and answer again:\n- "
                   + "\n- ".join(errors))
    return fallback, {"source": "fallback", "reason": "failed guardrails twice", "errors": seen}


# ---------------------------------------------------------------- one fund, then all funds

def forecast_one(fund: dict, run_date: date) -> dict:
    """Backtest + forecast + explanation + save for one fund. Returns a summary for job_runs."""
    started = time.time()
    code = int(fund["scheme_code"])
    data = load_inputs(fund)
    nav = data["nav"]
    info = {"name": fund.get("name"), "observations": len(nav), "benchmark_source": data["bench_source"],
            "category_feature": data["category"] is not None}
    if len(nav) < MIN_OBS:
        return {**info, "status": "skipped", "reason": "not enough history"}

    order, aic_table = models.select_arima_order(np.log(nav.to_numpy(dtype=float))[-models.ARIMA_WINDOW:])
    t_order = time.time()
    try:
        bt = backtest.run_backtest(nav, data["X"], order)
    except backtest.NotEnoughData as exc:
        return {**info, "status": "skipped", "reason": f"not enough history for a backtest ({exc})"}
    t_backtest = time.time()
    final = final_forecast(nav, data["X"], order, bt)
    explanation, expl_info = explain(fund, final, bt)
    write_forecasts(code, run_date, final, order, explanation)
    backtest.write_metrics(code, run_date, bt)

    per_h = {}
    for h, f in final["horizons"].items():
        hz = bt["horizons"].get(h) or {}
        hyb = (hz.get("metrics") or {}).get("hybrid") or {}
        per_h[str(h)] = {"weight_arima": f["weight_arima"], "multiplier": f["multiplier"],
                         "skill_vs_naive": num(hyb.get("skill_vs_naive"), 4), "coverage80": num(hyb.get("coverage80"), 3),
                         "point": num(f["point"], 4), "lo80": num(f["lo80"], 4), "hi80": num(f["hi80"], 4),
                         "ridge_alpha": f["ridge_alpha"]}
    return {**info, "status": "ok", "arima_order": models.order_text(order), "aic": aic_table,
            "last_nav_date": final["last_date"].isoformat(), "horizons": per_h,
            "backtest_origins": len(bt["origins"]), "backtest_failures": bt["failures"][:5],
            "explanation": expl_info,
            "timings_s": {"arima_order": round(t_order - started, 1), "backtest": round(t_backtest - t_order, 1),
                          "total": round(time.time() - started, 1)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flow 2: hybrid ARIMA + Ridge forecasts for every active fund")
    parser.add_argument("--codes", help="comma-separated scheme codes to limit the run (default: all active funds)")
    args = parser.parse_args(argv)
    codes = [int(c) for c in args.codes.split(",") if c.strip()] if args.codes else None

    run_date = today_local()
    with job_run("ml_forecast") as details:
        details["run_date"] = run_date.isoformat()
        funds = load_funds(codes)
        details["funds"] = {}
        print(f"ML forecast run {run_date}: {len(funds)} active fund(s)")
        counts = {"ok": 0, "skipped": 0, "failed": 0}
        for fund in funds:
            code = int(fund["scheme_code"])
            try:
                result = forecast_one(fund, run_date)
            except Exception as exc:  # noqa: BLE001 - record and carry on with the next fund
                traceback.print_exc()
                result = {"name": fund.get("name"), "status": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}
            counts[result["status"]] += 1
            details["funds"][str(code)] = clean(result)
            if result.get("explanation", {}).get("source") == "fallback":
                details["fallbacks"].append(f"{code}: explanation used the fixed template "
                                            f"({result['explanation'].get('reason')})")
            line = f"  {code} {fund.get('name')}: {result['status']}"
            if result["status"] == "ok":
                h30 = result["horizons"].get("30", {})
                line += (f" | ARIMA({result['arima_order']}) w={h30.get('weight_arima')} "
                         f"skill30={h30.get('skill_vs_naive')} cov80={h30.get('coverage80')} "
                         f"in {result['timings_s']['total']}s")
            else:
                line += f" ({result.get('reason')})"
            print(line, flush=True)
        details["counts"] = counts
        if counts["failed"] and not counts["ok"]:
            raise RuntimeError(f"Every fund failed ({counts['failed']}); see job_runs details")
        if counts["failed"]:
            details["_status"] = "partial"
        print(f"Done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
