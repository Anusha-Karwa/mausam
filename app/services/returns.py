"""Return calculations, in pure Python (no pandas on Vercel).

All returns are in PERCENT (1.24 means +1.24%). Differences between two returns
are in percentage points ("points" in the UI).

"As-of" lookup: for a reference date we use the last value on or before it, so
weekends and holidays work naturally. If the series doesn't go back far enough
(fund too new), the return is None and the UI says "Not enough history".
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

Series = list[tuple[date, float]]  # sorted oldest -> newest

PERIODS = ("ret_1w", "ret_1m", "ret_ytd", "ret_1y", "ret_3y_cagr")


def value_asof(series: Series, d: date) -> tuple[date, float] | None:
    """Last point on or before d, or None if d is before the series starts."""
    if not series:
        return None
    i = bisect_right(series, (d, math.inf))
    return series[i - 1] if i else None


def reference_dates(t: date) -> dict[str, date]:
    return {
        "ret_1w": t - timedelta(days=7),
        "ret_1m": t - relativedelta(months=1),
        "ret_ytd": date(t.year - 1, 12, 31),
        "ret_1y": t - relativedelta(years=1),
        "ret_3y_cagr": t - relativedelta(years=3),
    }


def simple_return(v_now: float, v_then: float) -> float:
    return (v_now / v_then - 1.0) * 100.0


def _ref_ok(series: Series, ref: date) -> tuple[date, float] | None:
    """Reference value, but only if the series actually covers that date.

    A fund launched after `ref` has no value on or before it, so the return is None.
    """
    point = value_asof(series, ref)
    if point is None:
        return None
    # If the last value on/before ref is very old (big data gap), treat as missing.
    if (ref - point[0]).days > 10:
        return None
    return point


def compute(series: Series, as_of: date | None = None) -> dict:
    """WoW, MoM, YTD, 1Y and 3Y CAGR for a series as of a date (default: its last date)."""
    out = {k: None for k in PERIODS}
    out.update({"as_of": None, "value": None})
    if not series:
        return out
    current = value_asof(series, as_of) if as_of else series[-1]
    if current is None:
        return out
    t, v = current
    out["as_of"], out["value"] = t, v
    for key, ref in reference_dates(t).items():
        point = _ref_ok(series, ref)
        if point is None or point[1] <= 0:
            continue
        if key == "ret_3y_cagr":
            out[key] = round(((v / point[1]) ** (1 / 3) - 1.0) * 100.0, 4)
        else:
            out[key] = round(simple_return(v, point[1]), 4)
    return out


def period_return(series: Series, start: date, end: date) -> float | None:
    a, b = value_asof(series, start), value_asof(series, end)
    if not a or not b or a[1] <= 0 or (start - a[0]).days > 10:
        return None
    return round(simple_return(b[1], a[1]), 4)


def diff(a: float | None, b: float | None) -> float | None:
    """a - b in percentage points, or None."""
    if a is None or b is None:
        return None
    return round(a - b, 4)


def daily_returns(series: Series) -> list[tuple[date, float]]:
    """Simple daily returns in percent between consecutive points."""
    return [(series[i][0], simple_return(series[i][1], series[i - 1][1])) for i in range(1, len(series)) if series[i - 1][1] > 0]


def annualised_volatility(series: Series, days: int = 365) -> float | None:
    if not series:
        return None
    start = series[-1][0] - timedelta(days=days)
    window = [p for p in series if p[0] >= start]
    rets = [math.log(window[i][1] / window[i - 1][1]) for i in range(1, len(window)) if window[i - 1][1] > 0]
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100.0, 2)


def drawdown(series: Series) -> list[tuple[date, float]]:
    """% below the previous peak at each date (0 at a new high, negative otherwise)."""
    peak = -math.inf
    out = []
    for d, v in series:
        peak = max(peak, v)
        out.append((d, round((v / peak - 1.0) * 100.0, 3)))
    return out


def max_drawdown(series: Series) -> float | None:
    dd = drawdown(series)
    return min(v for _, v in dd) if dd else None


def calendar_year_returns(series: Series) -> list[dict]:
    """Return for each calendar year covered (partial years flagged)."""
    if not series:
        return []
    out = []
    first_year, last_year = series[0][0].year, series[-1][0].year
    for y in range(first_year, last_year + 1):
        start_pt = value_asof(series, date(y - 1, 12, 31))
        end_pt = value_asof(series, date(y, 12, 31))
        partial = False
        if start_pt is None:
            start_pt = next((p for p in series if p[0].year == y), None)
            partial = True
        if end_pt is None or start_pt is None:
            continue
        if y == last_year:
            partial = True
        out.append({"year": y, "ret": round(simple_return(end_pt[1], start_pt[1]), 2), "partial": partial})
    return out


def monthly_returns(series: Series) -> list[tuple[str, float]]:
    """Month-end to month-end returns, labelled YYYY-MM."""
    if not series:
        return []
    month_end: dict[str, float] = {}
    for d, v in series:
        month_end[f"{d.year:04d}-{d.month:02d}"] = v
    keys = sorted(month_end)
    return [(keys[i], round(simple_return(month_end[keys[i]], month_end[keys[i - 1]]), 3)) for i in range(1, len(keys))]


def rolling_returns(series: Series, years: int = 1, step_days: int = 7) -> list[tuple[date, float]]:
    """Rolling N-year return sampled every step_days."""
    if not series:
        return []
    out = []
    start_date = series[0][0] + relativedelta(years=years)
    d = start_date
    end = series[-1][0]
    while d <= end:
        now = value_asof(series, d)
        then = value_asof(series, d - relativedelta(years=years))
        if now and then and then[1] > 0:
            out.append((d, round(simple_return(now[1], then[1]), 2)))
        d += timedelta(days=step_days)
    return out


def rebase(series: Series, start: date, base: float = 100.0) -> Series:
    """Rescale so the value on `start` equals base. Used for overlay charts."""
    first = value_asof(series, start)
    if first is None:
        window = series
        first = series[0] if series else None
    else:
        window = [p for p in series if p[0] >= first[0]]
    if not first or first[1] <= 0:
        return []
    return [(d, round(v / first[1] * base, 4)) for d, v in window]


def correlation(a: Series, b: Series, start: date | None = None) -> float | None:
    """Correlation of daily returns on common dates."""
    ra = dict(daily_returns([p for p in a if not start or p[0] >= start]))
    rb = dict(daily_returns([p for p in b if not start or p[0] >= start]))
    common = sorted(set(ra) & set(rb))
    if len(common) < 20:
        return None
    xs = [ra[d] for d in common]
    ys = [rb[d] for d in common]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if vx == 0 or vy == 0:
        return None
    return round(cov / (vx * vy), 3)


def last_friday(d: date) -> date:
    """Most recent Friday on or before d."""
    return d - timedelta(days=(d.weekday() - 4) % 7)


def is_stale(last_nav_date: date | None, today: date) -> bool:
    """Stale if the latest NAV is older than 4 calendar days on a weekday."""
    if last_nav_date is None:
        return True
    if today.weekday() >= 5:  # weekend: Friday's NAV is the freshest possible
        return (today - last_nav_date).days > 4 + (today.weekday() - 4)
    return (today - last_nav_date).days > 4
