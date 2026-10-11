"""Deterministic verdict engine (app/services/verdict.py). Rules only, no AI."""
import pytest

from app.services import verdict
from app.services.verdict import NO_ACTION, REVIEW, WATCH_ONE

TH = {
    "equity_week_lag_pp": 1.0, "equity_month_lag_pp": 2.0,
    "debt_week_lag_pp": 0.15, "debt_month_lag_pp": 0.4,
    "intl_week_lag_pp": 1.5, "intl_month_lag_pp": 3.0,
    "consecutive_weeks_to_watch": 3, "allocation_drift_pp": 10,
    "market_wide_band_pp": 0.75, "debt_market_wide_band_pp": 0.1, "intl_market_wide_band_pp": 1.0,
}
TARGET = {"equity": 70, "debt": 20, "international": 10}
ON_TARGET = {"equity": 70.0, "debt": 20.0, "international": 10.0}


def snap(gap_1w: float, gap_1m: float = 0.0) -> dict:
    """A weekly snapshot whose 1W gap vs benchmark is gap_1w and whose 1M gap is gap_1m (points)."""
    return {"delta_bench_1w": gap_1w, "ret_1m": 1.0 + gap_1m, "bench_ret_1m": 1.0, "cat_ret_1m": None}


OK = snap(-0.2)        # a normal week
LAG = snap(-1.5)       # 1.5 points behind: lagging (equity limit is 1.0)


def fund(name, history, asset_class="equity"):
    return {"name": name, "asset_class": asset_class, "history": history}


# ---------- lagging and streaks ----------

def test_is_lagging_week_and_month_rules():
    assert verdict.is_lagging(LAG, "equity", TH) is True
    assert verdict.is_lagging(OK, "equity", TH) is False
    assert verdict.is_lagging(snap(-0.2, gap_1m=-2.5), "equity", TH) is True   # month rule
    assert verdict.is_lagging(snap(-1.0), "equity", TH) is False                # exactly at the limit is not lagging


def test_debt_uses_category_gap_when_there_is_no_benchmark():
    s = {"delta_bench_1w": None, "delta_cat_1w": -0.2, "ret_1m": 0.5, "bench_ret_1m": None, "cat_ret_1m": 0.6}
    assert verdict.ref_gap_1w(s) == -0.2
    assert verdict.ref_gap_1m(s) == pytest.approx(-0.1)
    assert verdict.is_lagging(s, "debt", TH) is True       # 0.2 behind > 0.15 debt limit
    assert verdict.is_lagging(s, "equity", TH) is False    # same numbers are fine for equity


@pytest.mark.parametrize("history, expected", [
    ([LAG, LAG, LAG, OK, LAG], 3),   # newest first: three in a row, then a break
    ([OK, LAG, LAG, LAG], 0),        # newest week is fine: streak is broken
    ([LAG, LAG, OK, LAG], 2),
    ([LAG], 1),
    ([], 0),
])
def test_lag_streak_counts_from_newest(history, expected):
    assert verdict.lag_streak(history, "equity", TH) == expected


# ---------- the three verdicts ----------

def test_no_action_needed():
    funds = [fund("Large-cap index fund", [OK, OK, OK]), fund("Mid-cap fund", [LAG, OK, OK])]
    r = verdict.decide(funds, ON_TARGET, TARGET, TH)
    assert r["verdict"] == NO_ACTION
    assert r["watch"] == []
    assert len(r["lagging"]) == 1                     # lagging this week, but only for 1 week
    assert r["facts"] == {"funds_lagging_this_week": 1, "allocation_drift_pp": 0.0}


def test_one_fund_worth_watching_after_three_lagging_weeks():
    funds = [fund("Small-cap fund", [LAG, LAG, LAG, OK]), fund("Mid-cap fund", [OK, OK, OK])]
    r = verdict.decide(funds, ON_TARGET, TARGET, TH)
    assert r["verdict"] == WATCH_ONE
    assert [w["fund"] for w in r["watch"]] == ["Small-cap fund"]
    assert r["watch"][0]["reason"] == "streak"
    # Facts for Gemini: the fund, its streak and the gaps; no internal "reason" key.
    assert r["facts"] == {"fund": "Small-cap fund", "lag_streak_weeks": 3, "gap_1w_pp": -1.5, "gap_1m_pp": 0.0}


def test_two_lagging_weeks_is_not_yet_watch():
    funds = [fund("Small-cap fund", [LAG, LAG, OK])]
    r = verdict.decide(funds, ON_TARGET, TARGET, TH)
    assert r["verdict"] == NO_ACTION
    assert r["lagging"][0]["lag_streak_weeks"] == 2


def test_big_one_month_gap_puts_fund_on_watch_immediately():
    # 1M gap of -4.5 points is more than 2x the 2.0 monthly threshold, even with no weekly streak.
    funds = [fund("Mid-cap fund", [snap(-0.2, gap_1m=-4.5), OK, OK])]
    r = verdict.decide(funds, ON_TARGET, TARGET, TH)
    assert r["verdict"] == WATCH_ONE
    assert r["watch"][0]["reason"] == "big_month_gap"
    assert r["facts"]["gap_1m_pp"] == -4.5
    assert r["facts"]["lag_streak_weeks"] == 1


def test_month_gap_of_exactly_twice_threshold_is_not_watch():
    funds = [fund("Mid-cap fund", [snap(-0.2, gap_1m=-4.0), OK, OK])]
    r = verdict.decide(funds, ON_TARGET, TARGET, TH)
    assert r["verdict"] == NO_ACTION


def test_allocation_drift_over_limit_means_review():
    funds = [fund("Mid-cap fund", [OK])]
    alloc = {"equity": 82.0, "debt": 10.0, "international": 8.0}
    r = verdict.decide(funds, alloc, TARGET, TH)
    assert r["verdict"] == REVIEW
    assert r["drift_pp"] == 12.0
    assert r["drift_class"] == "equity"
    assert r["facts"]["allocation_drift_pp"] == 12.0
    assert r["facts"]["drift_asset_class"] == "equity"
    assert r["facts"]["drift_limit_pp"] == 10.0
    assert r["facts"]["funds_to_watch"] == []


def test_drift_exactly_at_limit_is_fine():
    alloc = {"equity": 80.0, "debt": 10.0, "international": 10.0}
    r = verdict.decide([fund("Mid-cap fund", [OK])], alloc, TARGET, TH)
    assert r["drift_pp"] == 10.0
    assert r["verdict"] == NO_ACTION


def test_two_watch_funds_means_review():
    funds = [fund("Small-cap fund", [LAG, LAG, LAG]), fund("Mid-cap fund", [LAG, LAG, LAG, LAG])]
    r = verdict.decide(funds, ON_TARGET, TARGET, TH)
    assert r["verdict"] == REVIEW
    assert {w["fund"] for w in r["facts"]["funds_to_watch"]} == {"Small-cap fund", "Mid-cap fund"}


def test_funds_without_history_are_skipped():
    r = verdict.decide([fund("New fund", [])], ON_TARGET, TARGET, TH)
    assert r["verdict"] == NO_ACTION
    assert r["lagging"] == [] and r["watch"] == []


def test_allocation_drift_helper():
    assert verdict.allocation_drift({"equity": 75, "debt": 17, "international": 8}, TARGET) == (5.0, "equity")
    assert verdict.allocation_drift({}, {}) == (0.0, None)


# ---------- reason text and weather ----------

def test_reason_text_for_each_verdict():
    watch = verdict.decide([fund("Small-cap fund", [LAG, LAG, LAG])], ON_TARGET, TARGET, TH)
    assert "Small-cap fund" in verdict.reason_text(watch)
    assert "3 weeks in a row" in verdict.reason_text(watch)

    month = verdict.decide([fund("Mid-cap fund", [snap(-0.2, gap_1m=-4.5)])], ON_TARGET, TARGET, TH)
    assert "4.50 points behind" in verdict.reason_text(month)

    review = verdict.decide([fund("Mid-cap fund", [OK])], {"equity": 82.0, "debt": 10.0, "international": 8.0}, TARGET, TH)
    text = verdict.reason_text(review)
    assert "equity share is 12.00 points away" in text
    assert "financial adviser" in text

    two = verdict.decide([fund("A", [LAG] * 3), fund("B", [LAG] * 3)], ON_TARGET, TARGET, TH)
    assert "2 funds have been trailing" in verdict.reason_text(two)

    calm = verdict.decide([fund("A", [OK])], ON_TARGET, TARGET, TH)
    assert "in line" in verdict.reason_text(calm)


@pytest.mark.parametrize("v, key, label, icon", [
    (NO_ACTION, "clear", "Clear skies", "sun"),
    (WATCH_ONE, "clouds", "Passing clouds", "cloud"),
    (REVIEW, "storm", "Storm watch", "cloud-rain"),
])
def test_weather_mapping(v, key, label, icon):
    assert verdict.weather(v) == {"key": key, "label": label, "icon": icon}


def test_weather_without_report():
    assert verdict.weather(None)["key"] == "unknown"
    assert verdict.weather("Buy everything")["key"] == "unknown"


def test_only_three_verdicts_exist():
    assert verdict.VERDICTS == ("No action needed", "One fund worth watching", "Consider reviewing your allocation")
