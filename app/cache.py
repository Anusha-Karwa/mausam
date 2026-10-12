"""Tiny in-memory cache for slow, rarely-changing reads (index cards, etc.).

It lives inside one warm serverless instance only, so it is safe: worst case a value is
recomputed. Jobs that change the data call clear() so nobody sees stale numbers for long.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_store: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def cached(key: str, ttl: float, compute: Callable[[], Any]) -> Any:
    now = time.time()
    with _lock:
        hit = _store.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = compute()
    with _lock:
        _store[key] = (now, value)
    return value


def clear(prefix: str = "") -> None:
    with _lock:
        for k in [k for k in _store if k.startswith(prefix)]:
            _store.pop(k, None)
