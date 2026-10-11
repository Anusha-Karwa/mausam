"""Feature matrix for the Ridge Regression half of the hybrid ARIMA + Ridge model.

Flow 2: Friday night forecasts and category averages.

The golden rule is NO LOOK-AHEAD: the feature row for date t may only use
NAVs (and benchmark / category values) published on or before t. Every feature
below is a backward-looking difference or rolling window, so changing any data
after t can never change the row for t (tests/test_ml_features.py checks this).

Rows are fund NAV dates (business days on which the fund published a NAV).
We never invent NAVs. Benchmark and category series are lined up with the
fund's dates by taking the latest value on or before each date (forward-fill
only, used purely for alignment).

Features at date t (r = daily log return, ln(NAV_t / NAV_t-1)):
  ret_lag1, ret_lag2, ret_lag3, ret_lag5  r_t, r_t-1, r_t-2, r_t-4 (the last 1, 2, 3, 5 days' returns)
  mom_5, mom_20, mom_60                   log return over the last 5 / 20 / 60 trading days (momentum)
  vol_20, vol_60                          standard deviation of daily log returns over 20 / 60 days
  dist_ma50                               NAV / (50-day moving average) - 1
  bench_ret_5, bench_ret_20               benchmark log return over the last 5 / 20 fund trading days
  cat_ret_20                              category-average log return over the last 20 fund trading days
  month_2 .. month_12                     optional month-of-year dummies (January is the baseline)

Target for horizon h: the cumulative log return over the NEXT h trading days,
ln(NAV_t+h / NAV_t). It is NaN for the last h rows (the future isn't known yet).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RETURN_LAGS = (1, 2, 3, 5)
MOMENTUM_WINDOWS = (5, 20, 60)
VOLATILITY_WINDOWS = (20, 60)
MA_WINDOW = 50
BENCH_WINDOWS = (5, 20)
CATEGORY_WINDOW = 20
WARMUP_ROWS = 61  # the longest look-back (60-day momentum / volatility) needs 61 NAVs


def to_series(rows) -> pd.Series:
    """[(date, value), ...] -> float Series indexed by date, sorted, duplicates and bad values removed."""
    if rows is None or len(rows) == 0:
        return pd.Series(dtype=float)
    index = pd.to_datetime([d for d, _ in rows])
    s = pd.Series([float(v) for _, v in rows], index=index, dtype=float)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[np.isfinite(s.to_numpy()) & (s.to_numpy() > 0)]
    s.index.name = "date"
    return s


def align_to(series: pd.Series | None, index: pd.DatetimeIndex) -> pd.Series:
    """Value of `series` on each date of `index`, using the latest value on or before that date.

    This is forward-fill only: a date never sees a value from its future.
    """
    if series is None or len(series) == 0:
        return pd.Series(np.nan, index=index, dtype=float)
    s = series.astype(float).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(index, method="ffill")


def build_features(nav: pd.Series, benchmark: pd.Series | None = None, category: pd.Series | None = None,
                   month_dummies: bool = False) -> pd.DataFrame:
    """Build the feature matrix (one row per NAV date) using only data up to each date.

    nav:        fund NAVs indexed by date (use to_series()).
    benchmark:  optional benchmark index levels (or proxy-fund NAVs) indexed by date.
    category:   optional category-average level series (e.g. the rebased 'average peer' line).
    Early rows contain NaN until enough history exists; the model drops them.
    """
    nav = nav.astype(float).sort_index()
    log_nav = np.log(nav)
    r = log_nav.diff()
    f = pd.DataFrame(index=nav.index)

    for k in RETURN_LAGS:
        f[f"ret_lag{k}"] = r.shift(k - 1)
    for w in MOMENTUM_WINDOWS:
        f[f"mom_{w}"] = log_nav - log_nav.shift(w)
    for w in VOLATILITY_WINDOWS:
        f[f"vol_{w}"] = r.rolling(w).std()
    f["dist_ma50"] = nav / nav.rolling(MA_WINDOW).mean() - 1.0

    if benchmark is not None and len(benchmark) > 0:
        b = np.log(align_to(benchmark, nav.index))
        for w in BENCH_WINDOWS:
            f[f"bench_ret_{w}"] = b - b.shift(w)

    if category is not None and len(category) > 0:
        c = np.log(align_to(category, nav.index))
        f[f"cat_ret_{CATEGORY_WINDOW}"] = c - c.shift(CATEGORY_WINDOW)

    if month_dummies:
        months = nav.index.month
        for m in range(2, 13):
            f[f"month_{m}"] = (months == m).astype(float)
    return f


def make_target(nav: pd.Series, horizon: int) -> pd.Series:
    """Cumulative log return over the next `horizon` trading days: ln(NAV_t+h / NAV_t)."""
    nav = nav.astype(float).sort_index()
    return np.log(nav.shift(-horizon) / nav)


def usable_columns(X: pd.DataFrame, max_missing: float = 0.10) -> list[str]:
    """Columns with at most `max_missing` share of NaN in the given (training) rows.

    Optional inputs such as the category average may only cover recent years; if a
    column is mostly empty we leave it out instead of throwing away most rows.
    """
    if len(X) == 0:
        return []
    share = X.isna().mean()
    return [c for c in X.columns if share[c] <= max_missing]
