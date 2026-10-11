"""Code checks on every Gemini output before it is saved or shown.

1. JSON matches the schema; every fund summary has exactly three sentences.
2. The verdict string equals the verdict our rules computed.
3. Number check: every percentage / points figure in the text must match a number
   in the facts we sent (within 0.05 after rounding to the same decimals).
4. Banned phrases (no buy/sell/switch advice, no guarantees, no price targets).
5. Length limits.
On failure the caller retries once with the errors appended, then falls back to a
deterministic template.
"""
from __future__ import annotations

import re
from datetime import date

from .verdict import VERDICTS

MAX_SENTENCE_WORDS = 34

REPORT_BANNED = [
    (r"you should buy", "you should buy"),
    (r"\bsell", "sell"),
    (r"\bredeem", "redeem"),
    (r"\bswitch", "switch"),
    (r"stop your sip", "stop your SIP"),
    (r"\bguarantee", "guaranteed"),
    (r"will definitely", "will definitely"),
    (r"sure[- ]shot", "sure-shot"),
    (r"target price", "target price"),
]

# In chat, words like "sell" may appear when explaining a concept. Only instructions are blocked.
CHAT_INSTRUCTION_PATTERNS = [
    r"\byou (should|must|need to|ought to|have to|could consider|might want to|may want to)\s+(buy|sell|switch|redeem|stop|pause|exit|start|invest more|increase|reduce|move)",
    r"\b(i|we) (would )?(recommend|suggest|advise)\b",
    r"\b(buy|sell|switch|redeem|exit)\s+(it |this |that |your |the )?(fund |sip )?(now|today|immediately|right away)\b",
    r"\b(stop|pause) your sip\b",
    r"\bwill definitely\b",
    r"\bsure[- ]shot\b",
    r"\btarget price\b",
    r"\bbest time to (buy|sell)\b",
]

NUMBER_RE = re.compile(r"(?<![\w.])[-+−]?\s?₹?\s?(\d{1,3}(?:,\d{2,3})+|\d+)(\.\d+)?")
UNIT_AFTER_RE = re.compile(r"^\s*(%|per ?cent|pp\b|points?\b|pts?\b)", re.I)
INDEX_NUMBERS = {50.0, 100.0, 150.0, 250.0, 500.0}


# ---------- helpers ----------

def _numbers_from_value(value, out: set[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.add(abs(float(value)))
    elif isinstance(value, str):
        for m in NUMBER_RE.finditer(value):
            out.add(abs(float(m.group(1).replace(",", "") + (m.group(2) or ""))))
    elif isinstance(value, date):
        out.update({float(value.year), float(value.month), float(value.day)})
    elif isinstance(value, dict):
        for v in value.values():
            _numbers_from_value(v, out)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _numbers_from_value(v, out)


def allowed_numbers(facts) -> set[float]:
    out: set[float] = set()
    _numbers_from_value(facts, out)
    return out


def numbers_in_text(text: str) -> list[dict]:
    found = []
    for m in NUMBER_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "") + (m.group(2) or "")
        decimals = len(m.group(2)) - 1 if m.group(2) else 0
        has_unit = bool(UNIT_AFTER_RE.match(text[m.end():m.end() + 10])) or "₹" in m.group(0)
        found.append({"value": abs(float(raw)), "decimals": decimals, "unit": has_unit, "text": m.group(0).strip()})
    return found


def _matches(n: dict, allowed: set[float], tol: float = 0.05) -> bool:
    for a in allowed:
        if abs(n["value"] - round(a, n["decimals"])) <= tol + 1e-9:
            return True
    return False


def check_numbers(text: str, facts, extra_ok: set[float] | None = None) -> list[str]:
    """Return the numbers in text that don't come from the facts."""
    allowed = allowed_numbers(facts) | (extra_ok or set())
    bad = []
    for n in numbers_in_text(text):
        if _matches(n, allowed):
            continue
        if not n["unit"]:
            # Bare integers are fine for counts, dates and index names ("3 weeks", "9 Oct", "Nifty 50").
            if n["decimals"] == 0 and (n["value"] <= 31 or n["value"] in INDEX_NUMBERS or 1990 <= n["value"] <= 2100):
                continue
        bad.append(n["text"])
    return bad


def split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'₹(])", text)
    return [p.strip() for p in parts if p.strip()]


def banned_in_report(text: str) -> list[str]:
    low = (text or "").lower()
    return [label for pattern, label in REPORT_BANNED if re.search(pattern, low)]


def banned_in_chat(text: str) -> list[str]:
    low = (text or "").lower()
    hits = [p for p in CHAT_INSTRUCTION_PATTERNS if re.search(p, low)]
    for m in re.finditer(r"\bguarantee", low):
        before = low[max(0, m.start() - 25):m.start()]
        if not re.search(r"\b(not|no|never|isn't|aren't|can't|cannot|nothing)\b", before):
            hits.append("guarantee")
    return hits


def _too_long(sentence: str, max_words: int = MAX_SENTENCE_WORDS) -> bool:
    return len(sentence.split()) > max_words


# ---------- validators ----------

def validate_report(report: dict, facts: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["Report is not a JSON object"]
    for key in ("headline", "funds", "market_paragraph", "verdict", "verdict_reason"):
        if key not in report:
            errors.append(f"Missing field: {key}")
    if errors:
        return errors

    if report["verdict"] not in VERDICTS:
        errors.append(f"Verdict '{report['verdict']}' is not one of the three allowed verdicts")
    elif report["verdict"] != facts.get("verdict"):
        errors.append(f"Verdict must be exactly '{facts.get('verdict')}' (copied from facts)")

    headline = str(report.get("headline", ""))
    if len(headline.split()) > 14:
        errors.append("Headline is longer than 14 words")

    fact_funds = {f["name"] for f in facts.get("funds", [])}
    got_funds = report.get("funds") or []
    got_names = {f.get("name") for f in got_funds if isinstance(f, dict)}
    missing = fact_funds - got_names
    if missing:
        errors.append(f"Missing summaries for: {', '.join(sorted(missing))}")
    valid_news_ids = {n.get("id") for n in facts.get("news", [])}

    texts = [headline, str(report.get("market_paragraph", "")), str(report.get("verdict_reason", ""))]
    for f in got_funds:
        if not isinstance(f, dict):
            errors.append("Fund entry is not an object")
            continue
        summary = f.get("summary")
        if not isinstance(summary, list) or len(summary) != 3:
            errors.append(f"{f.get('name')}: summary must be exactly 3 sentences")
            continue
        for i, s in enumerate(summary, 1):
            if len(split_sentences(str(s))) != 1:
                errors.append(f"{f.get('name')}: item {i} must be a single sentence")
            if _too_long(str(s)):
                errors.append(f"{f.get('name')}: sentence {i} is too long")
            texts.append(str(s))
        for nid in f.get("news_ids_used") or []:
            if nid not in valid_news_ids:
                errors.append(f"{f.get('name')}: cites news id {nid} which was not provided")

    mp_sentences = split_sentences(str(report.get("market_paragraph", "")))
    if not 1 <= len(mp_sentences) <= 4:
        errors.append("Market paragraph should be 2-3 sentences")
    vr_sentences = split_sentences(str(report.get("verdict_reason", "")))
    if not 1 <= len(vr_sentences) <= 2:
        errors.append("Verdict reason should be 1-2 sentences")

    all_text = " ".join(texts)
    bad_numbers = check_numbers(all_text, facts)
    if bad_numbers:
        errors.append(f"Numbers not found in the facts: {', '.join(sorted(set(bad_numbers))[:8])}")
    banned = banned_in_report(all_text)
    if banned:
        errors.append(f"Banned words used: {', '.join(sorted(set(banned)))}")
    return errors


def validate_short_text(text: str, facts, *, max_sentences: int, max_words: int, mode: str = "report",
                        must_include: list[str] | None = None) -> list[str]:
    errors = []
    sentences = split_sentences(text)
    if not text or not sentences:
        return ["Empty text"]
    if len(sentences) > max_sentences:
        errors.append(f"Use at most {max_sentences} sentences")
    if len(text.split()) > max_words:
        errors.append(f"Use at most {max_words} words")
    bad = check_numbers(text, facts)
    if bad:
        errors.append(f"Numbers not found in the facts: {', '.join(sorted(set(bad))[:8])}")
    banned = banned_in_report(text) if mode == "report" else banned_in_chat(text)
    if banned:
        errors.append(f"Advice-like or banned wording: {', '.join(sorted(set(map(str, banned))))}")
    for phrase in must_include or []:
        if not re.search(phrase, text, re.I):
            errors.append(f"Must include a phrase like '{phrase}'")
    return errors


def validate_chat(answer: str, context: dict) -> list[str]:
    errors = validate_short_text(answer, context, max_sentences=12, max_words=220, mode="chat")
    return errors
