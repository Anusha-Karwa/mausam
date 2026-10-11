"""News selection (app/services/news.py): dedupe, relevance filter, tags, week assignment."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import news


def item(title, url, hours_ago=0):
    return {"title": title, "url": url, "source": "Test",
            "published_at": datetime(2026, 10, 9, 12, tzinfo=timezone.utc) - timedelta(hours=hours_ago)}


def test_normalise_title():
    assert news.normalise_title("  Nifty ends HIGHER, banks gain!  ") == "nifty ends higher banks gain"
    assert len(news.normalise_title("x" * 200)) == 70
    assert news.normalise_title(None) == ""


def test_dedupe_by_normalised_title_and_needs_url():
    items = [
        item("Nifty ends higher as banks gain", "https://a.example/1"),
        item("NIFTY ends higher as banks gain!", "https://b.example/2"),   # same story, other outlet
        item("RBI holds repo rate", None),                                 # no link: can't be shown
        item("", "https://c.example/3"),                                   # empty title
        item("RBI holds repo rate", "https://d.example/4"),
    ]
    out = news.dedupe(items)
    assert [i["url"] for i in out] == ["https://a.example/1", "https://d.example/4"]


def test_select_drops_irrelevant_items():
    items = [
        item("Cricket team wins the final", "https://x.example/1"),
        item("Sensex climbs 500 points", "https://x.example/2"),
    ]
    out = news.select(items)
    assert [i["title"] for i in out] == ["Sensex climbs 500 points"]


def test_select_keeps_at_most_twelve_newest_first():
    items = [item(f"Sensex update number {i}", f"https://x.example/{i}", hours_ago=i) for i in range(20)]
    out = news.select(items)
    assert len(out) == 12
    times = [i["published_at"] for i in out]
    assert times == sorted(times, reverse=True)


def test_select_prefers_more_relevant_items():
    items = [item(f"Sensex update number {i}", f"https://x.example/{i}") for i in range(12)]
    items.append(item("RBI repo rate decision moves bond yields and the rupee", "https://x.example/rbi", hours_ago=48))
    out = news.select(items)
    assert "https://x.example/rbi" in [i["url"] for i in out]   # older, but scores higher on relevance


def test_select_adds_tags_with_macro_default():
    out = news.select([item("SEBI tightens rules for fund houses", "https://x.example/sebi")])
    assert out[0]["tags"] == ["macro"]


@pytest.mark.parametrize("title, tags", [
    ("RBI holds repo rate", ["debt"]),
    ("Nasdaq rallies as Fed signals pause", ["international"]),
    ("Crude oil prices jump", ["macro"]),
    ("Nifty ends higher as banks gain", ["equity"]),
    ("Smallcap stocks slide as inflation worries return", ["equity", "debt", "macro"]),
    ("Cricket team wins the final", []),
])
def test_tags_for(title, tags):
    assert news.tags_for(title) == tags


@pytest.mark.parametrize("d, week", [
    (date(2026, 10, 9), date(2026, 10, 9)),     # Friday -> its own week
    (date(2026, 10, 10), date(2026, 10, 16)),   # Saturday -> belongs to the NEXT Friday
    (date(2026, 10, 11), date(2026, 10, 16)),   # Sunday
    (date(2026, 10, 12), date(2026, 10, 16)),   # Monday
    (date(2026, 10, 8), date(2026, 10, 9)),     # Thursday
])
def test_week_of(d, week):
    assert week.weekday() == 4
    assert news.week_of(d) == week


def test_moneycontrol_and_newsapi_are_not_used():
    urls = " ".join(u for _, u in news.RSS_FEEDS)
    assert "moneycontrol" not in urls
    assert "newsapi" not in urls
