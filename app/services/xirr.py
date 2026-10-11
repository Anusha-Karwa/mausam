"""XIRR in pure Python: "your yearly return, accounting for when you invested".

Cash flows: money you put in is negative, money you take out (and today's
value) is positive. We solve sum(cf / (1+r)^(days/365)) = 0 with Newton-Raphson,
falling back to bisection. Returns a fraction (0.124 = 12.4% a year) or None.
"""
from __future__ import annotations

from datetime import date


def _npv(rate: float, flows: list[tuple[date, float]]) -> float:
    d0 = flows[0][0]
    return sum(cf / (1.0 + rate) ** ((d - d0).days / 365.0) for d, cf in flows)


def _dnpv(rate: float, flows: list[tuple[date, float]]) -> float:
    d0 = flows[0][0]
    total = 0.0
    for d, cf in flows:
        t = (d - d0).days / 365.0
        total -= t * cf / (1.0 + rate) ** (t + 1.0)
    return total


def xirr(flows: list[tuple[date, float]], guess: float = 0.1, tol: float = 1e-7, max_iter: int = 100) -> float | None:
    flows = sorted((d, float(cf)) for d, cf in flows if cf)
    if len(flows) < 2:
        return None
    if not (any(cf < 0 for _, cf in flows) and any(cf > 0 for _, cf in flows)):
        return None
    if (flows[-1][0] - flows[0][0]).days < 1:
        return None

    rate = guess
    for _ in range(max_iter):
        try:
            value = _npv(rate, flows)
            deriv = _dnpv(rate, flows)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(value) < tol:
            return rate
        if deriv == 0:
            break
        new_rate = rate - value / deriv
        if new_rate <= -0.9999 or new_rate != new_rate:  # out of range or NaN
            break
        if abs(new_rate - rate) < tol:
            return new_rate
        rate = new_rate

    # Bisection fallback
    lo, hi = -0.9999, 10.0
    try:
        f_lo, f_hi = _npv(lo, flows), _npv(hi, flows)
    except (OverflowError, ZeroDivisionError):
        return None
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2.0
        f_mid = _npv(mid, flows)
        if abs(f_mid) < tol or (hi - lo) / 2.0 < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return None
