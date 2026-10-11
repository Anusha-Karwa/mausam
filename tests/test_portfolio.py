"""Portfolio maths (pure parts of app/services/portfolio.py): FIFO, cash flows, allocation."""
from datetime import date

from app.services import portfolio


def txn(id_, d, kind, amount, units):
    return {"id": id_, "txn_date": d, "txn_type": kind, "amount": amount, "units": units}


# Two SIPs (100 units at NAV 10, then 100 units at NAV 12), then a sale of 150 units at NAV 15.
SIP1 = txn(1, date(2024, 1, 5), "sip", 1000.0, 100.0)
SIP2 = txn(2, date(2024, 2, 5), "sip", 1200.0, 100.0)
SELL = txn(3, date(2024, 3, 5), "sell", 2250.0, 150.0)


def test_fifo_buys_only():
    h = portfolio.fifo([SIP1, SIP2])
    assert h["units"] == 200.0
    assert h["invested"] == 2200.0
    assert h["realised"] == 0.0
    assert h["total_bought"] == 2200.0
    assert h["avg_cost_nav"] == 11.0


def test_fifo_partial_sell_removes_oldest_units_first():
    h = portfolio.fifo([SIP1, SIP2, SELL])
    # Sold 100 units from the first lot (cost 10 each) and 50 from the second (cost 12 each): cost 1,600.
    assert h["units"] == 50.0
    assert h["invested"] == 600.0          # 50 units left, all from the NAV-12 lot
    assert h["avg_cost_nav"] == 12.0
    assert h["realised"] == 650.0          # proceeds 2,250 - cost 1,600
    assert h["total_bought"] == 2200.0


def test_fifo_sorts_by_date_whatever_the_input_order():
    assert portfolio.fifo([SELL, SIP2, SIP1]) == portfolio.fifo([SIP1, SIP2, SELL])


def test_fifo_same_day_uses_id_order():
    buy = txn(10, date(2024, 1, 5), "buy", 500.0, 50.0)
    sell = txn(11, date(2024, 1, 5), "sell", 300.0, 25.0)
    h = portfolio.fifo([sell, buy])
    assert h["units"] == 25.0
    assert h["realised"] == 50.0           # 300 - 25 x 10


def test_fifo_accepts_numeric_strings():
    h = portfolio.fifo([txn(1, date(2024, 1, 5), "buy", "1000.00", "80.0000")])
    assert h["units"] == 80.0
    assert h["avg_cost_nav"] == 12.5


def test_fifo_selling_more_than_held_never_goes_negative():
    # The API refuses this (api_transactions.prepare); the maths must still stay sane.
    oversell = txn(3, date(2024, 3, 5), "sell", 3750.0, 250.0)
    h = portfolio.fifo([SIP1, SIP2, oversell])
    assert h["units"] == 0.0
    assert h["invested"] == 0.0
    assert h["avg_cost_nav"] is None


def test_fifo_empty():
    h = portfolio.fifo([])
    assert h == {"units": 0.0, "invested": 0.0, "realised": 0.0, "total_bought": 0.0, "avg_cost_nav": None}


def test_cash_flow_signs():
    flows = portfolio.cash_flows([SIP1, txn(9, date(2024, 1, 20), "buy", 5000.0, 400.0), SELL])
    assert flows == [(date(2024, 1, 5), -1000.0), (date(2024, 1, 20), -5000.0), (date(2024, 3, 5), 2250.0)]


def test_allocation_percentages():
    rows = [
        {"asset_class": "equity", "value": 500.0},
        {"asset_class": "equity", "value": 200.0},
        {"asset_class": "debt", "value": 200.0},
        {"asset_class": "international", "value": 100.0},
    ]
    assert portfolio.allocation(rows) == {"equity": 70.0, "debt": 20.0, "international": 10.0}


def test_allocation_rounds_to_two_decimals():
    rows = [{"asset_class": "equity", "value": 1.0}, {"asset_class": "debt", "value": 2.0}]
    a = portfolio.allocation(rows)
    assert a == {"equity": 33.33, "debt": 66.67, "international": 0.0}


def test_allocation_with_no_value():
    assert portfolio.allocation([]) == {"equity": 0.0, "debt": 0.0, "international": 0.0}
    assert portfolio.allocation([{"asset_class": "equity", "value": 0.0}]) == {"equity": 0.0, "debt": 0.0,
                                                                              "international": 0.0}


def test_xirr_of_fifo_example_is_positive():
    from app.services.xirr import xirr

    flows = portfolio.cash_flows([SIP1, SIP2]) + [(date(2025, 1, 5), 2600.0)]
    rate = xirr(flows)
    assert rate is not None and 0 < rate < 1
