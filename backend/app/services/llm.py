"""Minimal client for an OpenAI-compatible chat-completions endpoint (with tool calling).

Credentials, in order of precedence:

1. ``NEXIS_LLM_API_KEY`` with ``NEXIS_LLM_BASE_URL`` / ``NEXIS_LLM_MODEL`` (any compatible provider);
2. ``ANTHROPIC_API_KEY`` — Anthropic's OpenAI-compatible endpoint;
3. ``AI_GATEWAY_API_KEY`` or the deployment's Vercel OIDC token — the Vercel AI Gateway.

Nothing is sent anywhere unless one of these is present. Failures raise :class:`LLMUnavailable`
with a human-readable reason so callers can fall back to a data-only answer.
"""

from __future__ import annotations

import contextvars
import json
import os
from collections.abc import Iterator
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
GATEWAY_URL = "https://ai-gateway.vercel.sh/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1"
# Set per request from the ``x-vercel-oidc-token`` header (Vercel injects it into function requests).
_request_oidc: contextvars.ContextVar[str | None] = contextvars.ContextVar("nexis_oidc", default=None)


class LLMUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def set_request_token(token: str | None) -> None:
    _request_oidc.set(token)


def _config() -> dict[str, Any] | None:
    s = get_settings()
    if s.llm_api_key:
        return {
            "provider": "custom",
            "url": (s.llm_base_url or GATEWAY_URL).rstrip("/"),
            "key": s.llm_api_key,
            "model": s.llm_model,
        }
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "provider": "anthropic",
            "url": ANTHROPIC_URL,
            "key": os.environ["ANTHROPIC_API_KEY"],
            "model": s.llm_model.split("/")[-1].replace(".", "-"),
        }
    token = os.environ.get("AI_GATEWAY_API_KEY") or _request_oidc.get() or os.environ.get("VERCEL_OIDC_TOKEN")
    if token:
        return {"provider": "vercel-ai-gateway", "url": GATEWAY_URL, "key": token, "model": s.llm_model}
    return None


def status() -> dict[str, Any]:
    c = _config()
    if c is None:
        return {"configured": False, "provider": None, "model": None,
                "reason": "No language-model credentials are configured on the server."}  # fmt: skip
    return {"configured": True, "provider": c["provider"], "model": c["model"], "reason": None}


def chat(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, max_tokens: int = 1800, temperature: float = 0.3
) -> dict[str, Any]:
    """One chat-completions call; returns the assistant message (``content`` and optional ``tool_calls``)."""
    c = _config()
    if c is None:
        raise LLMUnavailable("No language-model credentials are configured on the server.")
    body: dict[str, Any] = {"model": c["model"], "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if tools:
        body["tools"] = tools
    try:
        r = httpx.post(
            f"{c['url']}/chat/completions",
            headers={"Authorization": f"Bearer {c['key']}", "Content-Type": "application/json"},
            content=json.dumps(body, default=str),
            timeout=90.0,
        )
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"The language model could not be reached ({exc.__class__.__name__}).") from exc
    if r.status_code != 200:
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        if isinstance(err, dict) and err.get("type") == "customer_verification_required":
            msg = "The Vercel AI Gateway is not activated for this account yet (the owner must add a card to unlock the free monthly credits)."
        log.warning("LLM call failed: HTTP %s %s", r.status_code, (msg or "")[:200])
        raise LLMUnavailable(msg or f"The language model returned HTTP {r.status_code}.")
    try:
        return r.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as exc:
        raise LLMUnavailable("Unexpected response from the language model.") from exc


def _error_message(status: int, raw: bytes) -> str:
    try:
        err = json.loads(raw or b"{}").get("error") or {}
    except ValueError:
        err = {}
    msg = err.get("message") if isinstance(err, dict) else str(err)
    if isinstance(err, dict) and err.get("type") == "customer_verification_required":
        msg = "The Vercel AI Gateway is not activated for this account yet (the owner must add a card to unlock the free monthly credits)."
    return msg or f"The language model returned HTTP {status}."


def chat_stream(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, max_tokens: int = 3000, temperature: float = 0.4
) -> Iterator[tuple[str, Any]]:
    """Streaming chat completion. Yields ``("text", delta)`` as tokens arrive, then ``("message", message)``
    with the full content and any tool calls reassembled from the stream."""
    c = _config()
    if c is None:
        raise LLMUnavailable("No language-model credentials are configured on the server.")
    body: dict[str, Any] = {
        "model": c["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        body["tools"] = tools
    content: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    try:
        with httpx.stream(
            "POST",
            f"{c['url']}/chat/completions",
            headers={"Authorization": f"Bearer {c['key']}", "Content-Type": "application/json"},
            content=json.dumps(body, default=str),
            timeout=httpx.Timeout(120.0, connect=15.0),
        ) as r:
            if r.status_code != 200:
                msg = _error_message(r.status_code, r.read())
                log.warning("LLM stream failed: HTTP %s %s", r.status_code, msg[:200])
                raise LLMUnavailable(msg)
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content.append(delta["content"])
                        yield ("text", delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(
                            tc.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        slot["function"]["name"] += fn.get("name") or ""
                        slot["function"]["arguments"] += fn.get("arguments") or ""
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"The language model could not be reached ({exc.__class__.__name__}).") from exc
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    yield ("message", msg)
