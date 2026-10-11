"""Benchmark index data.

Primary: Yahoo Finance's public chart endpoint (unofficial, no key).
Fallback: the NAV series of a direct-growth index fund that tracks the same index
(benchmarks.proxy_scheme_code), which our daily job keeps in nav_history anyway.

We never mix the two sources inside one series (an index level of 22,000 and a
fund NAV of 158 can't be joined). benchmark_series() picks one source per call.
USD indices are converted to rupees with the USD/INR rate on each date, so
returns are INR returns: (1 + r_index) x (1 + r_usdinr) - 1.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from .. import db
from . import mfapi
from .http import BROWSER_UA, FetchError, get_json

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

PROXY_NOTE = "Benchmark measured via an index fund that tracks it."


def benchmarks() -> list[dict]:
    return db.query("SELECT * FROM benchmarks ORDER BY key")


def benchmark(key: str) -> dict | None:
    return db.one("SELECT * FROM benchmarks WHERE key = %s", [key])


def fetch_yahoo(ticker: str, rng: str = "5d") -> list[tuple[date, float]]:
    url = CHART.format(ticker=quote(ticker, safe=""))
    payload = get_json(url, params={"range": rng, "interval": "1d"}, ua=BROWSER_UA, timeout=12)
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        raise FetchError(f"Yahoo returned no data for {ticker}")
    r = result[0]
    stamps = r.get("timestamp") or []
    closes = (((r.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    gmtoffset = int((r.get("meta") or {}).get("gmtoffset") or 0)
    out: dict[date, float] = {}
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        # convert to the exchange's local date (IST for NSE, New York for US)
        d = datetime.fromtimestamp(ts + gmtoffset, tz=timezone.utc).date()
        out[d] = float(close)
    if not out:
        raise FetchError(f"Yahoo returned empty closes for {ticker}")
    return sorted(out.items())


def store(key: str, rows: list[tuple[date, float]], source: str = "yahoo") -> int:
    return db.executemany(
        "INSERT INTO index_history (benchmark_key, trade_date, close, source) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (benchmark_key, trade_date) DO UPDATE SET close = EXCLUDED.close, source = EXCLUDED.source",
        [(key, d, round(v, 4), source) for d, v in rows],
    )


def refresh(rng: str = "5d") -> dict:
    """Fetch recent closes for every benchmark with a Yahoo ticker. Returns per-key status."""
    status = {}
    for b in benchmarks():
        if not b["yahoo_ticker"]:
            continue
        try:
            rows = fetch_yahoo(b["yahoo_ticker"], rng)
            store(b["key"], rows)
            status[b["key"]] = {"ok": True, "source": "yahoo", "rows": len(rows), "last": rows[-1][0].isoformat()}
        except Exception as exc:  # noqa: BLE001 - any failure falls back to the proxy
            status[b["key"]] = {"ok": False, "source": "proxy" if b["proxy_scheme_code"] else None, "error": str(exc)[:200]}
    return status


def _yahoo_rows(key: str, start: date | None = None) -> list[tuple[date, float]]:
    sql = "SELECT trade_date, close FROM index_history WHERE benchmark_key = %s AND source = 'yahoo'"
    params: list = [key]
    if start:
        sql += " AND trade_date >= %s"
        params.append(start)
    return [(r["trade_date"], float(r["close"])) for r in db.query(sql + " ORDER BY trade_date", params)]


def _asof(rows: list[tuple[date, float]], d: date) -> float | None:
    from bisect import bisect_right

    i = bisect_right(rows, (d, float("inf")))
    return rows[i - 1][1] if i else None


def to_inr(index_rows: list[tuple[date, float]], fx_rows: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Multiply each USD close by the USD/INR rate on (or just before) that date."""
    out = []
    for d, v in index_rows:
        fx = _asof(fx_rows, d)
        if fx:
            out.append((d, v * fx))
    return out


def benchmark_series(key: str, start: date | None = None, min_rows: int = 30) -> dict:
    """Return {'rows': [(date, value)], 'source': 'yahoo'|'proxy'|None, 'note': str}.

    Prefers Yahoo; uses the proxy fund if Yahoo data is missing, too short, or stale
    compared with the proxy (Yahoo blocked for a few days).
    """
    b = benchmark(key)
    if not b or key == "CATEGORY_ONLY":
        return {"rows": [], "source": None, "note": ""}
    fetch_start = (start - timedelta(days=10)) if start else None
    yahoo = _yahoo_rows(key, fetch_start)
    proxy: list[tuple[date, float]] = []
    if b["proxy_scheme_code"]:
        proxy = mfapi.series(b["proxy_scheme_code"], fetch_start)

    use_proxy = False
    if len(yahoo) < min_rows and proxy:
        use_proxy = True
    elif yahoo and proxy and (proxy[-1][0] - yahoo[-1][0]).days > 4:
        use_proxy = True

    if use_proxy:
        # Proxy funds are rupee funds already, so no currency conversion.
        return {"rows": proxy, "source": "proxy", "note": PROXY_NOTE}
    rows = yahoo
    if b["currency"] == "USD" and rows:
        rows = to_inr(rows, _yahoo_rows("USDINR", fetch_start))
    return {"rows": rows, "source": "yahoo" if rows else None, "note": "Price index from Yahoo Finance" if rows else ""}


def latest_index_dates() -> dict[str, date | None]:
    rows = db.query("SELECT benchmark_key, MAX(trade_date) AS last FROM index_history GROUP BY benchmark_key")
    return {r["benchmark_key"]: r["last"] for r in rows}
