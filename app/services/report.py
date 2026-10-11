"""The Saturday weather report.

Code computes every number and the verdict, packs them into `facts_json`, and
Gemini only turns those facts into friendly sentences. Guardrails check the
output; after one failed retry we use a deterministic template instead.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from .. import db
from . import attribution, gemini, guardrails, indices, news, portfolio, returns, snapshots
from . import verdict as verdict_engine
from .benchmarks import BENCHMARK_LABELS

MARKET_KEYS = [
    ("NIFTY50", "NIFTY50"), ("MIDCAP150", "MIDCAP150"), ("SMALLCAP100", "SMALLCAP100"),
    ("SMALLCAP250", "SMALLCAP250"), ("SP500", "SP500_INR"), ("NASDAQ100", "NASDAQ100_INR"),
]
INDEX_LABELS = {"NIFTY50": "Nifty 50", "MIDCAP150": "Nifty Midcap 150", "SMALLCAP100": "Nifty Smallcap 100",
                "SMALLCAP250": "Nifty Smallcap 250", "SP500_INR": "S&P 500 (in rupees)", "NASDAQ100_INR": "Nasdaq 100 (in rupees)"}

REPORT_SYSTEM = """You write a weekly mutual fund "weather report" for a beginner investor. Address the reader as "you".
Rules:
- Short, warm, plain sentences. At most 22 words per sentence. No abbreviations with full stops (write "versus", not "vs.").
- Each fund summary is EXACTLY three sentences, given as three separate strings:
  (1) how the fund did this week and this month;
  (2) how that compares with its benchmark and its category average (gaps are in "points");
  (3) which market event or news item most likely explains the move, citing ONLY items from `news` by putting their ids in news_ids_used.
      If no news item fits, say the move matched the broader market, or that no clear cause was found.
- Use ONLY numbers that appear in the facts. Never compute new numbers, never add totals or differences of your own. Round only to fewer decimals.
- Never invent news. Never predict prices or future returns.
- Copy the verdict string exactly from facts.verdict. Explain verdict_reason using facts.verdict_facts in 1-2 sentences.
- Never tell the reader to buy, sell, switch, redeem, stop or start a SIP. Do not use the words "sell", "selling", "sell-off", "redeem", "switch", "guaranteed".
  For falling markets say "fell", "declined" or "slipped". If the verdict is about allocation, suggest looking at it, or discussing it with a financial adviser.
- Explain any jargon in brackets the first time you use it, e.g. "benchmark (the market index it is measured against)".
- The headline is at most 14 words and matches the verdict's mood (clear skies, passing clouds, or storm watch).
- The market paragraph is 2-3 sentences about the index moves and the most important news."""

REPORT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "funds": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING"},
                "summary": {"type": "ARRAY", "items": {"type": "STRING"}, "minItems": 3, "maxItems": 3},
                "news_ids_used": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            },
            "required": ["name", "summary", "news_ids_used"],
        }},
        "market_paragraph": {"type": "STRING"},
        "verdict": {"type": "STRING", "enum": list(verdict_engine.VERDICTS)},
        "verdict_reason": {"type": "STRING"},
        "jargon_used": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["headline", "funds", "market_paragraph", "verdict", "verdict_reason", "jargon_used"],
}

MARKET_SYSTEM = """You summarise a week in Indian and US markets for a beginner investor with five funds:
a Nifty 50 index fund, a mid-cap fund, a small-cap fund, a short-duration debt fund and an international (US) fund of funds.
Rules: plain words; use ONLY numbers given; never invent events; no advice; never use the words sell, redeem, switch or guaranteed.
macro_summary: exactly 3 sentences. For each news item, why_it_matters: at most 18 words linking it to her fund types."""

MARKET_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "macro_summary": {"type": "STRING"},
        "news": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "INTEGER"}, "why_it_matters": {"type": "STRING"}}, "required": ["id", "why_it_matters"]}},
    },
    "required": ["macro_summary", "news"],
}


def _r2(v):
    return None if v is None else round(float(v), 2)


def fund_name(f: dict) -> str:
    return f.get("short_name") or f.get("scheme_name")


# ---------- market context ----------

def index_moves(week: date) -> dict:
    moves = {}
    for key, label in MARKET_KEYS:
        s = indices.benchmark_series(key, week - timedelta(days=400))
        rows = [p for p in s["rows"] if p[0] <= week]
        if not rows:
            continue
        r = returns.compute(rows, week)
        fresh = (week - rows[-1][0]).days <= 5
        moves[label] = {"ret_1w": _r2(r["ret_1w"]) if fresh else None, "ret_1m": _r2(r["ret_1m"]) if fresh else None,
                        "ret_ytd": _r2(r["ret_ytd"]) if fresh else None, "close": _r2(rows[-1][1]),
                        "as_of": rows[-1][0].isoformat(), "source": s["source"]}
    return moves


def market_summary(week: date, moves: dict, news_rows: list[dict]) -> dict:
    """Gemini market paragraph + why-it-matters lines, with a deterministic fallback."""
    facts = {"index_moves_pct": {INDEX_LABELS.get(k, k): {"ret_1w": v["ret_1w"]} for k, v in moves.items()},
             "news": [{"id": n["id"], "title": n["title"], "tags": n.get("tags") or []} for n in news_rows]}
    fallback = {"macro_summary": fallback_macro(moves), "news": {n["id"]: fallback_why(n) for n in news_rows}, "used_fallback": True}
    if not gemini.available():
        return fallback
    prompt = "Facts (JSON):\n" + json.dumps(facts, default=str)
    for attempt in range(2):
        try:
            data, meta = gemini.generate_json(MARKET_SYSTEM, prompt, MARKET_SCHEMA, timeout=30)
        except gemini.GeminiError:
            return fallback
        errors = guardrails.validate_short_text(data.get("macro_summary", ""), facts, max_sentences=4, max_words=90)
        whys = {}
        for item in data.get("news") or []:
            text = (item.get("why_it_matters") or "").strip()
            if item.get("id") in fallback["news"] and not guardrails.validate_short_text(text, facts, max_sentences=2, max_words=22):
                whys[item["id"]] = text
        if not errors:
            return {"macro_summary": data["macro_summary"].strip(),
                    "news": {nid: whys.get(nid, fallback["news"][nid]) for nid in fallback["news"]},
                    "used_fallback": False, "meta": meta}
        prompt += "\n\nYour previous answer failed these checks, fix them: " + "; ".join(errors)
    return fallback


def fallback_macro(moves: dict) -> str:
    def part(key, label):
        v = (moves.get(key) or {}).get("ret_1w")
        return f"the {label} {'rose' if v >= 0 else 'fell'} {abs(v):.2f}%" if v is not None else None

    india = [p for p in (part("NIFTY50", "Nifty 50"), part("MIDCAP150", "Midcap 150"),
                         part("SMALLCAP100", "Smallcap 100") or part("SMALLCAP250", "Smallcap 250")) if p]
    us = part("SP500_INR", "S&P 500 in rupees")
    sentences = []
    if india:
        sentences.append("This week " + ", ".join(india[:-1]) + (" and " if len(india) > 1 else "") + india[-1] + ".")
    if us:
        sentences.append(f"Abroad, {us}.")
    sentences.append("Headlines below give the main news of the week.")
    return " ".join(s[0].upper() + s[1:] for s in sentences)


def fallback_why(item: dict) -> str:
    tags = item.get("tags") or []
    if "debt" in tags:
        return "Interest-rate and inflation news matters most to your debt fund."
    if "international" in tags:
        return "US market news moves your international fund of funds."
    if "equity" in tags:
        return "Indian stock market news moves your three equity funds."
    return "Big-picture economic news can affect all your funds a little."


# ---------- weekly facts ----------

def build_facts(user_id: int, week: date, news_rows: list[dict], moves: dict) -> tuple[dict, dict]:
    """Return (facts_json for Gemini, extra context for rendering)."""
    snaps = snapshots.for_week(user_id, week)
    th = snapshots.thresholds(user_id)
    funds_for_verdict, fund_facts = [], []
    for s in snaps:
        hist = snapshots.history(user_id, s["fund_id"], week, limit=12)
        streak = verdict_engine.lag_streak(hist, s["asset_class"], th)
        funds_for_verdict.append({"name": fund_name(s), "asset_class": s["asset_class"], "history": hist})
        bench_label = BENCHMARK_LABELS.get(s["benchmark_key"] or "", "")
        stale = "stale_data" in (s.get("flags") or [])
        fund_facts.append({
            "name": fund_name(s), "asset_class": s["asset_class"],
            "ret_1w": _r2(s["ret_1w"]), "ret_1m": _r2(s["ret_1m"]), "ret_ytd": _r2(s["ret_ytd"]),
            "benchmark": bench_label if s["benchmark_key"] != "CATEGORY_ONLY" else "none (compared with category average)",
            "bench_ret_1w": _r2(s["bench_ret_1w"]), "bench_ret_1m": _r2(s["bench_ret_1m"]),
            "cat_ret_1w": _r2(s["cat_ret_1w"]), "cat_ret_1m": _r2(s["cat_ret_1m"]),
            "delta_bench_1w_pp": _r2(s["delta_bench_1w"]), "delta_cat_1w_pp": _r2(s["delta_cat_1w"]),
            "attribution": s["attribution"], "attribution_label": attribution.label(s["attribution"]),
            "lag_streak_weeks": streak, "stale": stale,
        })

    total_value = sum(float(s["value"] or 0) for s in snaps)
    alloc = {k: 0.0 for k in portfolio.ASSET_CLASSES}
    for s in snaps:
        alloc[s["asset_class"]] = alloc.get(s["asset_class"], 0.0) + float(s["value"] or 0)
    alloc = {k: (round(v / total_value * 100, 1) if total_value else 0.0) for k, v in alloc.items()}
    target = portfolio.target_allocation(user_id)
    if not total_value:
        # No holdings recorded yet: allocation can't drift.
        alloc = {k: float(target.get(k, 0)) for k in portfolio.ASSET_CLASSES}

    decision = verdict_engine.decide(funds_for_verdict, alloc, target, th)
    facts = {
        "week_ending": week.isoformat(),
        "verdict": decision["verdict"],
        "verdict_facts": decision["facts"],
        "allocation": {**alloc, "target": target},
        "indices": {k: {"ret_1w": v["ret_1w"]} for k, v in moves.items()},
        "funds": fund_facts,
        "news": [{"id": n["id"], "title": n["title"], "date": (n["published_at"].date().isoformat() if n.get("published_at") else None),
                  "tags": n.get("tags") or []} for n in news_rows],
    }
    return facts, {"decision": decision, "snapshots": snaps}


# ---------- generation ----------

def fallback_report(facts: dict, decision: dict) -> dict:
    funds = []
    for f in facts["funds"]:
        funds.append({"name": f["name"], "summary": fallback_sentences(f), "news_ids_used": []})
    weather = verdict_engine.weather(facts["verdict"])
    headline = {"clear": "Clear skies: your funds moved with their markets",
                "clouds": "Passing clouds: one fund is worth watching",
                "storm": "Storm watch: your mix is worth a look"}.get(weather["key"], "Your weekly money weather")
    return {
        "headline": headline,
        "funds": funds,
        "market_paragraph": fallback_macro({k: v for k, v in facts["indices"].items()}),
        "verdict": facts["verdict"],
        "verdict_reason": verdict_engine.reason_text(decision),
        "jargon_used": ["benchmark", "category average", "points"],
    }


def _moved(v: float) -> str:
    return f"rose {abs(v):.2f}%" if v >= 0 else f"fell {abs(v):.2f}%"


def fallback_sentences(f: dict) -> list[str]:
    name = f["name"]
    if f["ret_1w"] is None:
        s1 = f"{name} has no fresh weekly figure this week."
    elif f["ret_1m"] is None:
        s1 = f"{name} {_moved(f['ret_1w'])} this week."
    else:
        s1 = f"{name} {_moved(f['ret_1w'])} this week and {_moved(f['ret_1m'])} over the past month."

    if f["bench_ret_1w"] is not None and f["delta_bench_1w_pp"] is not None:
        side = "ahead of" if f["delta_bench_1w_pp"] >= 0 else "behind"
        s2 = (f"Its benchmark {_moved(f['bench_ret_1w'])}, so the fund finished {abs(f['delta_bench_1w_pp']):.2f} points {side} it.")
    elif f["cat_ret_1w"] is not None and f["delta_cat_1w_pp"] is not None:
        side = "ahead of" if f["delta_cat_1w_pp"] >= 0 else "behind"
        s2 = (f"Similar funds {_moved(f['cat_ret_1w'])} on average, so it finished {abs(f['delta_cat_1w_pp']):.2f} points {side} its category.")
    else:
        s2 = "We could not compare it with its benchmark or category this week."

    s3 = {
        "market_wide": "The move matched the broader market, so it looks market-wide rather than fund-specific.",
        "fund_specific": "The gap looks specific to this fund, and no single news item clearly explains it.",
        "mixed": "It was partly a market move and partly down to the fund's own choices.",
    }.get(f["attribution"], "There was not enough fresh data to explain the move this week.")
    return [s1, s2, s3]


def generate(facts: dict, decision: dict) -> dict:
    """Return {'report': dict, 'used_fallback': bool, 'model': str, 'errors': [...], 'meta': {...}}."""
    errors_seen: list[str] = []
    if gemini.available() and facts["funds"]:
        prompt = "Facts (JSON). Use only these numbers and news items:\n" + json.dumps(facts, default=str)
        for attempt in range(2):
            try:
                data, meta = gemini.generate_json(REPORT_SYSTEM, prompt, REPORT_SCHEMA, timeout=45)
            except gemini.GeminiError as exc:
                errors_seen.append(f"attempt {attempt + 1}: {exc}")
                break
            errors = guardrails.validate_report(data, facts)
            if not errors:
                return {"report": data, "used_fallback": False, "model": meta.get("model"), "errors": errors_seen, "meta": meta}
            errors_seen.append(f"attempt {attempt + 1}: " + "; ".join(errors))
            prompt += ("\n\nYour previous answer failed these checks. Fix every one and answer again:\n- "
                       + "\n- ".join(errors))
    report = fallback_report(facts, decision)
    return {"report": report, "used_fallback": True, "model": "fallback-template", "errors": errors_seen, "meta": {}}


def outlook(user_id: int) -> list[dict]:
    """Latest 30-day forecast range per fund, for the report appendix only (never the verdict)."""
    return db.query(
        """SELECT DISTINCT ON (f.id) f.id AS fund_id, COALESCE(f.short_name, f.scheme_name) AS name, fc.run_date,
                  fc.lo80, fc.hi80, fc.point, fc.target_date
           FROM funds f JOIN forecasts fc ON fc.scheme_code = f.scheme_code AND fc.horizon_days = 30
           WHERE f.user_id = %s AND f.is_active ORDER BY f.id, fc.run_date DESC""", [user_id])


def save(user_id: int, week: date, facts: dict, result: dict, html: str, trigger: str, extra: dict) -> dict:
    report_json = dict(result["report"])
    report_json["_meta"] = {"errors": result["errors"], "llm": result.get("meta"), "outlook": extra.get("outlook", []),
                            "news_why": extra.get("news_why", {}), "macro_used_fallback": extra.get("macro_used_fallback")}
    return db.one(
        """INSERT INTO reports (user_id, week_ending, verdict, verdict_reason, facts_json, report_json, report_html, model,
                                used_fallback, generated_at, trigger, emailed_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, NULL)
           ON CONFLICT (user_id, week_ending) DO UPDATE SET verdict=EXCLUDED.verdict, verdict_reason=EXCLUDED.verdict_reason,
             facts_json=EXCLUDED.facts_json, report_json=EXCLUDED.report_json, report_html=EXCLUDED.report_html,
             model=EXCLUDED.model, used_fallback=EXCLUDED.used_fallback, generated_at=now(), trigger=EXCLUDED.trigger,
             emailed_at=NULL
           RETURNING *""",
        [user_id, week, result["report"]["verdict"], result["report"]["verdict_reason"], facts, report_json, html,
         result["model"], result["used_fallback"], trigger],
    )


def latest(user_id: int) -> dict | None:
    return db.one("SELECT * FROM reports WHERE user_id = %s ORDER BY week_ending DESC LIMIT 1", [user_id])


def get(user_id: int, week: date) -> dict | None:
    return db.one("SELECT * FROM reports WHERE user_id = %s AND week_ending = %s", [user_id, week])


def all_reports(user_id: int) -> list[dict]:
    return db.query("SELECT id, week_ending, verdict, verdict_reason, used_fallback, generated_at, emailed_at, trigger, model, "
                    "report_json->>'headline' AS headline FROM reports WHERE user_id = %s ORDER BY week_ending DESC", [user_id])


def fund_summary(report_row: dict | None, name: str) -> list[str]:
    if not report_row:
        return []
    for f in (report_row.get("report_json") or {}).get("funds", []):
        if f.get("name") == name:
            return f.get("summary") or []
    return []


def news_for_report(news_rows: list[dict], whys: dict) -> list[dict]:
    out = []
    for n in news_rows:
        out.append({**n, "why_it_matters": whys.get(n["id"]) or n.get("why_it_matters")})
    return out


def store_whys(whys: dict) -> None:
    for nid, text in whys.items():
        db.execute("UPDATE news_items SET why_it_matters = %s WHERE id = %s", [text, nid])


def store_market_context(week: date, moves: dict, macro: str) -> None:
    db.execute(
        """INSERT INTO market_context (week_ending, index_moves, macro_summary) VALUES (%s, %s, %s)
           ON CONFLICT (week_ending) DO UPDATE SET index_moves = EXCLUDED.index_moves, macro_summary = EXCLUDED.macro_summary,
           created_at = now()""", [week, moves, macro])


def news_rows_for(week: date) -> list[dict]:
    return news.for_week(week)
