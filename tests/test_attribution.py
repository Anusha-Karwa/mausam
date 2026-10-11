"""Market-wide vs fund-specific labels (app/services/attribution.py)."""
import pytest

from app.services import attribution
from app.services.attribution import attribute

EQUITY_BAND = 0.75


@pytest.mark.parametrize("fund, ref, expected", [
    (-1.2, -1.6, "market_wide"),     # same direction, gap 0.4 <= 0.75
    (-1.0, -0.25, "market_wide"),    # gap exactly at the band counts as market-wide
    (0.0, 0.5, "market_wide"),       # flat counts as the "up" side
    (-3.0, -1.0, "fund_specific"),   # gap 2.0, far above the band
    (2.5, 0.5, "fund_specific"),
    (1.0, -1.0, "fund_specific"),    # opposite directions, big gap
    (-2.0, -1.0, "mixed"),           # same direction, gap 1.0: slightly above 0.75 (<= 1.5 x band)
    (0.1, -0.2, "mixed"),            # opposite directions, both tiny (< 0.3%)
    (0.3, -0.2, "mixed"),            # opposite directions, gap 0.5 still inside the band
])
def test_equity_labels(fund, ref, expected):
    assert attribute(fund, ref, EQUITY_BAND) == expected


@pytest.mark.parametrize("fund, ref, stale", [
    (None, -1.0, False),
    (-1.0, None, False),
    (None, None, False),
    (-1.2, -1.6, True),   # numbers present but the NAV is stale
])
def test_insufficient_data(fund, ref, stale):
    assert attribute(fund, ref, EQUITY_BAND, stale=stale) == "insufficient_data"


def test_debt_band_is_much_tighter():
    band = attribution.band_for("debt", {})
    assert band == 0.1
    assert attribute(0.12, 0.08, band) == "market_wide"     # gap 0.04
    assert attribute(0.30, 0.08, band) == "fund_specific"   # gap 0.22 > 0.15


def test_band_for_each_asset_class_defaults():
    assert attribution.band_for("equity", {}) == 0.75
    assert attribution.band_for("debt", {}) == 0.1
    assert attribution.band_for("international", {}) == 1.0


def test_band_for_reads_threshold_keys():
    th = {"market_wide_band_pp": 0.5, "debt_market_wide_band_pp": 0.2, "intl_market_wide_band_pp": 1.5}
    assert attribution.band_for("equity", th) == 0.5
    assert attribution.band_for("debt", th) == 0.2
    assert attribution.band_for("international", th) == 1.5


def test_plain_language_labels():
    assert attribution.label("market_wide") == "Moved with the market"
    assert attribution.label("fund_specific") == "Fund-specific move"
    assert attribution.label("mixed") == "Partly market, partly fund"
    assert attribution.label("insufficient_data") == "Not enough data this week"
    assert attribution.label(None) == "Not enough data this week"
    assert attribution.label("something-else") == "Not enough data this week"
