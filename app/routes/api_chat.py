"""Flow 7 — Ask Mausam (Gemini chat behind guardrails, 20 messages per hour)."""
from __future__ import annotations

from flask import Blueprint

from ..services import assistant
from ._util import body, fail, ok, user_id

bp = Blueprint("api_chat", __name__, url_prefix="/api/chat")


@bp.get("")
def history():
    return ok(assistant.history(user_id()))


@bp.post("")
def ask():
    q = (body().get("message") or "").strip()
    if not q:
        return fail("Type a question first.")
    if len(q) > 600:
        return fail("Please keep questions under 600 characters.")
    if assistant.messages_last_hour(user_id()) >= assistant.CHAT_LIMIT_PER_HOUR:
        return fail("You've asked 20 questions in the last hour. Take a break and try again a little later.", 429)
    return ok(assistant.ask(user_id(), q))


@bp.delete("")
def clear():
    return ok({"deleted": assistant.clear(user_id())})
