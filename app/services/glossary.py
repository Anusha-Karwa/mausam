"""Plain-language glossary. Source for tooltips, the Learn page, chat context and daily tips."""
from __future__ import annotations

GLOSSARY: dict[str, dict[str, str]] = {
    "nav": {
        "term": "NAV",
        "short": "Net Asset Value: the price of one unit of a fund, published once a day after markets close.",
        "why": "Your fund's value is units you own × NAV. It is not a live price.",
    },
    "sip": {
        "term": "SIP",
        "short": "Systematic Investment Plan: investing a fixed amount in a fund every month.",
        "why": "Your SIPs buy more units when NAVs are low and fewer when they are high.",
    },
    "benchmark": {
        "term": "Benchmark",
        "short": "The market index a fund is compared against, like the Nifty 50 for large companies.",
        "why": "If your fund trails its benchmark for weeks, it may be a fund problem, not a market one.",
    },
    "category average": {
        "term": "Category average",
        "short": "The average return of similar funds. Mausam computes it from up to 12 named funds.",
        "why": "It shows whether your fund is keeping up with funds that do the same job.",
    },
    "xirr": {
        "term": "XIRR",
        "short": "Your yearly return, accounting for when each rupee went in.",
        "why": "With monthly SIPs, this is the fairest single number for how your money has grown.",
    },
    "cagr": {
        "term": "CAGR",
        "short": "Compound annual growth rate: the steady yearly growth that gets from start to end value.",
        "why": "3-year CAGR smooths out good and bad years into one comparable number.",
    },
    "ytd": {
        "term": "YTD",
        "short": "Year to date: the change since the last trading day of last year.",
        "why": "A quick check on how this calendar year is going.",
    },
    "allocation": {
        "term": "Allocation",
        "short": "How your money is split between equity, debt and international funds.",
        "why": "Your mix decides how bumpy the ride is. Mausam compares it with your target.",
    },
    "debt fund": {
        "term": "Debt fund",
        "short": "A fund that lends money to governments and companies and earns interest.",
        "why": "Debt funds move slowly and steady the rest of your portfolio.",
    },
    "fof": {
        "term": "FoF",
        "short": "Fund of funds: a fund that invests in another fund, often one abroad.",
        "why": "Your international FoF gives you a slice of US companies, measured in rupees.",
    },
    "expense ratio": {
        "term": "Expense ratio",
        "short": "The yearly fee a fund charges, taken out of its NAV.",
        "why": "Lower fees mean more of the return stays with you. It isn't in free data, so check the factsheet.",
    },
    "points": {
        "term": "Points (pp)",
        "short": "Percentage points: the gap between two percentages. 5% vs 3% is a 2-point gap.",
        "why": "Mausam says 'behind by 1.8 points' to show how far a fund is from its benchmark.",
    },
    "volatility": {
        "term": "Volatility",
        "short": "How much a fund's value swings up and down, as a yearly percentage.",
        "why": "Higher volatility means bigger swings both ways. Small-cap funds swing the most.",
    },
    "drawdown": {
        "term": "Drawdown",
        "short": "How far a fund has fallen from its highest point.",
        "why": "It shows the worst dips you would have sat through.",
    },
    "attribution": {
        "term": "Market-wide vs fund-specific",
        "short": "Whether a fund moved because the whole market moved, or because of something about the fund itself.",
        "why": "A market-wide dip usually needs no action. A fund-specific gap is worth watching.",
    },
    "index fund": {
        "term": "Index fund",
        "short": "A fund that simply copies an index, such as the Nifty 50, at low cost.",
        "why": "It should move almost exactly with its index. A big gap would be unusual.",
    },
    "large cap": {
        "term": "Large cap",
        "short": "India's 100 biggest listed companies.",
        "why": "They tend to be steadier than mid and small companies.",
    },
    "mid cap": {
        "term": "Mid cap",
        "short": "Companies ranked 101 to 250 by size.",
        "why": "They can grow faster than large caps but swing more.",
    },
    "small cap": {
        "term": "Small cap",
        "short": "Companies ranked 251 and below by size.",
        "why": "The biggest growth potential and the biggest swings in your portfolio.",
    },
    "direct plan": {
        "term": "Direct plan",
        "short": "A version of a fund bought without a distributor, so it has lower fees.",
        "why": "Mausam recommends direct-plan growth versions when adding funds.",
    },
    "forecast range": {
        "term": "Forecast range",
        "short": "A band where the NAV is likely to land, with an honest record of how often past bands were right.",
        "why": "It's an estimate, not a promise. Mausam never uses it to tell you what to do.",
    },
    "rebalancing": {
        "term": "Rebalancing",
        "short": "Bringing your mix back towards your target allocation.",
        "why": "A big drift from target is the one thing Mausam flags as worth reviewing.",
    },
}

TOOLTIP_KEYS = {k: v["short"] for k, v in GLOSSARY.items()}


def define(key: str) -> str:
    entry = GLOSSARY.get(key.lower())
    return entry["short"] if entry else ""


def as_list() -> list[dict]:
    return [{"key": k, **v} for k, v in sorted(GLOSSARY.items(), key=lambda kv: kv[1]["term"].lower())]


def compact() -> dict[str, str]:
    """Short dictionary for Gemini context."""
    return {v["term"]: v["short"] for v in GLOSSARY.values()}


EXPLAINERS = [
    {"slug": "mutual-fund", "title": "What is a mutual fund?",
     "body": ["A mutual fund pools money from many people and invests it in shares, bonds or other assets.",
              "You own units. The NAV is the price of one unit, worked out once a day after markets close.",
              "Your value is simply units × NAV. Your SIPs buy new units every month."]},
    {"slug": "index-vs-active", "title": "Index vs active funds",
     "body": ["An index fund copies an index such as the Nifty 50, so it should move almost exactly with it.",
              "An active fund has a manager who picks stocks, trying to beat its benchmark.",
              "That's why Mausam compares every fund with a benchmark: it shows whether the extra fees for an active fund are paying off."]},
    {"slug": "cap-sizes", "title": "Large, mid and small cap",
     "body": ["Companies are ranked by size. The top 100 are large caps, 101 to 250 are mid caps, and the rest are small caps.",
              "Smaller companies can grow faster but swing far more, in both directions.",
              "That's why a small-cap fund can fall 3% in a week when the Nifty 50 falls only 1%."]},
    {"slug": "debt-funds", "title": "Debt funds",
     "body": ["Debt funds lend money to the government and companies and earn interest.",
              "They move slowly: a 0.1% week is normal. Interest-rate news from the RBI matters most to them.",
              "There's no free official debt index, so Mausam compares your debt fund with the average of similar funds."]},
    {"slug": "international-fof", "title": "International funds of funds",
     "body": ["An international FoF invests your rupees in a fund abroad, often one that tracks US companies.",
              "Two things move it: the US market and the rupee-dollar rate. When the rupee weakens, your FoF gains a little extra.",
              "Mausam converts US index moves into rupees so the comparison is fair."]},
    {"slug": "why-benchmark", "title": "Why compare with a benchmark?",
     "body": ["A fund falling 2% sounds bad. But if its market fell 3%, the fund actually did better than its market.",
              "Absolute returns alone can't tell you that. The benchmark gives the context.",
              "Mausam shows the gap in 'points': +0.4 points means slightly ahead, −1.8 points means behind."]},
    {"slug": "market-vs-fund", "title": "Market-wide vs fund-specific moves",
     "body": ["If your fund and its benchmark moved together, the move was market-wide: the whole market moved, not your fund in particular.",
              "If the gap is larger than a small band, Mausam labels it fund-specific: something about the fund's own choices.",
              "One fund-specific week is normal. Several in a row is what puts a fund on the watch list."]},
    {"slug": "forecast-ranges", "title": "Why forecasts are ranges",
     "body": ["Nobody can predict next month's NAV exactly. Anyone who gives you a single number is guessing.",
              "Mausam's model gives a range and then checks itself: how often did past ranges contain the real NAV?",
              "For volatile equity funds the model often can't beat a simple 'stays the same' guess, and Mausam tells you so."]},
]
