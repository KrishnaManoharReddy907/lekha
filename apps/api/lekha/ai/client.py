"""Single place that talks to the Claude API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import anthropic

from lekha.core.config import get_settings

# USD per million tokens (input, output). Used to record the cost of every AI call.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0),
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AIUnavailable(RuntimeError):
    pass


class AIRefused(RuntimeError):
    pass


@dataclass
class AIResult:
    text: str
    data: Any
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    request_id: str | None


def ai_enabled() -> bool:
    s = get_settings()
    return bool(s.anthropic_api_key) and not s.ai_offline


@lru_cache(maxsize=1)
def _client() -> anthropic.AsyncAnthropic:
    s = get_settings()
    if not s.anthropic_api_key:
        raise AIUnavailable("ANTHROPIC_API_KEY is not set")
    return anthropic.AsyncAnthropic(api_key=s.anthropic_api_key, max_retries=3, timeout=180.0)


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 6)


async def complete(
    *,
    system: str,
    content: list[dict[str, Any]] | str,
    schema: dict[str, Any] | None = None,
    max_tokens: int = 16000,
    effort: str | None = None,
    model: str | None = None,
) -> AIResult:
    """One Claude call. With `schema`, the response is guaranteed-valid JSON (structured outputs)."""
    if not ai_enabled():
        raise AIUnavailable("AI is not configured (set ANTHROPIC_API_KEY)")
    s = get_settings()
    model = model or s.ai_model
    output_config: dict[str, Any] = {"effort": effort or s.ai_effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": output_config,
    }
    started = time.perf_counter()
    try:
        if s.ai_fallbacks and model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"):
            resp = await _client().beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            resp = await _client().messages.create(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise AIUnavailable("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AIUnavailable("The Anthropic API key lacks permission for this model.") from exc
    latency = int((time.perf_counter() - started) * 1000)
    if resp.stop_reason == "refusal":
        raise AIRefused("The model declined this request.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = None
    if schema:
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("AI response was cut off (max_tokens); retry with a larger limit.")
        data = json.loads(text)
    used_model = getattr(resp, "model", model) or model
    u = resp.usage
    in_tok = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) + (
        getattr(u, "cache_creation_input_tokens", 0) or 0)
    return AIResult(text=text, data=data, model=used_model, input_tokens=in_tok, output_tokens=u.output_tokens or 0,
                    cost_usd=cost(used_model, in_tok, u.output_tokens or 0), latency_ms=latency,
                    request_id=getattr(resp, "_request_id", None))
