"""Small helpers shared by the ML scripts (Flow 2: Friday night forecasts and category averages).

Postgres JSONB rejects NaN and Infinity, and psycopg cannot always store numpy
numbers, so everything we save goes through clean() / num() first.
"""
from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np


def num(value, digits: int = 4) -> float | None:
    """A plain rounded Python float, or None when the value is missing / NaN / infinite."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, digits)


def clean(obj):
    """Make any nested structure JSON-safe: numpy -> Python, NaN -> None, dates -> ISO strings."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (datetime, date)):  # pandas.Timestamp is a datetime too
        return obj.isoformat()
    if isinstance(obj, np.ndarray):
        return [clean(v) for v in obj.tolist()]
    return obj
