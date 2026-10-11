"""ML feature construction: no look-ahead, correct targets, sensible alignment. No database or network."""
import numpy as np
import pandas as pd
import pytest

from ml.features import (WARMUP_ROWS, align_to, build_features, make_target, to_series, usable_columns)


def _nav(n=300, seed=1, start="2023-01-02"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n))), index=dates)


def _bench(nav, seed=2):
    # Benchmark on a slightly different calendar (drop some days) to exercise alignment.
    rng = np.random.default_rng(seed)
    dates = nav.index[::1].delete(list(range(10, len(nav), 17)))
    return pd.Series(20000 * np.exp(np.cumsum(rng.normal(0.0003, 0.009, len(dates)))), index=dates)


def test_features_do_not_look_ahead():
    nav = _nav()
    bench = _bench(nav)
    cat = _nav(seed=5) * 0.5
    base = build_features(nav, bench, cat, month_dummies=True)
    t = 200
    # Scramble everything strictly after t: NAV, benchmark and category.
    nav2, bench2, cat2 = nav.copy(), bench.copy(), cat.copy()
    cutoff = nav.index[t]
    nav2[nav2.index > cutoff] *= np.linspace(0.5, 2.0, int((nav2.index > cutoff).sum()))
    bench2[bench2.index > cutoff] = 1.0
    cat2[cat2.index > cutoff] = 999.0
    changed = build_features(nav2, bench2, cat2, month_dummies=True)
    pd.testing.assert_frame_equal(base.iloc[:t + 1], changed.iloc[:t + 1])
    # ...and the rows after t really did change, so the test has teeth.
    assert not base.iloc[t + 5:].equals(changed.iloc[t + 5:])


def test_expected_columns_and_values():
    nav = _nav()
    X = build_features(nav, _bench(nav))
    for col in ("ret_lag1", "ret_lag2", "ret_lag3", "ret_lag5", "mom_5", "mom_20", "mom_60",
                "vol_20", "vol_60", "dist_ma50", "bench_ret_5", "bench_ret_20"):
        assert col in X.columns
    log_nav = np.log(nav)
    t = 100
    assert X["ret_lag1"].iloc[t] == pytest.approx(log_nav.iloc[t] - log_nav.iloc[t - 1])
    assert X["ret_lag5"].iloc[t] == pytest.approx(log_nav.iloc[t - 4] - log_nav.iloc[t - 5])
    assert X["mom_20"].iloc[t] == pytest.approx(log_nav.iloc[t] - log_nav.iloc[t - 20])
    assert X["dist_ma50"].iloc[t] == pytest.approx(nav.iloc[t] / nav.iloc[t - 49:t + 1].mean() - 1)
    # After the warm-up every core feature is filled in.
    assert not X.iloc[WARMUP_ROWS:].isna().any().any()


def test_target_is_future_cumulative_log_return():
    nav = _nav(n=120)
    y = make_target(nav, 7)
    assert y.iloc[10] == pytest.approx(np.log(nav.iloc[17] / nav.iloc[10]))
    assert y.iloc[-7:].isna().all()
    assert y.iloc[:-7].notna().all()


def test_align_to_only_fills_forward():
    idx = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
    s = pd.Series([1.0, 3.0], index=pd.to_datetime(["2024-01-02", "2024-01-04"]))
    out = align_to(s, idx)
    assert np.isnan(out.iloc[0])          # nothing known yet: never back-filled from the future
    assert out.tolist()[1:] == [1.0, 1.0, 3.0]


def test_to_series_cleans_rows():
    from datetime import date

    rows = [(date(2024, 1, 3), 11.0), (date(2024, 1, 2), 10.0), (date(2024, 1, 3), 12.0), (date(2024, 1, 4), 0.0)]
    s = to_series(rows)
    assert list(s.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03"]
    assert s.iloc[-1] == 12.0


def test_usable_columns_drops_mostly_empty_optional_inputs():
    X = pd.DataFrame({"a": np.arange(10.0), "b": [np.nan] * 8 + [1.0, 2.0]})
    assert usable_columns(X) == ["a"]
