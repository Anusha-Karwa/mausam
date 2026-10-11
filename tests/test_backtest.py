"""Walk-forward backtest helpers and a synthetic random-walk sanity check. No database or network."""
import numpy as np
import pandas as pd
import pytest

from ml import backtest, models
from ml.features import build_features


def _random_walk(n=600, seed=0, sigma=0.01):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0, sigma, n))), index=dates)


# ---------- origins / windowing

def test_origins_are_monthly_and_leave_room_for_the_horizon():
    dates = pd.bdate_range("2022-01-03", periods=600)
    pos = backtest.origin_positions(dates, n_origins=12, max_horizon=30, min_train=200)
    assert len(pos) == 12
    assert pos == sorted(pos)
    assert pos[-1] == len(dates) - 1 - 30            # latest origin: its 30-day outcome is known
    gaps = np.diff(pos)
    assert all(17 <= g <= 26 for g in gaps)           # about one month of business days apart
    months = {(dates[p].year, dates[p].month) for p in pos}
    assert len(months) == 12


def test_origins_respect_minimum_history():
    dates = pd.bdate_range("2022-01-03", periods=300)
    pos = backtest.origin_positions(dates, n_origins=12, max_horizon=30, min_train=200)
    assert pos and min(pos) >= 200
    assert len(pos) < 12
    assert backtest.origin_positions(dates[:220], 12, 30, 200) == []


def test_ridge_trains_only_on_targets_known_at_the_origin():
    nav = _random_walk(n=400, seed=3)
    X = build_features(nav)
    from ml.features import make_target

    y = make_target(nav, 30)
    pos = 350
    base = models.ridge_forecast_at(X, y, pos, 30, alphas=(1.0,))
    # Change every NAV after the origin: features up to pos and targets known at pos are unchanged.
    nav2 = nav.copy()
    nav2.iloc[pos + 1:] *= 3.0
    X2 = build_features(nav2)
    y2 = make_target(nav2, 30)
    again = models.ridge_forecast_at(X2, y2, pos, 30, alphas=(1.0,))
    assert base is not None and again is not None
    assert again["cum"] == pytest.approx(base["cum"])


# ---------- metrics and calibration

def test_point_metrics_and_skill():
    nav0 = np.array([100.0, 100.0, 100.0, 100.0])
    actual = np.array([110.0, 90.0, 105.0, 95.0])
    naive = backtest.point_metrics(nav0, actual, nav0)
    assert naive["mae"] == pytest.approx(7.5)
    assert naive["directional_accuracy"] is None      # "stays the same" has no direction
    perfect = backtest.point_metrics(nav0, actual, actual)
    assert perfect["mae"] == 0 and perfect["directional_accuracy"] == 1.0
    assert backtest.skill_vs_naive(perfect["mae"], naive["mae"]) == 1.0
    assert backtest.skill_vs_naive(15.0, 7.5) == pytest.approx(-1.0)
    assert backtest.coverage(actual, actual - 1, actual + 1) == 1.0


def test_calibration_widens_too_narrow_intervals():
    rng = np.random.default_rng(7)
    actual = rng.normal(0, 2.0, 400)          # true spread is 2x what the model believes
    se = np.ones(400)
    m = backtest.calibrate_multiplier(np.zeros(400), se, actual, level=0.8)
    assert 1.8 <= m <= 2.2
    lo, hi = -m * models.Z80 * se, m * models.Z80 * se
    assert abs(backtest.coverage(actual, lo, hi) - 0.8) < 0.05


def test_calibration_stays_in_range_and_prefers_no_change():
    se = np.ones(10)
    # Perfect forecasts: every width covers everything, so ARIMA's own width (1.0) is kept.
    assert backtest.calibrate_multiplier(np.zeros(10), se, np.zeros(10)) == 1.0
    # Far too narrow: widen only as far as it helps (2.05 x 1.2816 just covers 2.611), within the cap.
    m = backtest.calibrate_multiplier(np.zeros(10), se, np.linspace(0.5, 10, 10))
    assert m == pytest.approx(2.05)
    assert min(backtest.MULTIPLIER_GRID) == 0.8 and max(backtest.MULTIPLIER_GRID) == 2.5


def test_choose_weight_picks_the_better_model():
    nav0 = np.full(5, 100.0)
    actual_cum = np.array([0.01, -0.02, 0.03, 0.0, 0.015])
    actual = nav0 * np.exp(actual_cum)
    w, _ = backtest.choose_weight(nav0, actual, actual_cum, np.zeros(5))
    assert w == 1.0                                   # "ARIMA" is perfect here
    w, _ = backtest.choose_weight(nav0, actual, np.zeros(5), actual_cum)
    assert w == 0.0                                   # "Ridge" is perfect here


# ---------- synthetic random walk: nothing can beat "stays the same"

def test_random_walk_has_no_real_skill():
    skills_arima, skills_hybrid = [], []
    for seed in (11, 12, 13):
        nav = _random_walk(n=600, seed=seed)
        X = build_features(nav)
        bt = backtest.run_backtest(nav, X, arima_order=(0, 1, 0), horizons=(7, 30), n_origins=12,
                                   alphas=(1.0, 100.0))
        for h in (7, 30):
            res = bt["horizons"][h]
            assert res is not None and res["n_origins"] == 12
            assert set(res["metrics"]) >= {"naive", "naive_drift", "arima", "hybrid"}
            assert 0.8 <= res["multiplier"] <= 2.5
            assert res["weight_arima"] in models.HYBRID_WEIGHTS
            assert len(res["hybrid_backtest"]) == 12
            skills_arima.append(res["metrics"]["arima"]["skill_vs_naive"])
            skills_hybrid.append(res["metrics"]["hybrid"]["skill_vs_naive"])
    assert abs(np.mean(skills_arima)) < 0.25
    assert abs(np.mean(skills_hybrid)) < 0.25


def test_metric_rows_shape():
    nav = _random_walk(n=500, seed=21)
    bt = backtest.run_backtest(nav, build_features(nav), arima_order=(0, 1, 0), alphas=(10.0,))
    rows = backtest.metric_rows(120716, "2026-10-10", bt)
    models_seen = {(r[2], r[3]) for r in rows}
    assert ("hybrid", 30) in models_seen and ("naive", 7) in models_seen
    hybrid30 = next(r for r in rows if r[2] == "hybrid" and r[3] == 30)
    assert '"backtest"' in hybrid30[-1]
    assert len(hybrid30) == 12
