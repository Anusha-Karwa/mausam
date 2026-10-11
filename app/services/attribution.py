"""Was this week's move market-wide or specific to the fund?

We compare the fund's weekly return with its benchmark (or, for debt funds,
the category average). `band` is how close (in points) counts as "moved with
the market"; it differs by asset class because debt funds move far less.
"""
from __future__ import annotations

LABELS = {
    "market_wide": "Moved with the market",
    "fund_specific": "Fund-specific move",
    "mixed": "Partly market, partly fund",
    "insufficient_data": "Not enough data this week",
}

TINY_MOVE = 0.3  # percent


def band_for(asset_class: str, thresholds: dict) -> float:
    if asset_class == "debt":
        return float(thresholds.get("debt_market_wide_band_pp", 0.1))
    if asset_class == "international":
        return float(thresholds.get("intl_market_wide_band_pp", 1.0))
    return float(thresholds.get("market_wide_band_pp", 0.75))


def attribute(fund_ret: float | None, ref_ret: float | None, band: float, stale: bool = False) -> str:
    if stale or fund_ret is None or ref_ret is None:
        return "insufficient_data"
    gap = abs(fund_ret - ref_ret)
    same_direction = (fund_ret >= 0) == (ref_ret >= 0)
    tiny = abs(fund_ret) < TINY_MOVE and abs(ref_ret) < TINY_MOVE
    if same_direction and gap <= band:
        return "market_wide"
    if not same_direction and tiny:
        return "mixed"
    if same_direction and gap <= band * 1.5:
        return "mixed"  # slightly above the band
    if not same_direction and gap <= band:
        return "mixed"
    return "fund_specific"


def label(code: str | None) -> str:
    return LABELS.get(code or "insufficient_data", LABELS["insufficient_data"])
