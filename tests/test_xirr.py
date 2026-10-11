"""XIRR (app/services/xirr.py): known answers and the cases that must return None."""
from datetime import date

import pytest

from app.services.xirr import _npv, xirr


def test_one_year_ten_percent_over_a_leap_year():
    # 2020 is a leap year: 366 days pass, and XIRR uses a 365-day year,
    # so 1000 x (1 + r)^(366/365) = 1100  =>  r = 1.1^(365/366) - 1  (a little under 10%).
    flows = [(date(2020, 1, 1), -1000.0), (date(2021, 1, 1), 1100.0)]
    expected = 1.1 ** (365 / 366) - 1
    assert expected == pytest.approx(0.09971, abs=1e-5)
    assert xirr(flows) == pytest.approx(expected, abs=1e-6)


def test_one_normal_year_loss():
    flows = [(date(2021, 1, 1), -1000.0), (date(2022, 1, 1), 900.0)]  # 365 days
    assert xirr(flows) == pytest.approx(-0.10, abs=1e-6)


def test_tripling_in_a_year():
    flows = [(date(2021, 1, 1), -1000.0), (date(2022, 1, 1), 3000.0)]
    assert xirr(flows) == pytest.approx(2.0, abs=1e-5)


def test_monthly_sip_is_positive_and_solves_npv():
    flows = [(date(2023, m, 1), -1000.0) for m in range(1, 13)]
    flows.append((date(2024, 1, 1), 13000.0))  # value today: 1,000 more than the 12,000 put in
    rate = xirr(flows)
    assert rate is not None
    assert 0.0 < rate < 0.5
    assert _npv(rate, sorted(flows)) == pytest.approx(0.0, abs=1e-3)


def test_order_of_flows_does_not_matter():
    flows = [(date(2021, 1, 1), 1100.0), (date(2020, 1, 1), -1000.0)]
    assert xirr(flows) == pytest.approx(1.1 ** (365 / 366) - 1, abs=1e-6)


@pytest.mark.parametrize("flows", [
    [],                                                                     # nothing
    [(date(2020, 1, 1), -1000.0)],                                          # a single flow
    [(date(2020, 1, 1), -1000.0), (date(2021, 1, 1), -500.0)],              # all money in, nothing out
    [(date(2020, 1, 1), 1000.0), (date(2021, 1, 1), 500.0)],                # all positive
    [(date(2020, 1, 1), -1000.0), (date(2020, 1, 1), 1100.0)],              # same day: no time passed
    [(date(2020, 1, 1), -1000.0), (date(2021, 1, 1), 0.0)],                 # zero flows are ignored -> one flow
])
def test_invalid_inputs_return_none(flows):
    assert xirr(flows) is None
