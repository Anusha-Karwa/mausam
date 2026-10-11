"""One polite HTTP helper: timeouts, a couple of retries with backoff, a clear User-Agent."""
from __future__ import annotations

import time
from typing import Any

import httpx

POLITE_UA = "Mausam/1.0 (student project)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class FetchError(RuntimeError):
    pass


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        timeout: float = 10.0, retries: int = 2, ua: str = POLITE_UA) -> httpx.Response:
    hdrs = {"User-Agent": ua, "Accept": "*/*"}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.get(url, params=params, headers=hdrs, timeout=timeout, follow_redirects=True)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(0.6 * (2 ** attempt))
                continue
            return resp
        except httpx.HTTPError as exc:
            last = exc
            if attempt < retries:
                time.sleep(0.6 * (2 ** attempt))
    raise FetchError(f"GET {url} failed: {last}")


def get_json(url: str, **kwargs) -> Any:
    resp = get(url, **kwargs)
    if resp.status_code != 200:
        raise FetchError(f"GET {url} returned HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(f"GET {url} did not return JSON") from exc
