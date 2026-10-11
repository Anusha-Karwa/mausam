"""Gemini Flash over REST (the only AI used by Mausam).

- JSON mode with a response schema, so outputs are structured and checkable.
- 1 retry on 429/5xx with backoff; callers fall back to deterministic text after that.
- We never send names or emails: only fund nicknames and numbers.
"""
from __future__ import annotations

import json
import time

import httpx

from ..config import config

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


def _extract_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason")
        raise GeminiError(f"No candidates returned{f' (blocked: {reason})' if reason else ''}")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise GeminiError(f"Empty response (finishReason={candidates[0].get('finishReason')})")
    return text


def generate_json(system: str, prompt: str, schema: dict, *, temperature: float = 0.4,
                  timeout: float = 25.0, retries: int = 1) -> tuple[dict, dict]:
    """Call Gemini and return (parsed_json, meta). meta has model, latency_ms, tokens."""
    if not available():
        raise GeminiError("GEMINI_API_KEY is not set")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    url = API.format(model=config.GEMINI_MODEL)
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}
    last_error = ""
    started = time.time()
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            last_error = f"network: {exc}"
            if attempt < retries:
                time.sleep(1.5)
                continue
            break
        if resp.status_code == 400 and "thinking" in resp.text.lower() and "thinkingConfig" in body["generationConfig"]:
            # Older models don't accept thinkingLevel; drop it and try again without spending a retry.
            body["generationConfig"].pop("thinkingConfig")
            resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            last_error = f"HTTP {resp.status_code}"
            time.sleep(2.0 * (attempt + 1))
            continue
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            break
        payload = resp.json()
        text = _extract_text(payload)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GeminiError(f"Response was not valid JSON: {exc}") from exc
        usage = payload.get("usageMetadata") or {}
        meta = {
            "model": payload.get("modelVersion") or config.GEMINI_MODEL,
            "latency_ms": int((time.time() - started) * 1000),
            "tokens_in": usage.get("promptTokenCount"),
            "tokens_out": usage.get("candidatesTokenCount"),
            "tokens_total": usage.get("totalTokenCount"),
        }
        return data, meta
    raise GeminiError(last_error or "Gemini call failed")


def ping() -> dict:
    """Tiny call for the status page health check."""
    data, meta = generate_json(
        "Reply with JSON only.", "Reply with ok=true.",
        {"type": "OBJECT", "properties": {"ok": {"type": "BOOLEAN"}}, "required": ["ok"]},
        timeout=15, retries=0,
    )
    return {"ok": bool(data.get("ok")), **meta}
