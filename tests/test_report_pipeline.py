"""Weekly report generation with Gemini mocked (app/services/report.generate).

Paths covered: valid first time; invalid then valid (retry with the errors in the prompt);
invalid twice (deterministic fallback); Gemini error (fallback); no Gemini key (fallback).
"""
import copy

import pytest

from app.services import gemini, guardrails, report


class FakeGemini:
    """Stands in for gemini.generate_json: returns queued answers (or raises) and records prompts."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, system, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer), {"model": "gemini-test", "latency_ms": 5}


@pytest.fixture
def use_gemini(monkeypatch):
    def install(answers):
        fake = FakeGemini(answers)
        monkeypatch.setattr(gemini, "available", lambda: True)
        monkeypatch.setattr(gemini, "generate_json", fake)
        return fake
    return install


def test_valid_first_time(use_gemini, facts, decision, valid_report):
    fake = use_gemini([valid_report])
    result = report.generate(facts, decision)
    assert result["used_fallback"] is False
    assert result["report"] == valid_report
    assert result["model"] == "gemini-test"
    assert result["errors"] == []
    assert len(fake.prompts) == 1
    assert '"verdict": "One fund worth watching"' in fake.prompts[0]   # facts JSON is in the prompt


def test_invalid_then_valid_uses_the_retry(use_gemini, facts, decision, valid_report):
    bad = copy.deepcopy(valid_report)
    bad["verdict"] = "No action needed"
    fake = use_gemini([bad, valid_report])
    result = report.generate(facts, decision)
    assert result["used_fallback"] is False
    assert result["report"]["verdict"] == "One fund worth watching"
    assert len(fake.prompts) == 2
    # The second prompt carries the guardrail errors so Gemini can fix them.
    assert "Your previous answer failed these checks" in fake.prompts[1]
    assert "Verdict must be exactly 'One fund worth watching'" in fake.prompts[1]
    assert len(result["errors"]) == 1 and result["errors"][0].startswith("attempt 1:")


def test_invalid_twice_falls_back_to_template(use_gemini, facts, decision, valid_report):
    bad = copy.deepcopy(valid_report)
    bad["funds"][0]["summary"][0] = "Your mid-cap fund fell 5.3% this week but is up 0.8% over the month."
    fake = use_gemini([bad, bad])
    result = report.generate(facts, decision)
    assert len(fake.prompts) == 2
    assert result["used_fallback"] is True
    assert result["model"] == "fallback-template"
    assert len(result["errors"]) == 2
    assert all("5.3" in e for e in result["errors"])
    rep = result["report"]
    assert rep["verdict"] == facts["verdict"]
    assert [f["name"] for f in rep["funds"]] == ["Mid-cap fund", "Small-cap fund"]
    # The fallback must itself pass every guardrail.
    assert guardrails.validate_report(rep, facts) == []


def test_gemini_error_falls_back(use_gemini, facts, decision):
    fake = use_gemini([gemini.GeminiError("HTTP 404: model not found")])
    result = report.generate(facts, decision)
    assert len(fake.prompts) == 1           # no second attempt after a hard error
    assert result["used_fallback"] is True
    assert result["errors"] == ["attempt 1: HTTP 404: model not found"]
    assert guardrails.validate_report(result["report"], facts) == []


def test_no_gemini_key_uses_template_without_calling(monkeypatch, facts, decision):
    def must_not_be_called(*a, **k):
        raise AssertionError("Gemini should not be called without a key")

    monkeypatch.setattr(gemini, "available", lambda: False)
    monkeypatch.setattr(gemini, "generate_json", must_not_be_called)
    result = report.generate(facts, decision)
    assert result["used_fallback"] is True
    assert result["errors"] == []


def test_fallback_report_for_each_verdict_passes_guardrails(facts, decision):
    from app.services import verdict as v

    # Clear skies
    calm = copy.deepcopy(facts)
    calm["verdict"] = v.NO_ACTION
    calm["verdict_facts"] = {"funds_lagging_this_week": 1, "allocation_drift_pp": 1.8}
    calm_decision = {"verdict": v.NO_ACTION, "watch": [], "lagging": [], "drift_pp": 1.8, "drift_class": "equity",
                     "facts": calm["verdict_facts"]}
    rep = report.fallback_report(calm, calm_decision)
    assert rep["headline"].startswith("Clear skies")
    assert guardrails.validate_report(rep, calm) == []

    # Storm watch (allocation drift)
    storm = copy.deepcopy(facts)
    storm["verdict"] = v.REVIEW
    storm["verdict_facts"] = {"allocation_drift_pp": 12.5, "drift_asset_class": "equity", "drift_limit_pp": 10.0,
                              "funds_to_watch": []}
    storm_decision = {"verdict": v.REVIEW, "watch": [], "lagging": [], "drift_pp": 12.5, "drift_class": "equity",
                      "facts": storm["verdict_facts"]}
    rep = report.fallback_report(storm, storm_decision)
    assert rep["headline"].startswith("Storm watch")
    assert "12.50 points" in rep["verdict_reason"]
    assert guardrails.validate_report(rep, storm) == []

    # Passing clouds
    rep = report.fallback_report(facts, decision)
    assert rep["headline"].startswith("Passing clouds")
    assert "3 weeks in a row" in rep["verdict_reason"]


def test_fallback_market_paragraph(facts):
    text = report.fallback_macro(facts["indices"])
    assert text.startswith("This week the Nifty 50 fell 0.90%")
    assert "Smallcap 100 fell 2.40%" in text
    assert "Abroad, the S&P 500 in rupees rose 0.70%." in text
    assert report.fallback_macro({}) == "Headlines below give the main news of the week."


def _fund(**over):
    base = {"name": "Debt fund", "ret_1w": None, "ret_1m": None, "bench_ret_1w": None, "delta_bench_1w_pp": None,
            "cat_ret_1w": None, "delta_cat_1w_pp": None, "attribution": "insufficient_data"}
    base.update(over)
    return base


def test_fallback_sentences_all_missing():
    s = report.fallback_sentences(_fund())
    assert s == [
        "Debt fund has no fresh weekly figure this week.",
        "We could not compare it with its benchmark or category this week.",
        "There was not enough fresh data to explain the move this week.",
    ]


def test_fallback_sentences_week_only_and_category_comparison():
    s = report.fallback_sentences(_fund(ret_1w=0.12, cat_ret_1w=0.1, delta_cat_1w_pp=0.02, attribution="market_wide"))
    assert s[0] == "Debt fund rose 0.12% this week."
    assert s[1] == "Similar funds rose 0.10% on average, so it finished 0.02 points ahead of its category."
    assert "market-wide" in s[2]


def test_fallback_sentences_benchmark_comparison():
    s = report.fallback_sentences(_fund(name="Small-cap fund", ret_1w=-4.2, ret_1m=-6.5, bench_ret_1w=-2.4,
                                        delta_bench_1w_pp=-1.8, attribution="fund_specific"))
    assert s[0] == "Small-cap fund fell 4.20% this week and fell 6.50% over the past month."
    assert s[1] == "Its benchmark fell 2.40%, so the fund finished 1.80 points behind it."
    assert "specific to this fund" in s[2]
    for sentence in s:
        assert len(guardrails.split_sentences(sentence)) == 1
