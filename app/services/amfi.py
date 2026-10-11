"""AMFI NAVAll.txt: the official daily NAV file from the Association of Mutual Funds in India.

Free, no key: https://www.amfiindia.com/spages/NAVAll.txt (~1.5 MB, every open scheme, latest NAV only).
Mausam uses it as an automatic fallback in the daily job when mfapi.in fails or is behind.

Format: semicolon-separated rows under a header line. As of Oct 2026 the header is
  Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date
(older files had 6 columns without Plan/Option), so we map columns by header name.
Between data rows there are section lines without semicolons:
  "Open Ended Schemes(Debt Scheme - Short Duration Fund)"  -> scheme category
  "ICICI Prudential Mutual Fund"                           -> fund house
Dates look like 09-Oct-2026; NAV can be "N.A." for suspended schemes.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime

from .http import get

URL = "https://www.amfiindia.com/spages/NAVAll.txt"

_cache: dict = {"at": 0.0, "data": None}
CACHE_SECONDS = 600

_CATEGORY_RE = re.compile(r"^(Open Ended Schemes|Close Ended Schemes|Interval Fund Schemes)\s*\((.*)\)\s*$", re.I)

DEFAULT_COLUMNS = ["Scheme Code", "ISIN Div Payout/ ISIN Growth", "ISIN Div Reinvestment", "Scheme Name",
                   "Net Asset Value", "Date"]


def _parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _norm(header: str) -> str:
    return re.sub(r"[^a-z]", "", header.lower())


def parse(text: str) -> dict[int, dict]:
    """Parse NAVAll.txt into {scheme_code: {scheme_name, nav, nav_date, isin_growth, plan, option, category, fund_house}}.

    Rows with a missing/invalid NAV or date are skipped (never guessed).
    """
    columns = DEFAULT_COLUMNS
    index: dict[str, int] = {}

    def build_index(cols: list[str]) -> dict[str, int]:
        n = [_norm(c) for c in cols]

        def find(*keys):
            for i, c in enumerate(n):
                if any(k in c for k in keys):
                    return i
            return None

        return {"code": find("schemecode"), "isin": find("isindivpayout", "isingrowth"), "name": find("schemename"),
                "plan": find("plan"), "option": find("option"), "nav": find("netassetvalue", "nav"), "date": find("date")}

    index = build_index(columns)
    out: dict[int, dict] = {}
    category = fund_house = None
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if ";" not in line:
            m = _CATEGORY_RE.match(line)
            if m:
                category = m.group(2).strip()
            else:
                fund_house = line
            continue
        parts = [p.strip() for p in line.split(";")]
        if _norm(parts[0]) == "schemecode":
            columns = parts
            index = build_index(columns)
            continue
        try:
            code = int(parts[index["code"]])
        except (TypeError, ValueError, IndexError):
            continue
        try:
            nav = float(parts[index["nav"]].replace(",", ""))
        except (TypeError, ValueError, IndexError):
            continue
        if nav <= 0:
            continue
        nav_date = _parse_date(parts[index["date"]]) if index["date"] is not None and index["date"] < len(parts) else None
        if nav_date is None:
            continue

        def col(key):
            i = index.get(key)
            return parts[i] if i is not None and i < len(parts) and parts[i] not in ("", "-") else None

        out[code] = {"scheme_code": code, "scheme_name": col("name"), "nav": nav, "nav_date": nav_date,
                     "isin_growth": col("isin"), "plan": col("plan"), "option": col("option"),
                     "category": category, "fund_house": fund_house}
    return out


def fetch(force: bool = False) -> dict[int, dict]:
    """Download and parse the file (cached for 10 minutes)."""
    if not force and _cache["data"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    resp = get(URL, timeout=30, retries=1)
    if resp.status_code != 200:
        raise RuntimeError(f"AMFI NAVAll.txt returned HTTP {resp.status_code}")
    data = parse(resp.text)
    if len(data) < 1000:
        raise RuntimeError(f"AMFI NAVAll.txt looked incomplete ({len(data)} schemes)")
    _cache.update(at=time.time(), data=data)
    return data
