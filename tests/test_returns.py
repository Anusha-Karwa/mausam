"""Returns engine (app/services/returns.py).

The main series is built so every answer is a round number:
  as of Fri 13 Jun 2025 the NAV is 337.5, and the reference NAVs are
  1W (6 Jun 2025) 300, 1M (13 May 2025) 270, YTD (31 Dec 2024) 250,
  1Y (13 Jun 2024) 225 and 3Y (13 Jun 2022) 100.
So 1W = +12.5%, 1M = +25%, YTD = +35%, 1Y = +50%, 3Y CAGR = 1.5^(3) -> +50% a year.
"""
from datetime import date, timedelta

import pytest

from app.services import returns

T = date(2025, 6, 13)


def business_days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def stepped_value(d: date) -> float:
    if d <= date(2022, 6, 13):
        return 100.0
    if d <= date(2024, 6, 13):
        return 225.0
    if d <= date(2024, 12, 31):
        return 250.0
    if d <= date(2025, 5, 13):
        return 270.0
    if d <= date(2025, 6, 6):
        return 300.0
    return 337.5


@pytest.fixture
def series():
    return [(d, stepped_value(d)) for d in business_days(date(2022, 1, 3), T)]


def test_dates_used_in_the_fixture_are_business_days():
    # Guard against a wrong calendar assumption in the test itself.
    assert T.weekday() == 4  # Friday
    for d in (date(2025, 6, 6), date(2025, 5, 13), date(2024, 12, 31), date(2024, 6, 13), date(2022, 6, 13)):
        assert d.weekday() < 5


def test_reference_dates():
    refs = returns.reference_dates(T)
    assert refs == {
        "ret_1w": date(2025, 6, 6),
        "ret_1m": date(2025, 5, 13),
        "ret_ytd": date(2024, 12, 31),
        "ret_1y": date(2024, 6, 13),
        "ret_3y_cagr": date(2022, 6, 13),
    }


def test_all_periods_exact(series):
    r = returns.compute(series)
    assert r["as_of"] == T
    assert r["value"] == 337.5
    assert r["ret_1w"] == pytest.approx(12.5, abs=1e-6)
    assert r["ret_1m"] == pytest.approx(25.0, abs=1e-6)
    assert r["ret_ytd"] == pytest.approx(35.0, abs=1e-6)
    assert r["ret_1y"] == pytest.approx(50.0, abs=1e-6)
    assert r["ret_3y_cagr"] == pytest.approx(50.0, abs=1e-4)


def test_as_of_a_sunday_uses_fridays_value(series):
    r = returns.compute(series, as_of=date(2025, 6, 15))  # Sunday
    assert r["as_of"] == T
    assert r["ret_1w"] == pytest.approx(12.5, abs=1e-6)


def test_as_of_earlier_date(series):
    # As of Sunday 8 Jun 2025 the latest value is Friday 6 Jun (300); a week before that is also 300.
    r = returns.compute(series, as_of=date(2025, 6, 8))
    assert r["as_of"] == date(2025, 6, 6)
    assert r["value"] == 300.0
    assert r["ret_1w"] == pytest.approx(0.0, abs=1e-9)
    assert r["ret_1m"] == pytest.approx((300 / 270 - 1) * 100, abs=1e-3)


def test_value_asof_over_weekend(series):
    assert returns.value_asof(series, date(2025, 6, 7)) == (date(2025, 6, 6), 300.0)  # Saturday
    assert returns.value_asof(series, date(2025, 6, 8)) == (date(2025, 6, 6), 300.0)  # Sunday
    assert returns.value_asof(series, date(2025, 6, 6)) == (date(2025, 6, 6), 300.0)  # exact day
    assert returns.value_asof(series, date(2021, 1, 1)) is None  # before the series starts
    assert returns.value_asof([], date(2025, 1, 1)) is None


def test_fund_too_new_gives_none():
    # Launched 3 Mar 2025: no 31 Dec 2024, 1-year or 3-year reference -> "Not enough history".
    s = [(d, 10.0 + i * 0.01) for i, d in enumerate(business_days(date(2025, 3, 3), T))]
    r = returns.compute(s)
    assert r["ret_ytd"] is None
    assert r["ret_1y"] is None
    assert r["ret_3y_cagr"] is None
    assert r["ret_1w"] is not None
    assert r["ret_1m"] is not None


def test_two_year_old_fund_has_1y_but_not_3y():
    s = [(d, 50.0) for d in business_days(date(2023, 6, 1), T)]
    r = returns.compute(s)
    assert r["ret_1y"] == pytest.approx(0.0)
    assert r["ret_3y_cagr"] is None


def test_big_data_gap_counts_as_missing():
    # Data stops on 29 Nov 2024 and resumes on 15 Jan 2025: the 31 Dec reference would be 32 days old.
    s = [(d, 100.0) for d in business_days(date(2024, 6, 3), date(2024, 11, 29))]
    s += [(d, 110.0) for d in business_days(date(2025, 1, 15), date(2025, 3, 14))]
    r = returns.compute(s)
    assert r["ret_ytd"] is None


def test_ytd_reference_when_31_dec_is_a_sunday():
    # 31 Dec 2023 was a Sunday, so the YTD reference is Friday 29 Dec 2023.
    assert date(2023, 12, 31).weekday() == 6
    s = []
    for d in business_days(date(2023, 12, 1), date(2024, 3, 15)):
        if d < date(2023, 12, 29):
            v = 90.0
        elif d == date(2023, 12, 29):
            v = 100.0
        else:
            v = 110.0
        s.append((d, v))
    r = returns.compute(s)
    assert r["as_of"] == date(2024, 3, 15)
    assert r["ret_ytd"] == pytest.approx(10.0, abs=1e-6)  # 110 / 100, not 110 / 90
    assert r["ret_1y"] is None


def test_empty_series():
    r = returns.compute([])
    assert all(r[k] is None for k in returns.PERIODS)
    assert r["as_of"] is None


def test_period_return_and_diff(series):
    assert returns.period_return(series, date(2025, 6, 6), T) == pytest.approx(12.5, abs=1e-6)
    assert returns.period_return(series, date(2020, 1, 1), T) is None
    assert returns.diff(-1.2, -1.6) == pytest.approx(0.4)
    assert returns.diff(None, 1.0) is None
    assert returns.diff(1.0, None) is None


def test_simple_return():
    assert returns.simple_return(110, 100) == pytest.approx(10.0)
    assert returns.simple_return(90, 100) == pytest.approx(-10.0)


def test_drawdown_and_max_drawdown():
    s = [(date(2025, 1, 1), 100.0), (date(2025, 1, 2), 120.0), (date(2025, 1, 3), 90.0), (date(2025, 1, 6), 130.0)]
    dd = returns.drawdown(s)
    assert [v for _, v in dd] == pytest.approx([0.0, 0.0, -25.0, 0.0])
    assert returns.max_drawdown(s) == pytest.approx(-25.0)
    assert returns.max_drawdown([]) is None


def test_calendar_year_returns():
    s = [(date(2022, 6, 1), 100.0), (date(2022, 12, 30), 110.0), (date(2023, 12, 29), 121.0), (date(2024, 6, 28), 133.1)]
    out = returns.calendar_year_returns(s)
    assert [o["year"] for o in out] == [2022, 2023, 2024]
    for o in out:
        assert o["ret"] == pytest.approx(10.0, abs=0.01)
    assert out[0]["partial"] is True    # started mid-2022
    assert out[1]["partial"] is False   # full year
    assert out[2]["partial"] is True    # current (last) year
    assert returns.calendar_year_returns([]) == []


def test_rebase():
    s = [(date(2025, 1, 1), 50.0), (date(2025, 1, 2), 55.0), (date(2025, 1, 3), 60.0)]
    assert returns.rebase(s, date(2025, 1, 2)) == [(date(2025, 1, 2), 100.0), (date(2025, 1, 3), pytest.approx(109.0909, abs=1e-3))]


@pytest.mark.parametrize("d, expected", [
    (date(2026, 10, 9), date(2026, 10, 9)),    # Friday -> itself
    (date(2026, 10, 10), date(2026, 10, 9)),   # Saturday -> yesterday
    (date(2026, 10, 11), date(2026, 10, 9)),   # Sunday
    (date(2026, 10, 12), date(2026, 10, 9)),   # Monday
    (date(2026, 10, 8), date(2026, 10, 2)),    # Thursday -> previous week's Friday
])
def test_last_friday(d, expected):
    assert expected.weekday() == 4
    assert returns.last_friday(d) == expected


def test_is_stale_weekday():
    wed = date(2026, 10, 7)
    assert wed.weekday() == 2
    assert returns.is_stale(date(2026, 10, 6), wed) is False   # 1 day old
    assert returns.is_stale(date(2026, 10, 3), wed) is False   # 4 days old: still fine
    assert returns.is_stale(date(2026, 10, 2), wed) is True    # 5 days old: stale


def test_is_stale_weekend_allows_fridays_nav():
    sat, sun = date(2026, 10, 10), date(2026, 10, 11)
    fri = date(2026, 10, 9)
    assert returns.is_stale(fri, sat) is False
    assert returns.is_stale(fri, sun) is False
    assert returns.is_stale(date(2026, 10, 2), sun) is True    # last week's Friday


def test_is_stale_without_data():
    assert returns.is_stale(None, date(2026, 10, 7)) is True
