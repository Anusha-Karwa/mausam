"""Hit every page and GET endpoint on a deployed Mausam and check they work.

Usage: python scripts/smoke_test.py https://<project>.vercel.app
Exits non-zero (with a readable report) if anything fails.
"""
from __future__ import annotations

import sys

import httpx

PAGES = ["/", "/portfolio", "/compare", "/reports", "/trends", "/market", "/forecasts", "/transactions", "/manage",
         "/calculators", "/ask", "/learn", "/alerts", "/settings", "/status", "/about"]
APIS = ["/api/health", "/api/portfolio/summary", "/api/portfolio/timeseries", "/api/reports", "/api/trends",
        "/api/market/indices", "/api/market/news", "/api/alerts", "/api/settings", "/api/transactions",
        "/api/funds/search?q=nifty"]


def main(base: str) -> int:
    base = base.rstrip("/")
    client = httpx.Client(timeout=90, follow_redirects=True)
    failures, checks = [], []

    def check(path: str, expect_text: str | None = None, is_api: bool = False):
        try:
            r = client.get(base + path)
            ok = r.status_code == 200
            if ok and is_api:
                ok = r.json().get("ok") is True
            if ok and expect_text:
                ok = expect_text in r.text
            checks.append((path, r.status_code, ok))
            if not ok:
                failures.append(f"{path}: HTTP {r.status_code} {r.text[:160]!r}")
            return r
        except Exception as exc:  # noqa: BLE001
            checks.append((path, "ERR", False))
            failures.append(f"{path}: {exc}")
            return None

    for p in PAGES:
        check(p, "Mausam")
    for a in APIS:
        check(a, is_api=True)

    summary = check("/api/portfolio/summary", is_api=True)
    codes = [f["scheme_code"] for f in (summary.json()["data"]["funds"] if summary is not None and summary.status_code == 200 else [])]
    if codes:
        check(f"/funds/{codes[0]}", "Mausam")
        check(f"/api/funds/{codes[0]}/nav?range=1Y", is_api=True)
        check(f"/api/funds/{codes[0]}/returns", is_api=True)
        if len(codes) >= 2:
            check(f"/api/compare?codes={codes[0]},{codes[1]}", is_api=True)
    reports = check("/api/reports", is_api=True)
    if reports is not None and reports.status_code == 200 and reports.json()["data"]:
        check(f"/reports/{reports.json()['data'][0]['week_ending']}", "Mausam")

    for path, status, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {status}  {path}")
    print(f"\n{len(checks) - len(failures)}/{len(checks)} passed")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
