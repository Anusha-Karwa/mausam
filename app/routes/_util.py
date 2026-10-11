"""Small helpers shared by the JSON API routes. Every response is {ok, data, error}."""
from __future__ import annotations

from datetime import date
from functools import wraps

from flask import g, jsonify, request


def ok(data=None, status: int = 200):
    return jsonify({"ok": True, "data": data, "error": None}), status


def fail(message: str, status: int = 400, data=None):
    return jsonify({"ok": False, "data": data, "error": message}), status


def body() -> dict:
    return request.get_json(silent=True) or {}


def user_id() -> int:
    return getattr(g, "user_id", 1)


def parse_date(value, field: str = "date") -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a date like 2026-10-09")


def handles_value_errors(fn):
    """Turn validation errors into friendly 400 responses."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            return fail(str(exc), 400)
    return wrapper
