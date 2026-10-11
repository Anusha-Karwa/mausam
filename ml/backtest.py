"""Walk-forward backtest: how good would our forecasts have been in the past?

Flow 2: Friday night forecasts and category averages.

Origins: 12 monthly dates over the last year (the latest one is 30 trading days
before the last NAV, so every forecast can be checked against a real NAV).
At each origin we fit every model using ONLY data up to that origin, forecast
7 and 30 trading days ahead, and compare with what actually happened.

Models compared
  naive        the NAV stays the same
  naive_drift  the NAV keeps growing at its average daily rate of the last ~3 years
  arima        ARIMA(p,1,q) with drift (order chosen once on the full window, reused at each origin)
  ridge        Ridge Regression on the features in ml/features.py
  hybrid       w x ARIMA + (1 - w) x Ridge, w chosen to minimise backtest MAE

Metrics (forecast_metrics table)
  mae, rmse                 in rupees of NAV
  mape                      in percent
  directional_accuracy      share (0-1) of origins where the forecast got the direction of the move right
  skill_vs_naive            1 - MAE_model / MAE_naive  (> 0 means better than "stays the same")
  coverage80                share (0-1) of actual NAVs that landed inside the 80% band
  n_origins                 how many origins were scored

Honesty notes: the hybrid weight and the interval multiplier are chosen on these
same 12 origins, so the hybrid's backtest numbers are slightly flattering. With
only 12 origins, coverage moves in steps of 1/12 (about 8 points).

Run `python ml/backtest.py <scheme_code>` to print a fund's backtest without saving anything.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import NormalDist

if __name__ == "__main__":  # allow `python ml/backtest.py` from the repo root
    _HERE = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _HERE]
    if str(_HERE.parent) not in sys.path:
        sys.path.insert(0, str(_HERE.parent))

import numpy as np
import pandas as pd

from ml import models
from ml.common import clean, num
from ml.features import make_target

HORIZONS = (7, 30)
N_ORIGINS = 12
MIN_TRAIN = 200            # an origin needs at least this many NAVs before it
MODELS = ("naive", "naive_drift", "arima", "ridge", "hybrid")
MULTIPLIER_GRID = tuple(round(0.8 + 0.05 * i, 2) for i in range(35))   # 0.80, 0.85, ..., 2.50


class NotEnoughData(ValueError):
    pass


# ---------------------------------------------------------------- pure helpers (unit-tested)

def origin_positions(dates, n_origins: int = N_ORIGINS, max_horizon: int = max(HORIZONS),
                     min_train: int = MIN_TRAIN) -> list[int]:
    """Row positions of the monthly backtest origins, oldest first.

    The latest origin is `max_horizon` rows before the last NAV (so its 30-day
    outcome is known). Earlier origins are the last NAV date on or before the same
    calendar day 1, 2, ... months earlier. Origins with fewer than `min_train`
    rows of history are dropped.
    """
    idx = pd.DatetimeIndex(dates)
    last_usable = len(idx) - 1 - max_horizon
    if last_usable < min_train:
        return []
    anchor = idx[last_usable]
    positions: list[int] = []
    for k in range(n_origins):
        target = anchor - pd.DateOffset(months=k)
        pos = int(idx.searchsorted(target, side="right")) - 1
        if pos < min_train:
            break
        if pos not in positions:
            positions.append(pos)
    return sorted(positions)


def point_metrics(nav0, actual, forecast) -> dict:
    """MAE, RMSE, MAPE (%) and directional accuracy (0-1) for one model's forecasts."""
    nav0 = np.asarray(nav0, dtype=float)
    actual = np.asarray(actual, dtype=float)
    forecast = np.asarray(forecast, dtype=float)
    err = forecast - actual
    move_f = np.sign(forecast - nav0)
    move_a = np.sign(actual - nav0)
    directional = None if np.all(move_f == 0) else float(np.mean(move_f == move_a))
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mape": float(np.mean(np.abs(err) / actual) * 100.0),
        "directional_accuracy": directional,
    }


def skill_vs_naive(mae_model: float | None, mae_naive: float | None) -> float | None:
    """1 - MAE_model / MAE_naive. Positive = better than 'the NAV stays the same'."""
    if mae_model is None or not mae_naive:
        return None
    return 1.0 - mae_model / mae_naive


def coverage(actual, low, high) -> float:
    """Share of actual values that fell inside [low, high]."""
    actual = np.asarray(actual, dtype=float)
    return float(np.mean((actual >= np.asarray(low)) & (actual <= np.asarray(high))))


def calibrate_multiplier(point_cum, se, actual_cum, level: float = 0.8, grid=MULTIPLIER_GRID) -> float:
    """Pick the interval multiplier m so backtest coverage of the `level` band is closest to `level`.

    The band is point +/- m x z x se (in log-return space). Ties go to the m closest
    to 1.0, i.e. the smallest change to ARIMA's own intervals.
    """
    z = NormalDist().inv_cdf(0.5 + level / 2.0)
    point_cum = np.asarray(point_cum, dtype=float)
    se = np.asarray(se, dtype=float)
    best_m, best_key = 1.0, None
    for m in grid:
        cov = coverage(actual_cum, point_cum - m * z * se, point_cum + m * z * se)
        key = (round(abs(cov - level), 9), abs(m - 1.0))
        if best_key is None or key < best_key:
            best_m, best_key = float(m), key
    return best_m


def choose_weight(nav0, actual, arima_cum, ridge_cum, weights=models.HYBRID_WEIGHTS) -> tuple[float, dict]:
    """Hybrid weight on ARIMA with the lowest backtest MAE (in NAV rupees). Returns (w, {w: mae})."""
    nav0 = np.asarray(nav0, dtype=float)
    actual = np.asarray(actual, dtype=float)
    best_w, best_mae = None, np.inf
    maes = {}
    for w in weights:
        fc = nav0 * np.exp(models.hybrid_cum(w, arima_cum, ridge_cum))
        mae = float(np.mean(np.abs(fc - actual)))
        maes[str(w)] = mae
        if mae < best_mae - 1e-12:
            best_w, best_mae = float(w), mae
    if best_w is None:  # only if every MAE was NaN; fall back to pure ARIMA
        best_w = 1.0
    return best_w, maes


def summarise_horizon(records: list[dict], horizon: int) -> dict | None:
    """Turn the per-origin forecasts for one horizon into weights, calibration and metrics."""
    if len(records) < 3:
        return None
    nav0 = np.array([r["nav0"] for r in records])
    actual = np.array([r["actual"] for r in records])
    actual_cum = np.array([r["actual_cum"] for r in records])
    arima = np.array([r["arima"] for r in records])
    se = np.array([r["se"] for r in records])
    drift = np.array([r["drift"] for r in records])
    ridge_missing = sum(r["ridge"] is None for r in records)
    has_ridge = ridge_missing < len(records)
    # Where Ridge had no forecast at an origin, the hybrid simply uses ARIMA there.
    ridge = np.array([r["ridge"] if r["ridge"] is not None else r["arima"] for r in records])

    if has_ridge:
        w, weight_mae = choose_weight(nav0, actual, arima, ridge)
    else:
        w, weight_mae = 1.0, {}
    hybrid = models.hybrid_cum(w, arima, ridge)
    multiplier = calibrate_multiplier(hybrid, se, actual_cum, 0.8)

    forecasts = {
        "naive": nav0.copy(),
        "naive_drift": nav0 * np.exp(drift),
        "arima": nav0 * np.exp(arima),
        "hybrid": nav0 * np.exp(hybrid),
    }
    if has_ridge:
        forecasts["ridge"] = nav0 * np.exp(ridge)
    metrics = {m: point_metrics(nav0, actual, fc) for m, fc in forecasts.items()}
    naive_mae = metrics["naive"]["mae"]
    for m in metrics:
        metrics[m]["skill_vs_naive"] = 0.0 if m == "naive" else skill_vs_naive(metrics[m]["mae"], naive_mae)
        metrics[m]["coverage80"] = None
        metrics[m]["n_origins"] = len(records)
    a_lo, a_hi = models.band(nav0, arima, se, 1.0, models.Z80)
    metrics["arima"]["coverage80"] = coverage(actual, a_lo, a_hi)
    h_lo, h_hi = models.band(nav0, hybrid, se, multiplier, models.Z80)
    metrics["hybrid"]["coverage80"] = coverage(actual, h_lo, h_hi)

    chart = [{"origin": r["origin"], "target_date": r["target_date"], "actual": num(r["actual"], 4),
              "forecast": num(forecasts["hybrid"][i], 4), "lo80": num(h_lo[i], 4), "hi80": num(h_hi[i], 4)}
             for i, r in enumerate(records)]
    return {"horizon": horizon, "weight_arima": w, "weight_mae": weight_mae, "multiplier": multiplier,
            "ridge_missing": ridge_missing, "n_origins": len(records), "metrics": metrics,
            "hybrid_backtest": chart}


def run_backtest(nav: pd.Series, X: pd.DataFrame, arima_order, horizons=HORIZONS, n_origins: int = N_ORIGINS,
                 alphas=models.RIDGE_ALPHAS, arima_window: int = models.ARIMA_WINDOW,
                 min_train: int = MIN_TRAIN) -> dict:
    """Walk-forward backtest for one fund. Pure: no database, no network.

    nav: NAV series (from features.to_series); X: features.build_features(nav, ...).
    arima_order: chosen once on the full window and reused at every origin (keeps runtime sane).
    Raises NotEnoughData if fewer than 3 origins can be scored.
    """
    nav = nav.astype(float).sort_index()
    dates = nav.index
    values = nav.to_numpy(dtype=float)
    log_nav = np.log(values)
    max_h = max(horizons)
    positions = origin_positions(dates, n_origins, max_h, min_train)
    if len(positions) < 3:
        raise NotEnoughData(f"only {len(positions)} backtest origins available")
    targets = {h: make_target(nav, h) for h in horizons}
    raw: dict[int, list[dict]] = {h: [] for h in horizons}
    failures = []
    for pos in positions:
        window = log_nav[max(0, pos - arima_window + 1): pos + 1]   # data up to the origin only
        try:
            res = models.fit_arima(window, arima_order)
            a_cum, a_se = models.arima_forecast(res, window[-1], max_h)
        except Exception as exc:  # noqa: BLE001 - one bad origin shouldn't sink the fund
            failures.append({"origin": dates[pos].date().isoformat(), "error": f"ARIMA: {str(exc)[:120]}"})
            continue
        drift_daily = float(np.mean(np.diff(window))) if len(window) > 1 else 0.0
        for h in horizons:
            try:
                rid = models.ridge_forecast_at(X, targets[h], pos, h, alphas)
            except Exception as exc:  # noqa: BLE001
                failures.append({"origin": dates[pos].date().isoformat(), "error": f"Ridge h={h}: {str(exc)[:120]}"})
                rid = None
            raw[h].append({
                "pos": pos,
                "origin": dates[pos].date().isoformat(),
                "target_date": dates[pos + h].date().isoformat(),
                "nav0": float(values[pos]),
                "actual": float(values[pos + h]),
                "actual_cum": float(log_nav[pos + h] - log_nav[pos]),
                "drift": drift_daily * h,
                "arima": float(a_cum[h - 1]),
                "se": float(a_se[h - 1]),
                "ridge": None if rid is None else float(rid["cum"]),
            })
    by_h = {h: summarise_horizon(raw[h], h) for h in horizons}
    if all(v is None for v in by_h.values()):
        raise NotEnoughData("fewer than 3 backtest origins could be scored")
    return {"arima_order": models.order_text(arima_order),
            "origins": [dates[p].date().isoformat() for p in positions],
            "horizons": by_h, "failures": failures}


# ---------------------------------------------------------------- database (not used by tests)

METRICS_SQL = """
INSERT INTO forecast_metrics (scheme_code, run_date, model, horizon_days, mae, mape, rmse, directional_accuracy,
                              skill_vs_naive, coverage80, n_origins, details)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (scheme_code, run_date, model, horizon_days) DO UPDATE SET
  mae = EXCLUDED.mae, mape = EXCLUDED.mape, rmse = EXCLUDED.rmse,
  directional_accuracy = EXCLUDED.directional_accuracy, skill_vs_naive = EXCLUDED.skill_vs_naive,
  coverage80 = EXCLUDED.coverage80, n_origins = EXCLUDED.n_origins, details = EXCLUDED.details
"""


def metric_rows(scheme_code: int, run_date, bt: dict) -> list[tuple]:
    """forecast_metrics rows (one per model and horizon) for a backtest result."""
    rows = []
    for h, res in bt["horizons"].items():
        if not res:
            continue
        for model in MODELS:
            m = res["metrics"].get(model)
            if not m:
                continue
            details = None
            if model == "hybrid":
                details = {"backtest": res["hybrid_backtest"], "weight_arima": res["weight_arima"],
                           "interval_multiplier": res["multiplier"], "mae_by_weight": res["weight_mae"],
                           "ridge_missing_origins": res["ridge_missing"]}
            elif model == "arima":
                details = {"order": bt["arima_order"]}
            rows.append((int(scheme_code), run_date, model, int(h), num(m["mae"], 6), num(m["mape"], 4),
                         num(m["rmse"], 6), num(m["directional_accuracy"], 3), num(m["skill_vs_naive"], 4),
                         num(m["coverage80"], 3), int(m["n_origins"]),
                         json.dumps(clean(details)) if details is not None else None))
    return rows


def write_metrics(scheme_code: int, run_date, bt: dict) -> int:
    """Replace this run's forecast_metrics rows for the fund (idempotent)."""
    from app import db

    rows = metric_rows(scheme_code, run_date, bt)
    db.execute("DELETE FROM forecast_metrics WHERE scheme_code = %s AND run_date = %s", [int(scheme_code), run_date])
    return db.executemany(METRICS_SQL, rows)


def main(argv: list[str] | None = None) -> int:
    """Print the backtest for the given scheme codes (nothing is saved)."""
    from ml import forecast

    codes = [int(c) for c in (argv if argv is not None else sys.argv[1:])]
    if not codes:
        print("Usage: python ml/backtest.py <scheme_code> [<scheme_code> ...]")
        return 2
    for code in codes:
        data = forecast.load_inputs(forecast.fund_by_code(code))
        if len(data["nav"]) < forecast.MIN_OBS:
            print(f"{code}: not enough history ({len(data['nav'])} NAVs)")
            continue
        order, _ = models.select_arima_order(np.log(data["nav"].to_numpy(dtype=float))[-models.ARIMA_WINDOW:])
        bt = run_backtest(data["nav"], data["X"], order)
        print(f"\n{code}: ARIMA order {bt['arima_order']}, origins {bt['origins'][0]} .. {bt['origins'][-1]}")
        for h, res in bt["horizons"].items():
            if not res:
                continue
            print(f"  h={h}: weight on ARIMA {res['weight_arima']}, interval multiplier {res['multiplier']}")
            for model, m in res["metrics"].items():
                print(f"    {model:12s} MAE {m['mae']:.4f}  skill {num(m['skill_vs_naive'], 3)}  "
                      f"coverage80 {num(m['coverage80'], 3)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
