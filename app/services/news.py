"""Market news: GNews (primary) and RSS feeds (fallback).

We store and show only the headline, source, date, link and our own one-line
"why it matters". Article bodies are never republished.
NewsAPI.org is deliberately NOT used: its free plan only works on localhost.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

from .. import db
from ..config import config
from .http import BROWSER_UA, get, get_json
from .returns import last_friday

GNEWS = "https://gnews.io/api/v4"

WEEKLY_QUERIES = [
    "Nifty OR Sensex",
    "RBI OR repo rate OR inflation",
    "FII OR rupee OR crude",
    "US Fed OR Nasdaq",
]

# Verified 11 Oct 2026 (Moneycontrol's RSS feeds stopped updating in 2024, so they are not used).
RSS_FEEDS = [
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Business Standard Markets", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Economic Times Mutual Funds", "https://economictimes.indiatimes.com/mf/rssfeeds/359241701.cms"),
]

TAG_WORDS = {
    "equity": ["nifty", "sensex", "stock", "shares", "midcap", "mid-cap", "smallcap", "small-cap", "equity", "ipo", "market"],
    "debt": ["rbi", "repo", "bond", "yield", "interest rate", "rate cut", "rate hike", "gilt", "debt", "inflation", "liquidity"],
    "international": ["fed", "nasdaq", "wall street", "s&p", "dow", "us market", "dollar", "treasury", "global", "tariff"],
    "macro": ["gdp", "inflation", "crude", "oil", "rupee", "fii", "fpi", "budget", "fiscal", "monsoon", "cpi", "wpi", "trade deficit"],
}

RELEVANT = sorted({w for words in TAG_WORDS.values() for w in words} | {"mutual fund", "sip", "amfi", "sebi"})


def normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (title or "").lower()).strip()[:70]


def tags_for(title: str) -> list[str]:
    low = (title or "").lower()
    return [tag for tag, words in TAG_WORDS.items() if any(w in low for w in words)]


def relevance(item: dict) -> int:
    low = (item.get("title") or "").lower()
    return sum(1 for w in RELEVANT if w in low)


def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = normalise_title(it.get("title", ""))
        if not key or key in seen or not it.get("url"):
            continue
        seen.add(key)
        out.append(it)
    return out


def select(items: list[dict], keep: int = 12) -> list[dict]:
    """Dedupe, drop irrelevant items, keep the most relevant (then newest) 8-12."""
    items = dedupe(items)
    scored = [it for it in items if relevance(it) > 0]
    scored.sort(key=lambda it: (relevance(it), it.get("published_at") or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    chosen = scored[:keep]
    chosen.sort(key=lambda it: it.get("published_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for it in chosen:
        it["tags"] = tags_for(it["title"]) or ["macro"]
    return chosen


def _parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_gnews(start: datetime, end: datetime, queries: list[str] | None = None, top: bool = False) -> list[dict]:
    if not config.GNEWS_API_KEY:
        raise RuntimeError("GNEWS_API_KEY not set")
    items = []
    calls = []
    if top:
        calls.append((f"{GNEWS}/top-headlines", {"category": "business", "lang": "en", "country": "in", "max": 10}))
    for q in queries or []:
        calls.append((f"{GNEWS}/search", {"q": q, "lang": "en", "country": "in", "max": 10,
                                           "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                           "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")}))
    errors = []
    for url, params in calls:
        params["apikey"] = config.GNEWS_API_KEY
        try:
            payload = get_json(url, params=params, timeout=10, retries=1)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:120])
            continue
        for a in payload.get("articles") or []:
            items.append({"title": html.unescape(a.get("title") or "").strip(), "url": a.get("url"),
                          "source": ((a.get("source") or {}).get("name")) or "GNews",
                          "published_at": _parse_iso(a.get("publishedAt"))})
    if not items and errors:
        raise RuntimeError("; ".join(errors))
    return items


def fetch_rss(start: datetime | None = None) -> list[dict]:
    import feedparser

    items = []
    for source, url in RSS_FEEDS:
        try:
            resp = get(url, ua=BROWSER_UA, timeout=10, retries=1)
            feed = feedparser.parse(resp.content)
        except Exception:  # noqa: BLE001
            continue
        for e in feed.entries[:50]:
            published = None
            if getattr(e, "published_parsed", None):
                published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            if start and published and published < start:
                continue
            items.append({"title": html.unescape(e.get("title") or "").strip(), "url": e.get("link"), "source": source,
                          "published_at": published})
    return items


def gather(week_end: date, daily: bool = False) -> dict:
    """Fetch the week's news: GNews first, RSS if GNews fails or returns nothing."""
    end = datetime.combine(week_end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    start = end - timedelta(days=8)
    used, error = "gnews", None
    try:
        raw = fetch_gnews(start, min(end, datetime.now(timezone.utc)),
                          queries=[] if daily else WEEKLY_QUERIES, top=daily)
        if not raw:
            raise RuntimeError("GNews returned no articles")
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:200]
        used = "rss"
        raw = fetch_rss(start)
    items = select(raw)
    return {"items": items, "source": used, "error": error}


def store(items: list[dict], week_ending: date) -> list[dict]:
    """Insert (or refresh) news rows; returns rows with ids."""
    saved = []
    for it in items:
        row = db.one(
            """INSERT INTO news_items (week_ending, published_at, title, source, url, tags)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (url) DO UPDATE SET week_ending = EXCLUDED.week_ending, tags = EXCLUDED.tags
               RETURNING id, week_ending, published_at, title, source, url, tags, why_it_matters""",
            [week_ending, it.get("published_at"), it["title"][:400], it.get("source"), it["url"], it.get("tags") or []],
        )
        if row:
            saved.append(row)
    return saved


def for_week(week_ending: date, limit: int = 12) -> list[dict]:
    return db.query("SELECT * FROM news_items WHERE week_ending = %s ORDER BY published_at DESC NULLS LAST LIMIT %s",
                    [week_ending, limit])


def latest(limit: int = 3) -> list[dict]:
    return db.query("SELECT * FROM news_items ORDER BY published_at DESC NULLS LAST, id DESC LIMIT %s", [limit])


def week_of(d: date) -> date:
    """News published Sat-Fri belongs to the week ending on that Friday."""
    return last_friday(d) if d.weekday() == 4 else last_friday(d + timedelta(days=(4 - d.weekday()) % 7))
