"""Seed Meera's demo data: five real funds, 3 years of SIPs at real NAVs, peers, 26 weeks of snapshots,
one real Gemini report and a daily tip.

Usage (from the repo root, with DATABASE_URL set):
    python scripts/seed_meera.py            # asks you to confirm the five scheme codes first
    python scripts/seed_meera.py --yes      # non-interactive (CI)
    python scripts/seed_meera.py --yes --only report
Every step is idempotent, so it is safe to run again.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STEPS = ["migrate", "benchmarks", "funds", "transactions", "peers", "snapshots", "report", "tip"]


def confirm_funds() -> bool:
    from app.services import mfapi
    from app.services.seed import MEERA_FUNDS

    print("Meera's five funds (Direct Plan - Growth), checked live on mfapi.in:")
    for spec in MEERA_FUNDS:
        try:
            meta = mfapi.latest(spec["code"])["meta"]
            print(f"  {spec['code']}  {spec['short_name']:<22} {meta['scheme_name']}  [{meta['scheme_category']}]  SIP Rs {spec['sip']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {spec['code']}  could not be checked: {exc}")
    return input("Save these funds? [y/N] ").strip().lower() in ("y", "yes")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    parser.add_argument("--only", choices=STEPS, help="run a single step")
    args = parser.parse_args(argv)

    from app import create_app, jobs
    from app.services import seed
    from db.migrate import migrate

    if not args.yes and (args.only in (None, "funds")) and not confirm_funds():
        print("Stopped. Edit MEERA_FUNDS in app/services/seed.py to change the funds.")
        return 1

    actions = {
        "migrate": migrate,
        "benchmarks": seed.step_benchmarks,
        "funds": seed.step_funds,
        "transactions": seed.step_transactions,
        "peers": seed.step_peers,
        "snapshots": seed.step_snapshots,
        "report": lambda: {k: v for k, v in jobs.weekly_report(trigger="manual", send_email=False).items() if k != "trace"},
        "tip": lambda: jobs.daily_nav(trigger="manual"),
    }
    app = create_app()
    with app.app_context(), app.test_request_context("/"):
        for step in ([args.only] if args.only else STEPS):
            print(f"\n== {step}")
            result = actions[step]()
            text = str(result)
            print(text[:1200] + ("…" if len(text) > 1200 else ""))
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
