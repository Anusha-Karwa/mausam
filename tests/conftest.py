"""Shared pytest setup.

- Puts the repo root on sys.path so `import app...` and `import ml...` work from `pytest -q`.
- Blocks the network and the database for every test: unit tests must never call
  mfapi.in, Yahoo, GNews, Gemini, Resend or Neon. Anything that tries fails loudly.
- Provides a realistic weekly-report `facts` dict (shaped like spec section 11.2 and
  report.build_facts) and a hand-written report that passes every guardrail.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_network_or_database(monkeypatch):
    import httpx

    from app import db

    def blocked(*args, **kwargs):
        raise RuntimeError("Unit tests must not use the network or the database")

    monkeypatch.setattr(httpx, "get", blocked)
    monkeypatch.setattr(httpx, "post", blocked)
    monkeypatch.setattr(db, "_connect", blocked)
    yield


# ---------- weekly report facts (built by code) and a valid Gemini-style report ----------

FACTS = {
    "week_ending": "2026-10-09",
    "verdict": "One fund worth watching",
    "verdict_facts": {"fund": "Small-cap fund", "lag_streak_weeks": 3, "gap_1w_pp": -1.8, "gap_1m_pp": -4.1},
    "allocation": {"equity": 68.2, "debt": 21.5, "international": 10.3,
                   "target": {"equity": 70, "debt": 20, "international": 10}},
    "indices": {"NIFTY50": {"ret_1w": -0.9}, "MIDCAP150": {"ret_1w": -1.6}, "SMALLCAP100": {"ret_1w": -2.4},
                "SP500_INR": {"ret_1w": 0.7}},
    "funds": [
        {"name": "Mid-cap fund", "asset_class": "equity", "ret_1w": -1.2, "ret_1m": 0.8, "ret_ytd": 9.13,
         "benchmark": "Nifty Midcap 150", "bench_ret_1w": -1.6, "bench_ret_1m": 0.2,
         "cat_ret_1w": -1.4, "cat_ret_1m": 0.5, "delta_bench_1w_pp": 0.4, "delta_cat_1w_pp": 0.2,
         "attribution": "market_wide", "attribution_label": "Moved with the market", "lag_streak_weeks": 0,
         "stale": False},
        {"name": "Small-cap fund", "asset_class": "equity", "ret_1w": -4.2, "ret_1m": -6.5, "ret_ytd": 2.75,
         "benchmark": "Nifty Smallcap 250", "bench_ret_1w": -2.4, "bench_ret_1m": -2.4,
         "cat_ret_1w": -2.6, "cat_ret_1m": -3.1, "delta_bench_1w_pp": -1.8, "delta_cat_1w_pp": -1.6,
         "attribution": "fund_specific", "attribution_label": "Fund-specific move", "lag_streak_weeks": 3,
         "stale": False},
    ],
    "news": [
        {"id": 3, "title": "RBI holds repo rate", "date": "2026-10-08", "tags": ["debt"]},
        {"id": 7, "title": "Smallcap stocks slide on profit booking", "date": "2026-10-07", "tags": ["equity"]},
    ],
}

VALID_REPORT = {
    "headline": "Passing clouds: one fund is worth watching this week",
    "funds": [
        {"name": "Mid-cap fund",
         "summary": [
             "Your mid-cap fund fell 1.2% this week but is up 0.8% over the month.",
             "Its benchmark (the market index it is measured against) fell 1.6%, so the fund finished 0.4 points ahead.",
             "The move matched the broader market, so it looks market-wide.",
         ],
         "news_ids_used": []},
        {"name": "Small-cap fund",
         "summary": [
             "Your small-cap fund fell 4.2% this week and 6.5% over the month.",
             "That is 1.8 points behind the Nifty Smallcap 250, which fell 2.4%.",
             "Small company shares slipped on profit booking, and this fund slipped more than most.",
         ],
         "news_ids_used": [7]},
    ],
    "market_paragraph": ("The Nifty 50 fell 0.9% and the Midcap 150 fell 1.6% this week. "
                         "Small companies fell the most, while the S&P 500 in rupees rose 0.7%."),
    "verdict": "One fund worth watching",
    "verdict_reason": ("Your small-cap fund has trailed its benchmark for 3 weeks in a row. "
                       "It is 4.1 points behind over the past month."),
    "jargon_used": ["benchmark"],
}

# The verdict-engine result that goes with FACTS (what verdict.decide would return).
DECISION = {
    "verdict": "One fund worth watching",
    "watch": [{"fund": "Small-cap fund", "lag_streak_weeks": 3, "gap_1w_pp": -1.8, "gap_1m_pp": -4.1,
               "reason": "streak"}],
    "lagging": [{"fund": "Small-cap fund", "lag_streak_weeks": 3, "gap_1w_pp": -1.8, "gap_1m_pp": -4.1}],
    "drift_pp": 1.8,
    "drift_class": "equity",
    "facts": {"fund": "Small-cap fund", "lag_streak_weeks": 3, "gap_1w_pp": -1.8, "gap_1m_pp": -4.1},
}


@pytest.fixture
def facts():
    return copy.deepcopy(FACTS)


@pytest.fixture
def valid_report():
    return copy.deepcopy(VALID_REPORT)


@pytest.fixture
def decision():
    return copy.deepcopy(DECISION)
