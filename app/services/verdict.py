"""Deterministic verdict engine. Rules only, no AI.

Gemini later phrases the reason in friendly words, but it cannot change the
verdict: the guardrails reject any report whose verdict differs from this one.
"""
from __future__ import annotations

NO_ACTION = "No action needed"
WATCH_ONE = "One fund worth watching"
REVIEW = "Consider reviewing your allocation"
VERDICTS = (NO_ACTION, WATCH_ONE, REVIEW)

WEATHER = {
    NO_ACTION: {"key": "clear", "label": "Clear skies", "icon": "sun"},
    WATCH_ONE: {"key": "clouds", "label": "Passing clouds", "icon": "cloud"},
    REVIEW: {"key": "storm", "label": "Storm watch", "icon": "cloud-rain"},
}


def weather(verdict: str | None) -> dict:
    return WEATHER.get(verdict or "", {"key": "unknown", "label": "No report yet", "icon": "cloud-sun"})


def lag_thresholds(asset_class: str, thresholds: dict) -> tuple[float, float]:
    prefix = {"debt": "debt", "international": "intl"}.get(asset_class, "equity")
    return float(thresholds[f"{prefix}_week_lag_pp"]), float(thresholds[f"{prefix}_month_lag_pp"])


def ref_gap_1m(snap: dict) -> float | None:
    """1-month gap vs benchmark (or category average when there is no benchmark)."""
    fund = snap.get("ret_1m")
    ref = snap.get("bench_ret_1m") if snap.get("bench_ret_1m") is not None else snap.get("cat_ret_1m")
    if fund is None or ref is None:
        return None
    return round(fund - ref, 4)


def ref_gap_1w(snap: dict) -> float | None:
    if snap.get("delta_bench_1w") is not None:
        return snap["delta_bench_1w"]
    return snap.get("delta_cat_1w")


def is_lagging(snap: dict, asset_class: str, thresholds: dict) -> bool:
    week_lag, month_lag = lag_thresholds(asset_class, thresholds)
    g1w, g1m = ref_gap_1w(snap), ref_gap_1m(snap)
    return (g1w is not None and g1w < -week_lag) or (g1m is not None and g1m < -month_lag)


def lag_streak(history: list[dict], asset_class: str, thresholds: dict) -> int:
    """Consecutive lagging weeks counting back from the newest snapshot (history newest first)."""
    streak = 0
    for snap in history:
        if is_lagging(snap, asset_class, thresholds):
            streak += 1
        else:
            break
    return streak


def allocation_drift(actual: dict, target: dict) -> tuple[float, str | None]:
    worst, worst_key = 0.0, None
    for key in set(actual) | set(target):
        gap = abs(float(actual.get(key, 0.0)) - float(target.get(key, 0.0)))
        if gap > worst:
            worst, worst_key = gap, key
    return round(worst, 2), worst_key


def decide(funds: list[dict], allocation: dict, target: dict, thresholds: dict) -> dict:
    """funds: [{name, asset_class, history: [snapshots newest first]}]; allocation: % by asset class.

    Returns {verdict, watch: [...], lagging: [...], drift_pp, drift_class, facts}.
    """
    n_watch = int(thresholds.get("consecutive_weeks_to_watch", 3))
    drift_limit = float(thresholds.get("allocation_drift_pp", 10))
    watch, lagging = [], []
    for f in funds:
        history = f.get("history") or []
        if not history:
            continue
        current = history[0]
        _, month_lag = lag_thresholds(f["asset_class"], thresholds)
        streak = lag_streak(history, f["asset_class"], thresholds)
        g1m = ref_gap_1m(current)
        entry = {"fund": f["name"], "lag_streak_weeks": streak,
                 "gap_1w_pp": None if ref_gap_1w(current) is None else round(ref_gap_1w(current), 2),
                 "gap_1m_pp": None if g1m is None else round(g1m, 2)}
        if streak >= 1:
            lagging.append(entry)
        if streak >= n_watch or (g1m is not None and g1m < -2 * month_lag):
            entry["reason"] = "streak" if streak >= n_watch else "big_month_gap"
            watch.append(entry)

    drift, drift_class = allocation_drift(allocation, target)
    if drift > drift_limit or len(watch) >= 2:
        verdict = REVIEW
    elif len(watch) == 1:
        verdict = WATCH_ONE
    else:
        verdict = NO_ACTION

    facts: dict = {}
    if verdict == REVIEW:
        facts = {"allocation_drift_pp": drift, "drift_asset_class": drift_class, "drift_limit_pp": drift_limit,
                 "funds_to_watch": watch}
    elif verdict == WATCH_ONE:
        facts = dict(watch[0])
        facts.pop("reason", None)
    else:
        facts = {"funds_lagging_this_week": len(lagging), "allocation_drift_pp": drift}
    return {"verdict": verdict, "watch": watch, "lagging": lagging, "drift_pp": drift,
            "drift_class": drift_class, "facts": facts}


def reason_text(result: dict) -> str:
    """Plain deterministic reason (used by the fallback template and as Gemini's guide)."""
    v, f = result["verdict"], result["facts"]
    if v == WATCH_ONE:
        if f.get("lag_streak_weeks", 0) >= 2:
            return (f"{f['fund']} has trailed its benchmark for {f['lag_streak_weeks']} weeks in a row, "
                    f"so it is worth keeping an eye on.")
        return f"{f['fund']} is {abs(f.get('gap_1m_pp') or 0):.2f} points behind its benchmark over the past month, so it is worth keeping an eye on."
    if v == REVIEW:
        parts = []
        if result["drift_pp"] > f.get("drift_limit_pp", 10):
            parts.append(f"your {f.get('drift_asset_class')} share is {result['drift_pp']:.2f} points away from your target")
        if len(result["watch"]) >= 2:
            parts.append(f"{len(result['watch'])} funds have been trailing their benchmarks")
        return "Worth looking at your allocation, or discussing it with a financial adviser: " + " and ".join(parts) + "."
    return "Your funds moved broadly in line with their markets and your mix is close to your target."
