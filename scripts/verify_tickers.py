"""Check which Yahoo Finance tickers return usable daily history (spec Section 7.2).

Usage: python scripts/verify_tickers.py
No database needed. Benchmarks whose ticker fails fall back to the proxy index fund in db/seed.sql.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CANDIDATES = {
    "NIFTY50": ["^NSEI"],
    "MIDCAP150": ["NIFTYMIDCAP150.NS", "^NIFMDCP150"],
    "SMALLCAP100": ["^CNXSC"],
    "SMALLCAP250": ["NIFTYSMLCAP250.NS"],
    "SP500": ["^GSPC"],
    "NASDAQ100": ["^NDX"],
    "USDINR": ["USDINR=X"],
}


def main() -> int:
    from app.services.indices import fetch_yahoo

    seed = (ROOT / "db" / "seed.sql").read_text(encoding="utf-8")
    proxies = dict(re.findall(r"\('(\w+)',\s*'[^']*',\s*(?:'[^']*'|NULL),\s*(\d+|NULL)", seed))
    for key, tickers in CANDIDATES.items():
        for t in tickers:
            try:
                rows = fetch_yahoo(t, "1y")
                verdict = "OK" if len(rows) >= 200 else "TOO SHORT (use proxy)"
                print(f"{key:<12} {t:<20} {len(rows):>4} points, last {rows[-1][0]}  {verdict}")
            except Exception as exc:  # noqa: BLE001
                print(f"{key:<12} {t:<20} FAILED ({exc})")
        print(f"{'':<12} proxy fund: {proxies.get(key, 'none')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
