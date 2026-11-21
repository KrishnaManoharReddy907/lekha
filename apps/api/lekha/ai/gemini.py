"""Google Gemini provider (free tier friendly).

Uses the REST API directly with httpx. Models are tried in order (LEKHA_GEMINI_MODELS); a model
that is rate-limited or out of daily quota is skipped for a cool-down period, so the free tier's
small per-model quotas are combined. Structured output uses `responseJsonSchema`.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import structlog

from lekha.ai.client import AIRefused, AIResult, AIUnavailable
from lekha.core.config import get_settings

log = structlog.get_logger("gemini")
BASE = "https://generativelanguage.googleapis.com/v1beta"
_cooldown: dict[str, float] = {}  # model -> unix time until which it is skipped


def _parts(content: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    out: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "image" and block.get("source", {}).get("type") == "base64":
            src = block["source"]
            out.append({"inlineData": {"mimeType": src["media_type"], "data": src["data"]}})
        elif block.get("type") == "document" and block.get("source", {}).get("type") == "base64":
            src = block["source"]
            out.append({"inlineData": {"mimeType": src["media_type"], "data": src["data"]}})
        elif block.get("type") == "text":
            out.append({"text": block["text"]})
    return out


def _strip_unsupported(schema: Any) -> Any:
    """Gemini's schema dialect rejects some JSON-Schema keywords; drop them (validation happens locally)."""
    if isinstance(schema, dict):
        return {k: _strip_unsupported(v) for k, v in schema.items() if k not in ("additionalProperties", "$schema")}
    if isinstance(schema, list):
        return [_strip_unsupported(x) for x in schema]
    return schema


async def complete_gemini(*, system: str, content: list[dict[str, Any]] | str, schema: dict[str, Any] | None,
                          max_tokens: int, fast: bool = False) -> AIResult:
    s = get_settings()
    key = s.gemini_api_key
    if not key:
        raise AIUnavailable("GEMINI_API_KEY is not set")
    models = [m.strip() for m in s.gemini_models.split(",") if m.strip()]
    if fast:
        # Text tasks (summaries, letters, copilot steps): fastest model with the largest free quota first.
        models = sorted(models, key=lambda m: 0 if "lite" in m else 1)
    gen: dict[str, Any] = {"maxOutputTokens": max(1024, min(max_tokens, 32768))}
    if schema:
        gen["responseMimeType"] = "application/json"
        gen["responseJsonSchema"] = _strip_unsupported(schema)
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": _parts(content)}],
        "generationConfig": gen,
    }
    errors: list[str] = []
    # A free-tier model under heavy load can hang rather than fail; fail over quickly instead of waiting.
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for model in models:
            if _cooldown.get(model, 0) > time.time():
                errors.append(f"{model}: cooling down after quota error")
                continue
            started = time.perf_counter()
            try:
                r = await client.post(f"{BASE}/models/{model}:generateContent", json=body,
                                      headers={"x-goog-api-key": key})
            except httpx.TimeoutException:
                _cooldown[model] = time.time() + 300
                errors.append(f"{model}: timed out")
                log.warning("gemini.timeout", model=model)
                continue
            except httpx.HTTPError as exc:
                errors.append(f"{model}: {exc}")
                continue
            latency = int((time.perf_counter() - started) * 1000)
            if r.status_code == 429:
                # Daily quota exhausted or per-minute limit hit: skip this model for a while.
                text = r.text.lower()
                _cooldown[model] = time.time() + (3600 if "per day" in text or "daily" in text else 65)
                errors.append(f"{model}: rate limited")
                log.warning("gemini.rate_limited", model=model)
                continue
            if r.status_code in (500, 502, 503, 504):
                # Model overloaded: short cool-down, try the next model now.
                _cooldown[model] = time.time() + 30
                errors.append(f"{model}: overloaded (HTTP {r.status_code})")
                continue
            if r.status_code in (401, 403):
                raise AIUnavailable("The Gemini API key was rejected. Check GEMINI_API_KEY.")
            if r.status_code == 404:
                errors.append(f"{model}: not available")
                continue
            if r.status_code >= 400:
                errors.append(f"{model}: HTTP {r.status_code} {r.text[:200]}")
                continue
            data = r.json()
            cands = data.get("candidates") or []
            if not cands:
                if (data.get("promptFeedback") or {}).get("blockReason"):
                    raise AIRefused(f"Gemini blocked the request: {data['promptFeedback']['blockReason']}")
                errors.append(f"{model}: empty response")
                continue
            cand = cands[0]
            finish = cand.get("finishReason")
            if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
                raise AIRefused(f"Gemini declined ({finish}).")
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
            if finish == "MAX_TOKENS":
                errors.append(f"{model}: response cut off")
                continue
            parsed = None
            if schema:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    errors.append(f"{model}: invalid JSON")
                    continue
            u = data.get("usageMetadata") or {}
            return AIResult(text=text, data=parsed, model=data.get("modelVersion") or model,
                            input_tokens=int(u.get("promptTokenCount") or 0),
                            output_tokens=int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0),
                            cost_usd=0.0, latency_ms=latency, request_id=data.get("responseId"))
    raise AIUnavailable("All Gemini models unavailable: " + "; ".join(errors))
