"""Indian number formatting and other template filters (app/templating.py)."""
from datetime import date, datetime

import pytest

from app import templating as t

MINUS = "−"  # the typographic minus sign used in the UI


@pytest.mark.parametrize("n, expected", [
    (0, "0"),
    (999, "999"),
    (1000, "1,000"),
    (12345, "12,345"),
    (100000, "1,00,000"),
    (1234567, "12,34,567"),
    (12345678, "1,23,45,678"),
    (-1234567, "12,34,567"),   # sign is handled by inr(), not here
])
def test_indian_group(n, expected):
    assert t.indian_group(n) == expected


def test_inr_basic_and_negative():
    assert t.inr(1234567) == "₹12,34,567"
    assert t.inr(-2500) == f"{MINUS}₹2,500"
    assert t.inr(None) == "–"
    assert t.inr(1234.4) == "₹1,234"


def test_inr_signed():
    assert t.inr(2500, signed=True) == "+₹2,500"
    assert t.inr(-2500, signed=True) == f"{MINUS}₹2,500"
    assert t.inr(0, signed=True) == "+₹0"


def test_inr_with_decimals():
    assert t.inr(1234.5, decimals=2) == "₹1,234.50"
    assert t.inr(1234.56, decimals=2) == "₹1,234.56"
    assert t.inr(-150000.25, decimals=2) == f"{MINUS}₹1,50,000.25"
    # Rounding carries into the whole rupees (it used to print ₹1,234.00 here).
    assert t.inr(1234.999, decimals=2) == "₹1,235.00"


def test_pct_and_pts_markup():
    assert "+1.24%" in str(t.pct(1.2391))
    assert f"{MINUS}0.50%" in str(t.pct(-0.5))
    assert "▲" in str(t.pct(1.0)) and "▼" in str(t.pct(-1.0))   # never colour alone
    assert "Not enough history" in str(t.pct(None))
    assert f"{MINUS}1.80 pts" in str(t.pts(-1.8))


def test_dates():
    assert t.fmt_date(date(2026, 10, 9)) == "9 Oct 2026"
    assert t.fmt_date("2026-10-09") == "9 Oct 2026"
    assert t.fmt_date(datetime(2026, 10, 9, 18, 30), style="short") == "9 Oct"
    assert t.fmt_date(None) == "–"


def test_signed_class():
    assert t.signed_class(1) == "gain"
    assert t.signed_class(0) == "gain"
    assert t.signed_class(-0.01) == "loss"
    assert t.signed_class(None) == "muted"


def test_term_tooltip_uses_glossary():
    html = str(t.term("benchmark"))
    assert 'class="term"' in html and "data-tip=" in html
    assert str(t.term("not-a-term")) == "not-a-term"
