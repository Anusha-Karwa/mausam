"""Gemini-backed daily tip and the "Ask Mausam" chat, both behind guardrails."""
from __future__ import annotations

import json
import random
from datetime import date, timedelta

from .. import db
from . import gemini, glossary, guardrails, portfolio
from . import report as report_service
from . import snapshots

TIP_SYSTEM = """You write "One thing to know today" for a beginner mutual fund investor. Address her as "you".
One or two friendly sentences, at most 40 words in total. Use ONLY numbers from the facts.
Mention the glossary term and explain it simply. Never give advice to buy, sell, switch, redeem, stop or start anything.
Never predict prices. Do not use the words sell, redeem, switch or guaranteed."""

TIP_SCHEMA = {"type": "OBJECT", "properties": {"tip": {"type": "STRING"}}, "required": ["tip"]}

CHAT_SYSTEM = """You are "Ask Mausam", a friendly helper inside a personal mutual fund tracker. You speak to one beginner investor as "you".
Rules:
- Answer ONLY about her funds, mutual fund concepts, and how this app works. Politely refuse anything else in one sentence.
- Plain language, short paragraphs, at most 160 words. Explain jargon simply.
- Use ONLY numbers that appear in the context. Never calculate new figures. If a number isn't in the context, say you don't have it.
- Never tell her to buy, sell, switch, redeem, stop or start a SIP or fund, and never say what you "recommend" or "suggest".
  If she asks what to do, explain the considerations and say: "a SEBI-registered adviser can help with that decision."
- Never promise or guarantee returns. Never predict prices; forecasts in the app are rough ranges, not promises.
- You can explain what 'sell' or 'redeem' means as a concept, but never as an instruction."""

CHAT_SCHEMA = {"type": "OBJECT", "properties": {"answer": {"type": "STRING"}}, "required": ["answer"]}

CHAT_LIMIT_PER_HOUR = 20
STARTERS = ["Why did my small-cap fund fall?", "What is a benchmark?", "Am I on track?", "What does XIRR mean?"]


# ---------- daily tip ----------

def _pick_term(today: date) -> str:
    recent = db.query("SELECT facts_json FROM daily_tips WHERE tip_date >= %s", [today - timedelta(days=14)])
    used = {(r["facts_json"] or {}).get("term") for r in recent}
    choices = [v["term"] for v in glossary.GLOSSARY.values() if v["term"] not in used] or [v["term"] for v in glossary.GLOSSARY.values()]
    rng = random.Random(today.toordinal())
    return rng.choice(choices)


def tip_facts(user_id: int, today: date) -> dict:
    summ = portfolio.summary(user_id, today=today)
    movers = sorted([f for f in summ["funds"] if f["day_change_pct"] is not None], key=lambda f: abs(f["day_change_pct"]), reverse=True)
    sips = portfolio.next_sips(user_id, today)
    term = _pick_term(today)
    entry = next(v for v in glossary.GLOSSARY.values() if v["term"] == term)
    return {
        "portfolio_change_rupees": summ["day_change"], "portfolio_change_pct": summ["day_change_pct"],
        "nav_date": summ["as_of"].isoformat() if summ["as_of"] else None,
        "biggest_mover": ({"fund": movers[0]["name"], "change_pct": movers[0]["day_change_pct"]} if movers else None),
        "next_sip": ({"fund": sips[0]["name"], "amount_rupees": sips[0]["amount"], "days_away": sips[0]["days"]} if sips else None),
        "term": term, "term_definition": entry["short"],
    }


def fallback_tip(facts: dict) -> str:
    tip = f"{facts['term']}: {facts['term_definition']}"
    if facts.get("next_sip") and facts["next_sip"]["days_away"] <= 7:
        s = facts["next_sip"]
        tip += f" Your {s['fund']} SIP is due in {s['days_away']} days."
    return tip


def daily_tip(user_id: int, today: date) -> dict:
    facts = tip_facts(user_id, today)
    text, used_fallback, meta = fallback_tip(facts), True, {}
    if gemini.available():
        prompt = "Facts (JSON):\n" + json.dumps(facts, default=str)
        for _ in range(2):
            try:
                data, meta = gemini.generate_json(TIP_SYSTEM, prompt, TIP_SCHEMA, timeout=25)
            except gemini.GeminiError:
                break
            candidate = (data.get("tip") or "").strip()
            errors = guardrails.validate_short_text(candidate, facts, max_sentences=2, max_words=45)
            if not errors:
                text, used_fallback = candidate, False
                break
            prompt += "\n\nFix these problems: " + "; ".join(errors)
    facts["used_fallback"] = used_fallback
    db.execute("""INSERT INTO daily_tips (tip_date, tip, facts_json) VALUES (%s, %s, %s)
                  ON CONFLICT (tip_date) DO UPDATE SET tip = EXCLUDED.tip, facts_json = EXCLUDED.facts_json""",
               [today, text, facts])
    return {"tip": text, "used_fallback": used_fallback, "meta": meta}


def latest_tip() -> dict | None:
    return db.one("SELECT * FROM daily_tips ORDER BY tip_date DESC LIMIT 1")


# ---------- chat ----------

def chat_context(user_id: int) -> dict:
    """Everything the chat may use. No name, no email."""
    summ = portfolio.summary(user_id)
    week = snapshots.latest_week(user_id)
    snaps = snapshots.for_week(user_id, week) if week else []
    rep = report_service.latest(user_id)
    return {
        "portfolio": {
            "invested_rupees": summ["invested"], "value_rupees": summ["value"], "gain_rupees": summ["gain"],
            "gain_pct": summ["gain_pct"], "xirr_pct": summ["xirr"], "nav_date": summ["as_of"].isoformat() if summ["as_of"] else None,
            "allocation_pct": summ["allocation"], "target_allocation_pct": summ["target"],
            "funds": [{"name": f["name"], "asset_class": f["asset_class"], "value_rupees": f["value"], "invested_rupees": f["invested"],
                       "gain_pct": f["gain_pct"], "xirr_pct": f["xirr"], "weight_pct": f["weight"]} for f in summ["funds"]],
        },
        "latest_week": {
            "week_ending": week.isoformat() if week else None,
            "funds": [{"name": s["short_name"] or s["scheme_name"], "ret_1w": s["ret_1w"], "ret_1m": s["ret_1m"],
                       "ret_ytd": s["ret_ytd"], "ret_1y": s["ret_1y"], "bench_ret_1w": s["bench_ret_1w"],
                       "cat_ret_1w": s["cat_ret_1w"], "gap_vs_benchmark_1w_pts": s["delta_bench_1w"],
                       "move_type": s["attribution"]} for s in snaps],
        },
        "latest_report": ({"week_ending": rep["week_ending"].isoformat(), "verdict": rep["verdict"],
                           "verdict_reason": rep["verdict_reason"],
                           "headline": (rep["report_json"] or {}).get("headline"),
                           "market_paragraph": (rep["report_json"] or {}).get("market_paragraph"),
                           "fund_summaries": {f["name"]: f["summary"] for f in (rep["report_json"] or {}).get("funds", [])}}
                          if rep else None),
        "glossary": glossary.compact(),
    }


def history(user_id: int, limit: int = 50) -> list[dict]:
    rows = db.query("SELECT id, role, content, created_at FROM chat_messages WHERE user_id = %s ORDER BY id DESC LIMIT %s",
                    [user_id, limit])
    return rows[::-1]


def messages_last_hour(user_id: int) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM chat_messages WHERE user_id = %s AND role = 'user' AND created_at > now() - interval '1 hour'",
                         [user_id]) or 0)


SAFE_FALLBACK = ("Sorry, I couldn't give a safe answer to that just now. I can explain your funds, how they compare with "
                 "their benchmarks, or what any term in the app means. For decisions about buying or selling, "
                 "a SEBI-registered adviser can help with that decision.")


def ask(user_id: int, question: str) -> dict:
    question = question.strip()[:600]
    ctx = chat_context(user_id)
    recent = history(user_id, limit=6)
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
    prompt = (f"Context (JSON, the only facts you may use):\n{json.dumps(ctx, default=str)}\n\n"
              f"Recent conversation:\n{convo or '(none)'}\n\nQuestion: {question}")
    answer, ok, meta = SAFE_FALLBACK, False, {}
    if gemini.available():
        allowed = dict(ctx)
        allowed["question"] = question
        for _ in range(2):
            try:
                data, meta = gemini.generate_json(CHAT_SYSTEM, prompt, CHAT_SCHEMA, temperature=0.3, timeout=30)
            except gemini.GeminiError as exc:
                meta = {"error": str(exc)[:200]}
                break
            candidate = (data.get("answer") or "").strip()
            errors = guardrails.validate_chat(candidate, allowed)
            if not errors:
                answer, ok = candidate, True
                break
            prompt += "\n\nYour previous answer broke these rules, rewrite it: " + "; ".join(errors)
    else:
        answer = "The AI helper isn't set up yet (no Gemini key). Meanwhile, hover any underlined term in the app for a plain explanation."
    db.execute("INSERT INTO chat_messages (user_id, role, content) VALUES (%s, 'user', %s)", [user_id, question])
    db.execute("INSERT INTO chat_messages (user_id, role, content) VALUES (%s, 'assistant', %s)", [user_id, answer])
    return {"answer": answer, "guardrails_passed": ok, "meta": {k: v for k, v in meta.items() if k != "tokens_in"}}


def clear(user_id: int) -> int:
    return db.execute("DELETE FROM chat_messages WHERE user_id = %s", [user_id])
