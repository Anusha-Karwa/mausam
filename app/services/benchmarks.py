"""Category -> asset class + default benchmark.

No free API tells us a fund's official benchmark, so we map from mfapi's
`scheme_category` (and the scheme name for index funds and FoFs). Meera can
override the benchmark on the Manage funds page.

mfapi spells categories inconsistently ("Equity Scheme - Mid Cap Fund",
"Equity Schemes - Small Cap Fund", "Overseas Fund of Funds - Fund of Funds
investing overseas", "Other Scheme - FoF Overseas"), so we normalise first.
"""
from __future__ import annotations

import re

BENCHMARK_LABELS = {
    "NIFTY50": "Nifty 50",
    "MIDCAP150": "Nifty Midcap 150",
    "SMALLCAP100": "Nifty Smallcap 100",
    "SMALLCAP250": "Nifty Smallcap 250",
    "SP500": "S&P 500 (in rupees)",
    "NASDAQ100": "Nasdaq 100 (in rupees)",
    "CATEGORY_ONLY": "Category average only",
}

# Benchmarks a fund can be compared against (shown in the override dropdown)
SELECTABLE = ["NIFTY50", "MIDCAP150", "SMALLCAP250", "SMALLCAP100", "SP500", "NASDAQ100", "CATEGORY_ONLY"]

ASSET_LABELS = {"equity": "Equity", "debt": "Debt", "international": "International"}


def normalise_category(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    if " - " in text:
        text = text.split(" - ", 1)[1]
    return re.sub(r"\s+", " ", text).strip()


def _is_overseas(cat: str, name: str) -> bool:
    return ("overseas" in cat or "international" in cat or "fof overseas" in cat
            or ("fund of fund" in cat and any(w in name for w in ("nasdaq", "us ", "u.s", "global", "world", "s&p"))))


def _index_tracked(name: str) -> str | None:
    n = name.lower()
    if re.search(r"nifty\s*50\b(?!\s*(value|equal|quality|momentum))", n) and "next 50" not in n:
        return "Nifty 50"
    if "midcap 150" in n:
        return "Nifty Midcap 150"
    if "smallcap 250" in n:
        return "Nifty Smallcap 250"
    if "sensex" in n:
        return "Sensex"
    if "nasdaq" in n:
        return "Nasdaq 100"
    if "s&p 500" in n:
        return "S&P 500"
    if "next 50" in n:
        return "Nifty Next 50"
    return None


def classify(scheme_category: str | None, scheme_name: str | None) -> dict:
    """Return asset_class, benchmark_key, peer_group, needs_confirmation and a plain 'why' line."""
    cat = normalise_category(scheme_category)
    raw = (scheme_category or "").lower()
    name = (scheme_name or "").lower()

    if _is_overseas(cat, name) or "fof overseas" in raw:
        key = "NASDAQ100" if ("nasdaq" in name or "technology" in name) else "SP500"
        return {
            "asset_class": "international", "benchmark_key": key, "peer_group": "International fund of funds",
            "needs_confirmation": False,
            "why": f"It invests abroad, so we compare it with the {BENCHMARK_LABELS[key]}, converted to rupees.",
        }
    if "debt scheme" in raw or "income" in raw or any(w in cat for w in ("duration", "short term", "gilt", "liquid", "money market", "corporate bond", "banking and psu", "credit risk", "overnight", "floater", "dynamic bond")):
        group = "Short duration fund" if ("short duration" in cat or "short term" in cat) else cat.capitalize() or "Debt fund"
        return {
            "asset_class": "debt", "benchmark_key": "CATEGORY_ONLY", "peer_group": group,
            "needs_confirmation": False,
            "why": "Official debt indices (from CRISIL) aren't free, so we compare it with the average of similar debt funds.",
        }
    if "index fund" in cat or "index funds" in raw or "etf" in cat:
        tracked = _index_tracked(name)
        mapped = {"Nifty 50": "NIFTY50", "Nifty Midcap 150": "MIDCAP150", "Nifty Smallcap 250": "SMALLCAP250",
                  "Nasdaq 100": "NASDAQ100", "S&P 500": "SP500"}.get(tracked or "")
        key = mapped or "NIFTY50"
        asset = "international" if key in ("NASDAQ100", "SP500") else "equity"
        if mapped:
            why = f"It's an index fund tracking the {tracked}, so that index is its benchmark."
        elif tracked:
            # e.g. Nifty Next 50 or Sensex: we don't store that index, so the Nifty 50 is only a stand-in.
            why = f"It tracks the {tracked}, which Mausam doesn't have daily data for. We used the Nifty 50; please confirm."
        else:
            why = "We couldn't tell which index this fund tracks. We used the Nifty 50; please confirm."
        return {
            "asset_class": asset, "benchmark_key": key, "peer_group": f"Index funds · {tracked or 'Other'}",
            "needs_confirmation": mapped is None,
            "why": why,
        }
    if "large cap" in cat and "mid" not in cat:
        return {"asset_class": "equity", "benchmark_key": "NIFTY50", "peer_group": "Large cap fund", "needs_confirmation": False,
                "why": "Large-cap funds buy India's biggest companies, which is what the Nifty 50 tracks."}
    if "mid cap" in cat and "large" not in cat:
        return {"asset_class": "equity", "benchmark_key": "MIDCAP150", "peer_group": "Mid cap fund", "needs_confirmation": False,
                "why": "Mid-cap funds buy medium-sized companies, which is what the Nifty Midcap 150 tracks."}
    if "small cap" in cat:
        return {"asset_class": "equity", "benchmark_key": "SMALLCAP250", "peer_group": "Small cap fund", "needs_confirmation": False,
                "why": ("Most small-cap funds officially use the Nifty Smallcap 250. Free daily history for the "
                        "Smallcap 100 isn't available, so we use the 250.")}
    group = cat.capitalize() if cat else "Other"
    return {"asset_class": "equity", "benchmark_key": "NIFTY50", "peer_group": group, "needs_confirmation": True,
            "why": "We didn't recognise this category, so we used the Nifty 50. Please confirm the benchmark."}


def peer_name_filter(peer_group: str):
    """Name test used to find candidate peers before confirming their category from meta."""
    g = peer_group.lower()
    if g.startswith("index funds · "):
        tracked = peer_group.split("· ", 1)[1].lower()
        return lambda n: tracked in n.lower() and ("index" in n.lower() or "etf" in n.lower())
    if g == "mid cap fund":
        return lambda n: bool(re.search(r"\bmid ?cap\b", n, re.I)) and not re.search(r"large|index|etf|150", n, re.I)
    if g == "small cap fund":
        return lambda n: bool(re.search(r"\bsmall ?cap\b", n, re.I)) and not re.search(r"index|etf|250|100", n, re.I)
    if g == "large cap fund":
        return lambda n: bool(re.search(r"\b(large ?cap|bluechip|blue chip|top 100)\b", n, re.I)) and not re.search(r"mid|index|etf", n, re.I)
    if g == "short duration fund":
        return lambda n: bool(re.search(r"short (term|duration)", n, re.I)) and not re.search(r"ultra|gilt|to short|debt fund series|fmp|interval", n, re.I)
    if g == "international fund of funds":
        return lambda n: bool(re.search(r"(fof|fund of fund)", n, re.I)) and bool(re.search(r"(nasdaq|u\.?s\.?|global|world|international|overseas|s&p|europe|emerging|china|japan|asia)", n, re.I))
    words = [w for w in re.findall(r"[a-z]+", g) if w not in ("fund", "funds", "scheme")]
    return lambda n: all(w in n.lower() for w in words[:2])
