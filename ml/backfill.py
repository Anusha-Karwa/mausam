"""Flow 2: Friday night forecasts and category averages -- the backfill step (runs first).

1. Top up NAV history (mfapi.in) for every active fund and every benchmark proxy fund.
   mfapi.top_up() fetches the latest NAV and fills any gap since our last stored date;
   a fund with no history at all gets its full history.
2. Re-fetch 5 years of daily index closes from Yahoo (weekly safety net for the
   5-day window the daily Vercel job fetches). If Yahoo fails, the proxy fund NAVs
   from step 1 are used by indices.benchmark_series() instead.
Logs a job_runs row (job = 'backfill'). Fails (non-zero exit) only if every NAV top-up failed.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":  # allow `python ml/backfill.py` from the repo root
    _HERE = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _HERE]
    if str(_HERE.parent) not in sys.path:
        sys.path.insert(0, str(_HERE.parent))

from app.jobs import job_run, tracked_codes
from app.services import indices, mfapi


def main() -> int:
    with job_run("backfill") as details:
        codes = tracked_codes()
        all_codes = codes["funds"] + codes["proxies"]
        print(f"Topping up NAVs for {len(codes['funds'])} fund(s) and {len(codes['proxies'])} proxy fund(s)")
        updated, failed = {}, {}
        for code in all_codes:
            try:
                res = mfapi.top_up(code)
                latest = res["latest"].isoformat() if res.get("latest") else None
                updated[str(code)] = {"filled": res["filled"], "latest": latest}
                print(f"  {code}: +{res['filled']} row(s), latest {latest}", flush=True)
            except Exception as exc:  # noqa: BLE001 - record and continue
                failed[str(code)] = str(exc)[:200]
                print(f"  {code}: FAILED {str(exc)[:200]}", flush=True)
        details["navs"] = {"updated": updated, "failed": failed}

        print("Fetching 5 years of index closes from Yahoo")
        idx = indices.refresh("5y")
        details["indices"] = idx
        for key, st in idx.items():
            if st["ok"]:
                print(f"  {key}: {st['rows']} closes, last {st['last']}")
            else:
                note = "using proxy fund" if st.get("source") == "proxy" else "no proxy"
                details["fallbacks"].append(f"{key}: Yahoo failed, {note}")
                print(f"  {key}: Yahoo failed ({st.get('error')}), {note}")

        if all_codes and not updated:
            raise RuntimeError("mfapi.in: every NAV top-up failed")
        if failed or any(not st["ok"] for st in idx.values()):
            details["_status"] = "partial"
    return 0


if __name__ == "__main__":
    sys.exit(main())
