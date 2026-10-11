"""Flow 2: Friday night forecasts and category averages -- the category-average step.

For every peer group used by an active fund, make sure we have up to 12 named peer
funds, top up their NAVs, and store the simple average of their returns in
category_stats for last Friday. The real work lives in app.services.category so the
web app and this job share one implementation.

Peer discovery (searching mfapi.in for peers again) is slow, so it only runs:
  - on the first Saturday of the month (India time), or
  - when asked: `python ml/peers.py --rediscover`, or REDISCOVER_PEERS=true
    (the GitHub Actions "Run workflow" checkbox sets this).
Otherwise existing peers are reused (groups with fewer than 5 peers are rediscovered anyway).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

if __name__ == "__main__":  # allow `python ml/peers.py` from the repo root
    _HERE = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _HERE]
    if str(_HERE.parent) not in sys.path:
        sys.path.insert(0, str(_HERE.parent))

from app.jobs import job_run, today_local
from app.services import category, returns
from ml.common import clean


def is_first_saturday(d: date) -> bool:
    return d.weekday() == 5 and d.day <= 7


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flow 2: refresh category averages (category_stats)")
    parser.add_argument("--rediscover", action="store_true", help="search mfapi.in for peer funds again")
    args = parser.parse_args(argv)

    today = today_local()
    as_of = returns.last_friday(today)
    env_flag = os.environ.get("REDISCOVER_PEERS", "").strip().lower() in ("1", "true", "yes")
    reasons = [r for r, on in (("--rediscover flag", args.rediscover), ("REDISCOVER_PEERS", env_flag),
                               ("first Saturday of the month", is_first_saturday(today))) if on]
    rediscover = bool(reasons)

    with job_run("peers") as details:
        details.update({"as_of": as_of.isoformat(), "rediscover": rediscover, "rediscover_reason": reasons})
        print(f"Category averages as of {as_of} (rediscover peers: {', '.join(reasons) if reasons else 'no'})")
        result = category.refresh_all_groups(as_of, rediscover=rediscover)
        groups = {}
        for group, r in result.items():
            stats = r.get("stats") or {}
            groups[group] = {"peers": r.get("peers"), "peer_count": stats.get("peer_count"),
                             "ret_1w": stats.get("ret_1w"), "ret_1m": stats.get("ret_1m")}
            print(f"  {group}: {r.get('peers')} peers, average built from {stats.get('peer_count')} "
                  f"| 1W {stats.get('ret_1w')}% | 1M {stats.get('ret_1m')}%", flush=True)
        details["groups"] = clean(groups)
        empty = [g for g, v in groups.items() if not v["peer_count"]]
        if empty:
            details["_status"] = "partial"
            details["fallbacks"].append(f"no category average for: {', '.join(empty)} (pages show benchmark only)")
        if not groups:
            print("  No active funds with a peer group yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
