"""AMFI NAVAll.txt parser (the official fallback NAV source)."""
from datetime import date

from app.services import amfi

# Real lines from NAVAll.txt (9 Oct 2026), with the current 8-column header, CRLF endings,
# fund-house and category section lines, and a suspended scheme with "N.A.".
SAMPLE_8_COL = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\r\n"
    " \r\n"
    "Open Ended Schemes(Debt Scheme - Short Duration Fund)\r\n"
    " \r\n"
    "ICICI Prudential Mutual Fund\r\n"
    " \r\n"
    "120754;INF109K013N3;-;ICICI Prudential Short Term Fund;Direct Plan;Growth;70.7746;09-Oct-2026\r\n"
    "999001;INF000000001;-;Some Suspended Fund;Direct Plan;Growth;N.A.;09-Oct-2026\r\n"
    "Open Ended Schemes(Equity Scheme - Mid Cap Fund)\r\n"
    "HDFC Mutual Fund\r\n"
    "118989;INF179K01XQ0;-;HDFC Mid Cap Fund;Direct Plan;Growth Option;219.816;09-Oct-2026\r\n"
    "garbage line;with;semicolons\r\n"
)

# The older 6-column layout described in AMFI's documentation.
SAMPLE_6_COL = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n"
    "Open Ended Schemes(Other Scheme - Index Funds)\n"
    "UTI Mutual Fund\n"
    "120716;INF789F01XA0;-;UTI Nifty 50 Index Fund - Direct Plan - Growth;158.5759;09-Oct-2026\n"
)


def test_parses_current_eight_column_file():
    data = amfi.parse(SAMPLE_8_COL)
    assert set(data) == {120754, 118989}
    row = data[120754]
    assert row["nav"] == 70.7746
    assert row["nav_date"] == date(2026, 10, 9)
    assert row["scheme_name"] == "ICICI Prudential Short Term Fund"
    assert row["plan"] == "Direct Plan"
    assert row["option"] == "Growth"
    assert row["isin_growth"] == "INF109K013N3"
    assert row["category"] == "Debt Scheme - Short Duration Fund"
    assert row["fund_house"] == "ICICI Prudential Mutual Fund"


def test_section_lines_apply_to_following_rows():
    row = amfi.parse(SAMPLE_8_COL)[118989]
    assert row["category"] == "Equity Scheme - Mid Cap Fund"
    assert row["fund_house"] == "HDFC Mutual Fund"
    assert row["nav"] == 219.816


def test_skips_suspended_and_garbage_rows():
    data = amfi.parse(SAMPLE_8_COL)
    assert 999001 not in data  # "N.A." NAV is never guessed


def test_parses_older_six_column_file():
    data = amfi.parse(SAMPLE_6_COL)
    row = data[120716]
    assert row["nav"] == 158.5759
    assert row["nav_date"] == date(2026, 10, 9)
    assert row["plan"] is None
    assert row["category"] == "Other Scheme - Index Funds"


def test_empty_or_missing_text():
    assert amfi.parse("") == {}
    assert amfi.parse(None) == {}


def test_date_formats():
    assert amfi._parse_date("09-Oct-2026") == date(2026, 10, 9)
    assert amfi._parse_date("09-10-2026") == date(2026, 10, 9)
    assert amfi._parse_date("not a date") is None
