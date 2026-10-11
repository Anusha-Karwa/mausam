"""mfapi.in parsing (app/services/mfapi.py): dd-mm-yyyy dates, string NAVs, junk rows, ordering."""
from datetime import date

import pytest

from app.services import mfapi


def test_parse_date_dd_mm_yyyy():
    assert mfapi.parse_date("09-10-2026") == date(2026, 10, 9)
    assert mfapi.parse_date(" 01-02-2026 ") == date(2026, 2, 1)   # 1 Feb, not 2 Jan
    with pytest.raises(ValueError):
        mfapi.parse_date("2026-10-09")


@pytest.mark.parametrize("raw, expected", [
    ("101.5", 101.5),
    ("  12.3456 ", 12.3456),
    ("1,234.50", 1234.5),
    (99, 99.0),
    ("N.A.", None),
    ("", None),
    (None, None),
    ("0", None),     # a zero NAV is junk
    ("-5", None),
])
def test_parse_nav(raw, expected):
    assert mfapi.parse_nav(raw) == expected


def test_parse_rows_newest_first_input_becomes_oldest_first():
    data = [  # mfapi returns newest first, NAVs as strings
        {"date": "10-10-2026", "nav": "101.50000"},
        {"date": "09-10-2026", "nav": "100.25000"},
        {"date": "08-10-2026", "nav": "99.00000"},
    ]
    assert mfapi.parse_rows(data) == [
        (date(2026, 10, 8), 99.0),
        (date(2026, 10, 9), 100.25),
        (date(2026, 10, 10), 101.5),
    ]


def test_parse_rows_skips_bad_rows():
    data = [
        {"date": "10-10-2026", "nav": "101.5"},
        {"date": "2026-10-09", "nav": "100"},     # wrong date format
        {"date": "31-02-2026", "nav": "100"},     # impossible date
        {"date": "07-10-2026", "nav": "N.A."},    # no NAV
        {"nav": "5"},                             # no date
        {"date": None, "nav": "5"},               # null date
        {"date": "06-10-2026", "nav": "0"},       # zero NAV
        "not a row",                              # not even a dict
        {"date": "05-10-2026", "nav": "1,234.5"},
    ]
    assert mfapi.parse_rows(data) == [(date(2026, 10, 5), 1234.5), (date(2026, 10, 10), 101.5)]


def test_parse_rows_duplicate_dates_keep_one_value():
    data = [{"date": "09-10-2026", "nav": "100"}, {"date": "09-10-2026", "nav": "100.5"}]
    assert mfapi.parse_rows(data) == [(date(2026, 10, 9), 100.5)]


def test_parse_rows_empty():
    assert mfapi.parse_rows([]) == []
    assert mfapi.parse_rows(None) == []
