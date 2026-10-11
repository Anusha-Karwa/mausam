"""Category -> asset class + benchmark mapping (app/services/benchmarks.py), using real mfapi spellings."""
import pytest

from app.services.benchmarks import classify, normalise_category, peer_name_filter


def test_normalise_category():
    assert normalise_category("Equity Scheme - Mid Cap Fund") == "mid cap fund"
    assert normalise_category("Overseas Fund of Funds - Fund of Funds investing overseas") == "fund of funds investing overseas"
    assert normalise_category("  Other Scheme -   Index Funds ") == "index funds"
    assert normalise_category(None) == ""


def test_mid_cap():
    c = classify("Equity Scheme - Mid Cap Fund", "HDFC Mid Cap Fund - Growth Option - Direct Plan")
    assert (c["asset_class"], c["benchmark_key"], c["peer_group"]) == ("equity", "MIDCAP150", "Mid cap fund")
    assert c["needs_confirmation"] is False


def test_small_cap_uses_smallcap_250():
    c = classify("Equity Schemes - Small Cap Fund", "Nippon India Small Cap Fund - Direct Plan Growth Plan - Growth Option")
    assert (c["asset_class"], c["benchmark_key"], c["peer_group"]) == ("equity", "SMALLCAP250", "Small cap fund")
    assert "Smallcap 250" in c["why"]


def test_large_cap():
    c = classify("Equity Scheme - Large Cap Fund", "Some Bluechip Fund - Direct Plan - Growth")
    assert (c["benchmark_key"], c["peer_group"]) == ("NIFTY50", "Large cap fund")


@pytest.mark.parametrize("category, name", [
    ("Debt Scheme - Short Duration Fund", "ICICI Prudential Short Term Fund - Direct Plan - Growth Option"),
    ("Income/Debt Oriented Schemes - Short Term Fund", "ICICI Prudential Short Term Fund - Direct Plan - Growth Option"),
])
def test_short_duration_debt(category, name):
    c = classify(category, name)
    assert c["asset_class"] == "debt"
    assert c["benchmark_key"] == "CATEGORY_ONLY"
    assert c["peer_group"] == "Short duration fund"
    assert c["needs_confirmation"] is False


def test_other_debt_category_keeps_its_own_peer_group():
    c = classify("Debt Scheme - Liquid Fund", "Some Liquid Fund - Direct Plan - Growth")
    assert c["asset_class"] == "debt"
    assert c["peer_group"] == "Liquid fund"


def test_overseas_fof_with_nasdaq_in_name():
    c = classify("Overseas Fund of Funds - Fund of Funds investing overseas",
                 "Invesco India - Invesco EQQQ NASDAQ-100 ETF FoF - Direct Plan - Growth")
    assert (c["asset_class"], c["benchmark_key"]) == ("international", "NASDAQ100")
    assert c["peer_group"] == "International fund of funds"


def test_other_scheme_fof_overseas_defaults_to_sp500():
    c = classify("Other Scheme - FoF Overseas", "PGIM India Global Equity Opportunities Fund of Fund - Direct Plan - Growth")
    assert (c["asset_class"], c["benchmark_key"]) == ("international", "SP500")


def test_nifty_50_index_fund():
    c = classify("Other Scheme - Index Funds", "UTI Nifty 50 Index Fund - Direct Plan - Growth")
    assert (c["asset_class"], c["benchmark_key"]) == ("equity", "NIFTY50")
    assert c["peer_group"] == "Index funds · Nifty 50"
    assert c["needs_confirmation"] is False


def test_nifty_next_50_is_not_nifty_50():
    c = classify("Other Scheme - Index Funds", "UTI Nifty Next 50 Index Fund - Direct Plan - Growth")
    assert c["peer_group"] == "Index funds · Nifty Next 50"
    assert c["peer_group"] != "Index funds · Nifty 50"
    # We have no Nifty Next 50 series, so the Nifty 50 is only a stand-in and must be confirmed.
    assert c["needs_confirmation"] is True


def test_index_funds_for_other_indices():
    assert classify("Other Scheme - Index Funds", "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan")["benchmark_key"] == "MIDCAP150"
    assert classify("Other Scheme - Index Funds", "Motilal Oswal Nifty Smallcap 250 Index Fund- Direct Plan")["benchmark_key"] == "SMALLCAP250"
    sp = classify("Other Scheme - Index Funds", "Motilal Oswal S&P 500 Index Fund - Direct Plan Growth")
    assert (sp["asset_class"], sp["benchmark_key"]) == ("international", "SP500")


def test_index_fund_with_unknown_index_needs_confirmation():
    c = classify("Other Scheme - Index Funds", "Some Fancy Factor Index Fund - Direct Plan - Growth")
    assert c["benchmark_key"] == "NIFTY50"
    assert c["needs_confirmation"] is True
    assert c["peer_group"] == "Index funds · Other"


@pytest.mark.parametrize("category", ["Solution Oriented Scheme - Retirement Fund", "", None])
def test_unknown_category_needs_confirmation(category):
    c = classify(category, "Some Fund - Direct Plan - Growth")
    assert c["asset_class"] == "equity"
    assert c["benchmark_key"] == "NIFTY50"
    assert c["needs_confirmation"] is True


def test_every_answer_has_a_plain_reason():
    for cat, name in [("Equity Scheme - Mid Cap Fund", "x"), ("Debt Scheme - Short Duration Fund", "x"),
                      ("Other Scheme - FoF Overseas", "x"), ("Other Scheme - Index Funds", "Nifty 50 Index Fund"),
                      ("Weird", "x")]:
        assert classify(cat, name)["why"]


# ---------- peer search name filter ----------

@pytest.mark.parametrize("group, name, expected", [
    ("Mid cap fund", "HDFC Mid Cap Fund - Growth Option - Direct Plan", True),
    ("Mid cap fund", "Kotak Emerging Equity Fund Midcap - Direct", True),
    ("Mid cap fund", "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan", False),   # index fund
    ("Mid cap fund", "HDFC Large and Mid Cap Fund - Direct Growth", False),              # different category
    ("Small cap fund", "Nippon India Small Cap Fund - Direct Plan Growth", True),
    ("Small cap fund", "Nippon India Nifty Smallcap 250 Index Fund - Direct", False),
    ("Short duration fund", "ICICI Prudential Short Term Fund - Direct Plan - Growth", True),
    ("Short duration fund", "HDFC Short Duration Debt Fund - Direct Growth", True),
    ("Short duration fund", "ICICI Prudential Ultra Short Term Fund - Direct Plan - Growth", False),
    ("Index funds · Nifty 50", "HDFC Index Fund-NIFTY 50 Plan - Direct Plan", True),
    ("Index funds · Nifty 50", "UTI Nifty Next 50 Index Fund - Direct Plan - Growth", False),
    ("Index funds · Nifty 50", "Quant Nifty 50 Active Fund", False),                     # not an index fund/ETF
    ("International fund of funds", "Motilal Oswal Nasdaq 100 FoF - Direct Plan", True),
    ("International fund of funds", "HDFC Mid Cap Fund - Direct Plan", False),
    ("Large cap fund", "ICICI Prudential Bluechip Fund - Direct Plan - Growth", True),
    ("Large cap fund", "Some Large & Mid Cap Fund - Direct", False),
])
def test_peer_name_filter(group, name, expected):
    assert peer_name_filter(group)(name) is expected
