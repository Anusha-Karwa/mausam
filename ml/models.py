"""The two halves of the hybrid ARIMA + Ridge Regression model, and how they are combined.

Flow 2: Friday night forecasts and category averages. Pure functions (no database),
shared by ml/backtest.py (walk-forward evaluation) and ml/forecast.py (this week's forecast).

ARIMA (AutoRegressive Integrated Moving Average)
  Fitted on log NAV with d=1 and a drift term (trend='t'), which is the same as an
  ARMA model on daily log returns with a constant. The (p, q) order is picked from
  {0,1,2} x {0,1,2} by AIC on the last ~3 years. It gives a mean path and
  prediction intervals for 1..30 trading days ahead.

Ridge Regression (direct multi-horizon)
  One model per horizon h (7 and 30 trading days) predicting the cumulative log
  return over the next h days from the features in ml/features.py. StandardScaler +
  Ridge in one Pipeline; alpha chosen by time-ordered cross-validation
  (TimeSeriesSplit with a gap of h days so overlapping targets can't leak).

Hybrid
  point = w x ARIMA + (1 - w) x Ridge, on cumulative log returns, with w chosen in
  the backtest. Intervals reuse ARIMA's standard errors around the hybrid point,
  widened or narrowed by a calibration multiplier (also chosen in the backtest).
  NAV forecast = NAV_t x exp(cumulative log return).
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from ml.features import usable_columns

Z80 = 1.2815515655446004   # standard normal quantile for a central 80% interval
Z95 = 1.959963984540054    # ... and for 95%

ARIMA_GRID = tuple((p, 1, q) for p in (0, 1, 2) for q in (0, 1, 2))
ARIMA_WINDOW = 756          # about 3 years of trading days
ARIMA_MAXITER = 150

RIDGE_ALPHAS = (0.1, 1.0, 10.0, 100.0)
RIDGE_WINDOW = 1260         # train Ridge on at most ~5 years of rows
MIN_RIDGE_ROWS = 100        # fewer usable training rows than this -> no Ridge forecast

HYBRID_WEIGHTS = (1.0, 0.75, 0.5, 0.25, 0.0)   # weight on ARIMA; ties go to the earlier (simpler) one


def order_text(order) -> str:
    """(1, 1, 2) -> '1,1,2' (how it is stored in forecasts.arima_order)."""
    return ",".join(str(int(x)) for x in order)


# ---------------------------------------------------------------- ARIMA

def fit_arima(log_nav, order, maxiter: int = ARIMA_MAXITER):
    """Fit ARIMA(p,1,q) with drift to a 1-D array of log NAVs. Raises if the fit fails.

    We pass a plain numpy array (not a date-indexed Series) because NAV dates skip
    holidays, which statsmodels would warn about. Convergence warnings are silenced;
    a fit that errors or returns a non-finite AIC counts as failed.
    """
    from statsmodels.tsa.arima.model import ARIMA

    y = np.asarray(log_nav, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ARIMA(y, order=tuple(int(x) for x in order), trend="t")
        try:
            # Faster: cap the optimiser's iterations and skip the parameter covariance
            # matrix (forecast intervals come from the Kalman filter and don't need it).
            res = model.fit(method_kwargs={"maxiter": maxiter}, cov_type="none")
        except Exception:  # noqa: BLE001 - older/newer statsmodels may reject an option; use defaults
            res = model.fit()
    if not np.isfinite(res.aic):
        raise ValueError(f"ARIMA{tuple(order)} gave a non-finite AIC")
    return res


def arima_forecast(res, last_log: float, steps: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean cumulative log return and its standard error for 1..steps days ahead."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fc = res.get_forecast(steps=steps)
        mean = np.asarray(fc.predicted_mean, dtype=float).reshape(-1)
        ci = np.asarray(fc.conf_int(alpha=0.05), dtype=float).reshape(steps, 2)
    # Recover the standard error from the 95% interval (works whatever the statsmodels version).
    se = (ci[:, 1] - ci[:, 0]) / (2.0 * Z95)
    if not (np.all(np.isfinite(mean)) and np.all(np.isfinite(se))):
        raise ValueError("ARIMA forecast contained non-finite values")
    return mean - float(last_log), se


def select_arima_order(log_nav, grid=ARIMA_GRID) -> tuple[tuple[int, int, int], dict]:
    """Try every (p,1,q) in the grid and keep the lowest AIC. Failing orders are skipped.

    Returns (best_order, {"p,d,q": aic or None}). Raises RuntimeError if nothing fits.
    """
    best_order, best_aic = None, np.inf
    table: dict[str, float | None] = {}
    for order in grid:
        try:
            res = fit_arima(log_nav, order)
        except Exception:  # noqa: BLE001 - skip orders that fail to fit
            table[order_text(order)] = None
            continue
        aic = float(res.aic)
        table[order_text(order)] = round(aic, 2)
        if aic < best_aic:
            best_order, best_aic = tuple(order), aic
    if best_order is None:
        raise RuntimeError("No ARIMA order could be fitted")
    return best_order, table


# ---------------------------------------------------------------- Ridge

def cv_splits(n_rows: int, horizon: int, max_splits: int = 5) -> int:
    """How many TimeSeriesSplit folds fit in n_rows with a gap of `horizon` (0 = too few rows for CV)."""
    for k in range(max_splits, 1, -1):
        test_size = n_rows // (k + 1)
        first_train = n_rows - k * test_size - horizon
        if test_size >= 20 and first_train >= 40:
            return k
    return 0


def fit_ridge(X: np.ndarray, y: np.ndarray, horizon: int, alphas=RIDGE_ALPHAS):
    """StandardScaler + Ridge, with alpha picked by time-ordered CV (never shuffled). Returns (model, alpha)."""
    from sklearn.linear_model import Ridge
    from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    def make(alpha: float) -> Pipeline:
        return Pipeline([("scale", StandardScaler()), ("ridge", Ridge(alpha=alpha))])

    k = cv_splits(len(X), horizon)
    if k >= 2 and len(alphas) > 1:
        search = GridSearchCV(make(1.0), {"ridge__alpha": list(alphas)},
                              cv=TimeSeriesSplit(n_splits=k, gap=horizon),
                              scoring="neg_mean_absolute_error")
        search.fit(X, y)
        alpha = float(search.best_params_["ridge__alpha"])
    else:
        # Too little data for cross-validation: use the only alpha given, else a middle value.
        alpha = float(alphas[0]) if len(alphas) == 1 else 10.0
    model = make(alpha)
    model.fit(X, y)
    return model, alpha


def ridge_forecast_at(X: pd.DataFrame, target: pd.Series, pos: int, horizon: int,
                      alphas=RIDGE_ALPHAS, window: int = RIDGE_WINDOW) -> dict | None:
    """Ridge forecast of the cumulative log return over `horizon` days, made at row `pos`.

    Only rows whose target was already known at `pos` are used for training: a row s
    has target ln(NAV_s+h / NAV_s), known only when s + h <= pos. Returns None when
    there isn't enough clean data.
    """
    train_end = pos - horizon
    if train_end < 0:
        return None
    start = max(0, train_end - window + 1)
    Xt = X.iloc[start:train_end + 1]
    yt = target.iloc[start:train_end + 1]
    cols = usable_columns(Xt)
    if not cols:
        return None
    x_now = X.iloc[pos][cols].to_numpy(dtype=float)
    if not np.all(np.isfinite(x_now)):
        return None
    X_train = Xt[cols].to_numpy(dtype=float)
    y_train = yt.to_numpy(dtype=float)
    keep = np.all(np.isfinite(X_train), axis=1) & np.isfinite(y_train)
    if int(keep.sum()) < MIN_RIDGE_ROWS:
        return None
    model, alpha = fit_ridge(X_train[keep], y_train[keep], horizon, alphas)
    pred = float(model.predict(x_now.reshape(1, -1))[0])
    if not np.isfinite(pred):
        return None
    return {"cum": pred, "alpha": alpha, "n_train": int(keep.sum()), "columns": cols}


# ---------------------------------------------------------------- Hybrid

def hybrid_cum(weight_arima: float, arima_cum, ridge_cum):
    """w x ARIMA + (1 - w) x Ridge, on cumulative log returns."""
    return weight_arima * np.asarray(arima_cum, dtype=float) + (1.0 - weight_arima) * np.asarray(ridge_cum, dtype=float)


def band(nav0, cum, se, multiplier: float, z: float):
    """(low, high) NAV band: NAV_t x exp(cum -/+ multiplier x z x se)."""
    nav0 = np.asarray(nav0, dtype=float)
    cum = np.asarray(cum, dtype=float)
    width = multiplier * z * np.asarray(se, dtype=float)
    return nav0 * np.exp(cum - width), nav0 * np.exp(cum + width)
