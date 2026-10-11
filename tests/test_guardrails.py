"""Guardrails on every Gemini output (app/services/guardrails.py).

`facts` and `valid_report` fixtures come from tests/conftest.py (shaped like spec section 11.2).
"""
import pytest

from app.services import guardrails as g


# ---------- number check ----------

def test_numbers_present_in_facts_are_ok(facts):
    assert g.check_numbers("Your mid-cap fund fell 1.2% this week, 0.4 points ahead of its benchmark.", facts) == []


def test_invented_number_is_flagged(facts):
    bad = g.check_numbers("Your mid-cap fund fell 5.3% this week.", facts)
    assert bad == ["5.3"]


def test_rounding_to_fewer_decimals_is_allowed(facts):
    # The fact is 9.13; "9%" and "9.1%" are honest roundings of it.
    assert g.check_numbers("It is up 9% this year.", facts) == []
    assert g.check_numbers("It is up 9.1% this year.", facts) == []
    assert g.check_numbers("It is up 9.13% this year.", facts) == []
    assert g.check_numbers("It is up 9.4% this year.", facts) == ["9.4"]


def test_negative_signs_do_not_matter(facts):
    assert g.check_numbers("The gap was -1.8 points, or −1.8 points.", facts) == []


def test_bare_small_integers_dates_and_index_names_are_ok(facts):
    text = ("For 3 weeks in a row, up to 9 Oct 2026, the Nifty 50, Midcap 150, Smallcap 250, "
            "Nasdaq 100 and S&P 500 moved.")
    assert g.check_numbers(text, facts) == []


def test_bare_large_integer_not_in_facts_is_flagged(facts):
    assert g.check_numbers("You hold 45 funds.", facts) == ["45"]


def test_rupee_amounts_must_match_facts(facts):
    assert g.check_numbers("Your SIP of ₹3,000 went in.", facts) == ["₹3,000"]
    facts["sip_amount_rupees"] = 3000
    assert g.check_numbers("Your SIP of ₹3,000 went in.", facts) == []


def test_indian_digit_grouping_is_parsed(facts):
    facts["value_rupees"] = 123456
    assert g.check_numbers("Your funds are worth ₹1,23,456 today.", facts) == []
    nums = g.numbers_in_text("₹1,23,456")
    assert nums[0]["value"] == 123456.0
    assert nums[0]["unit"] is True


def test_points_and_percent_are_units():
    found = g.numbers_in_text("up 1.25% and 0.4 points, 2 pp, 7 percent")
    assert [(n["value"], n["unit"]) for n in found] == [(1.25, True), (0.4, True), (2.0, True), (7.0, True)]
    assert found[0]["decimals"] == 2


def test_allowed_numbers_walks_nested_facts(facts):
    allowed = g.allowed_numbers(facts)
    for n in (1.8, 4.1, 68.2, 9.13, 0.7, 2026.0, 150.0, 3.0, 7.0):
        assert n in allowed
    assert 0.0 not in g.allowed_numbers({"flag": False})  # booleans are not numbers


# ---------- banned phrases (reports) ----------

@pytest.mark.parametrize("text, label", [
    ("You should buy more of this fund.", "you should buy"),
    ("A sell-off hit small companies.", "sell"),
    ("Returns are guaranteed.", "guaranteed"),
    ("Consider a switch to a large-cap fund.", "switch"),
    ("You could redeem your units.", "redeem"),
    ("It will definitely recover.", "will definitely"),
    ("This is a sure-shot winner.", "sure-shot"),
    ("Our target price is 200.", "target price"),
    ("Please stop your SIP.", "stop your SIP"),
])
def test_banned_in_report(text, label):
    assert label in g.banned_in_report(text)


def test_calm_report_language_passes():
    assert g.banned_in_report("Small companies fell this week, and your fund slipped with them.") == []
    # "fell" is not "sell"; words merely containing "sell" at a non-word-start are fine
    assert g.banned_in_report("The bestseller list is unrelated.") == []


# ---------- chat mode ----------

def test_chat_allows_explaining_concepts():
    text = "Selling means redeeming units: the fund house pays you units × NAV on that day."
    assert g.banned_in_chat(text) == []
    # The same sentence would not be allowed in a report.
    assert g.banned_in_report(text) != []


@pytest.mark.parametrize("text", [
    "You should sell this fund.",
    "I recommend increasing your SIP.",
    "We suggest a different fund.",
    "Sell it now before it falls further.",
    "Stop your SIP for a while.",
    "This fund will definitely beat the market.",
])
def test_chat_blocks_instructions(text):
    assert g.banned_in_chat(text) != []


def test_chat_guarantee_only_allowed_when_negated():
    assert g.banned_in_chat("Mutual fund returns are not guaranteed.") == []
    assert g.banned_in_chat("No one can guarantee returns, and there is no guarantee here.") == []
    assert g.banned_in_chat("Your returns are guaranteed.") == ["guarantee"]


def test_validate_chat_checks_numbers_and_advice():
    ctx = {"portfolio": {"value_rupees": 125000.0, "gain_pct": 12.4}}
    assert g.validate_chat("Your funds are worth ₹1,25,000, up 12.4% overall.", ctx) == []
    errors = g.validate_chat("Your funds are worth ₹1,30,000.", ctx)
    assert any("Numbers not found" in e for e in errors)
    errors = g.validate_chat("You should sell your small-cap fund.", ctx)
    assert any("Advice-like" in e for e in errors)


def test_validate_short_text_limits(facts):
    assert g.validate_short_text("", facts, max_sentences=2, max_words=20) == ["Empty text"]
    errs = g.validate_short_text("One. Two. Three.", facts, max_sentences=2, max_words=20)
    assert "Use at most 2 sentences" in errs
    errs = g.validate_short_text("word " * 30, facts, max_sentences=2, max_words=20)
    assert "Use at most 20 words" in errs
    errs = g.validate_short_text("This range is a rough guide.", facts, max_sentences=2, max_words=20,
                                 must_include=["estimate, not a promise"])
    assert any("Must include" in e for e in errs)


# ---------- sentence splitting ----------

@pytest.mark.parametrize("text, n", [
    ("Returns were 9.13% this year.", 1),
    ("The NAV rose 1.25% today. It was 3.5% last week!", 2),
    ("It fell 1.2%. Its benchmark fell 1.6%. News explains it.", 3),
    ("Is it good? Yes, it is.", 2),
    ("A fund (e.g. an index fund) is cheap.", 1),
    ("", 0),
])
def test_split_sentences(text, n):
    assert len(g.split_sentences(text)) == n


# ---------- full report validation ----------

def test_valid_report_passes(valid_report, facts):
    assert g.validate_report(valid_report, facts) == []


def test_not_an_object(facts):
    assert g.validate_report(["not", "a", "dict"], facts) == ["Report is not a JSON object"]


def test_missing_field(valid_report, facts):
    del valid_report["verdict_reason"]
    assert g.validate_report(valid_report, facts) == ["Missing field: verdict_reason"]


def test_wrong_verdict(valid_report, facts):
    valid_report["verdict"] = "No action needed"
    errors = g.validate_report(valid_report, facts)
    assert errors == ["Verdict must be exactly 'One fund worth watching' (copied from facts)"]


def test_made_up_verdict(valid_report, facts):
    valid_report["verdict"] = "Buy the dip"
    errors = g.validate_report(valid_report, facts)
    assert any("not one of the three allowed verdicts" in e for e in errors)


def test_two_sentences_for_a_fund(valid_report, facts):
    valid_report["funds"][0]["summary"] = valid_report["funds"][0]["summary"][:2]
    errors = g.validate_report(valid_report, facts)
    assert errors == ["Mid-cap fund: summary must be exactly 3 sentences"]


def test_two_sentences_squeezed_into_one_item(valid_report, facts):
    valid_report["funds"][0]["summary"][2] = "The move matched the market. Nothing else happened."
    errors = g.validate_report(valid_report, facts)
    assert errors == ["Mid-cap fund: item 3 must be a single sentence"]


def test_unknown_news_id(valid_report, facts):
    valid_report["funds"][1]["news_ids_used"] = [7, 99]
    errors = g.validate_report(valid_report, facts)
    assert errors == ["Small-cap fund: cites news id 99 which was not provided"]


def test_missing_fund(valid_report, facts):
    valid_report["funds"] = valid_report["funds"][:1]
    errors = g.validate_report(valid_report, facts)
    assert errors == ["Missing summaries for: Small-cap fund"]


def test_invented_number_in_report(valid_report, facts):
    valid_report["funds"][0]["summary"][0] = "Your mid-cap fund fell 5.3% this week but is up 0.8% over the month."
    errors = g.validate_report(valid_report, facts)
    assert errors == ["Numbers not found in the facts: 5.3"]


def test_banned_word_in_report(valid_report, facts):
    valid_report["market_paragraph"] = "A sell-off hit small companies this week. The Nifty 50 fell 0.9%."
    errors = g.validate_report(valid_report, facts)
    assert errors == ["Banned words used: sell"]


def test_headline_too_long(valid_report, facts):
    valid_report["headline"] = " ".join(["word"] * 15)
    assert "Headline is longer than 14 words" in g.validate_report(valid_report, facts)


def test_sentence_too_long(valid_report, facts):
    valid_report["funds"][0]["summary"][2] = "The move " + "really " * 40 + "matched the market."
    assert "Mid-cap fund: sentence 3 is too long" in g.validate_report(valid_report, facts)
