"""Client for OpenAI-compatible chat-completions endpoints, with tool calling, streaming and failover.

Every provider below speaks the same protocol; each is enabled by its own key, and they are tried in
order (strongest first) — if one is rate-limited, out of quota or down, the next one answers:

=====================  ===========================  ===============================================
Provider               Environment variable          Default models (override with ``*_MODEL``)
=====================  ===========================  ===============================================
custom                 NEXIS_LLM_API_KEY (+ _BASE_URL) NEXIS_LLM_MODEL
Anthropic Claude       ANTHROPIC_API_KEY             claude-opus-5-5
OpenAI                 OPENAI_API_KEY                gpt-5
Google Gemini          GEMINI_API_KEY                gemini-2.5-pro, then gemini-2.5-flash (free tier)
Groq                   GROQ_API_KEY                  openai/gpt-oss-120b, llama-3.3-70b-versatile (free)
OpenRouter             OPENROUTER_API_KEY            openai/gpt-oss-120b:free, deepseek/deepseek-chat-v3.1:free
GitHub Models          GITHUB_MODELS_TOKEN           openai/gpt-4.1, openai/gpt-4.1-mini
Vercel AI Gateway      AI_GATEWAY_API_KEY / OIDC     NEXIS_LLM_MODEL (comma list; GPT-5.1 → GPT-5 → GPT-4.1)
=====================  ===========================  ===============================================

Nothing is sent anywhere unless a key is present. When every provider fails, :class:`LLMUnavailable`
carries a readable reason so callers can fall back to the data-only engine.
"""

from __future__ import annotations

import contextvars
import json
import os
import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
GATEWAY_URL = "https://ai-gateway.vercel.sh/v1"
# Set per request from the ``x-vercel-oidc-token`` header (Vercel injects it into function requests).
_request_oidc: contextvars.ContextVar[str | None] = contextvars.ContextVar("nexis_oidc", default=None)
# Providers that recently failed with a quota/auth error are skipped for a while (per instance).
_cooldown: dict[str, float] = {}
COOLDOWN_SECONDS = 600

PROVIDERS: list[dict[str, Any]] = [
    {"name": "Anthropic Claude", "key_env": "ANTHROPIC_API_KEY", "url": "https://api.anthropic.com/v1", "models": ["claude-opus-5-5", "claude-sonnet-5-5"]},
    {"name": "OpenAI", "key_env": "OPENAI_API_KEY", "url": "https://api.openai.com/v1", "models": ["gpt-5", "gpt-4.1"]},
    {"name": "Google Gemini", "key_env": "GEMINI_API_KEY", "url": "https://generativelanguage.googleapis.com/v1beta/openai", "models": ["gemini-2.5-pro", "gemini-2.5-flash"]},
    {"name": "Groq", "key_env": "GROQ_API_KEY", "url": "https://api.groq.com/openai/v1", "models": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]},
    {"name": "OpenRouter", "key_env": "OPENROUTER_API_KEY", "url": "https://openrouter.ai/api/v1", "models": ["openai/gpt-oss-120b:free", "deepseek/deepseek-chat-v3.1:free"]},
    {"name": "GitHub Models", "key_env": "GITHUB_MODELS_TOKEN", "url": "https://models.github.ai/inference", "models": ["openai/gpt-4.1", "openai/gpt-4.1-mini"]},
]  # fmt: skip


class LLMUnavailable(Exception):
    def __init__(self, reason: str, retryable: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def set_request_token(token: str | None) -> None:
    _request_oidc.set(token)


def _env_models(prefix: str, default: list[str]) -> list[str]:
    raw = os.environ.get(f"{prefix}_MODEL") or os.environ.get(f"{prefix}_MODELS")
    return [m.strip() for m in raw.split(",") if m.strip()] if raw else default


def _models(raw: str) -> list[str]:
    return [m.strip() for m in raw.split(",") if m.strip()]


def _targets(models: list[str] | None = None) -> list[dict[str, Any]]:
    """Every configured (provider, model) pair, strongest first. ``models`` overrides the model list."""
    s = get_settings()
    out: list[dict[str, Any]] = []
    if s.llm_api_key:
        for m in models or _models(s.llm_model):
            out.append(
                {"provider": "custom", "url": (s.llm_base_url or GATEWAY_URL).rstrip("/"), "key": s.llm_api_key, "model": m}
            )
    for p in PROVIDERS:
        key = os.environ.get(p["key_env"])
        if key:
            prefix = p["key_env"].rsplit("_", 2)[0] if p["key_env"].endswith("_API_KEY") else p["key_env"].rsplit("_", 1)[0]
            for m in _env_models(prefix, p["models"]):
                out.append({"provider": p["name"], "url": p["url"], "key": key, "model": m})
    token = os.environ.get("AI_GATEWAY_API_KEY") or _request_oidc.get() or os.environ.get("VERCEL_OIDC_TOKEN")
    if token:
        for m in models or _models(s.llm_model):
            out.append({"provider": "Vercel AI Gateway", "url": GATEWAY_URL, "key": token, "model": m})
    now = time.time()
    return [t for t in out if _cooldown.get(f"{t['provider']}:{t['model']}", 0) < now] or out


def status() -> dict[str, Any]:
    ts = _targets()
    if not ts:
        return {"configured": False, "provider": None, "model": None, "providers": [],
                "reason": "No AI provider key is configured on the server."}  # fmt: skip
    return {"configured": True, "provider": ts[0]["provider"], "model": ts[0]["model"],
            "providers": list(dict.fromkeys(t["provider"] for t in ts)), "reason": None}  # fmt: skip


def _error_message(status: int, raw: bytes, provider: str) -> tuple[str, bool]:
    """Readable reason plus whether the next provider should be tried."""
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, list) and err:
        err = err[0].get("error") if isinstance(err[0], dict) else err[0]
    msg = (err.get("message") if isinstance(err, dict) else str(err or "")) or f"HTTP {status}"
    if isinstance(err, dict) and err.get("type") == "customer_verification_required":
        msg = "the Vercel AI Gateway is not activated (a card must be added to unlock its free credits)"
    return f"{provider}: {msg[:200]}", True


def _body(
    t: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    temperature: float,
    stream: bool,
) -> str:
    body: dict[str, Any] = {"model": t["model"], "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if stream:
        body["stream"] = True
    if tools:
        body["tools"] = tools
    return json.dumps(body, default=str)


def _headers(t: dict[str, Any]) -> dict[str, str]:
    h = {"Authorization": f"Bearer {t['key']}", "Content-Type": "application/json"}
    if t["provider"] == "OpenRouter":
        h |= {"HTTP-Referer": get_settings().public_url or "https://nexis-finance-five.vercel.app", "X-Title": "Nexis Finance"}
    return h


def _fail(t: dict[str, Any], reason: str, errors: list[str]) -> None:
    errors.append(reason)
    _cooldown[f"{t['provider']}:{t['model']}"] = time.time() + COOLDOWN_SECONDS
    log.warning("LLM %s/%s failed: %s", t["provider"], t["model"], reason[:200])


def _no_targets() -> LLMUnavailable:
    return LLMUnavailable("No AI provider key is configured on the server.")


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 1800,
    temperature: float = 0.3,
    models: list[str] | None = None,
) -> dict[str, Any]:
    """One chat-completions call (first provider that answers); returns the assistant message."""
    targets = _targets(models)
    if not targets:
        raise _no_targets()
    errors: list[str] = []
    for t in targets:
        try:
            r = httpx.post(
                f"{t['url']}/chat/completions",
                headers=_headers(t),
                content=_body(t, messages, tools, max_tokens, temperature, False),
                timeout=90.0,
            )
        except httpx.HTTPError as exc:
            _fail(t, f"{t['provider']}: unreachable ({exc.__class__.__name__})", errors)
            continue
        if r.status_code != 200:
            reason, _ = _error_message(r.status_code, r.content, t["provider"])
            _fail(t, reason, errors)
            continue
        try:
            msg = r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError):
            _fail(t, f"{t['provider']}: unexpected response", errors)
            continue
        msg["_provider"], msg["_model"] = t["provider"], t["model"]
        return msg
    raise LLMUnavailable("; ".join(errors[-3:]))


def chat_stream(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, max_tokens: int = 3000, temperature: float = 0.4
) -> Iterator[tuple[str, Any]]:
    """Streaming chat completion with failover. Yields ``("text", delta)`` as tokens arrive, then
    ``("message", message)`` with the full content and any tool calls reassembled from the stream.
    Failover only happens before the first token; a provider that dies mid-answer raises."""
    targets = _targets()
    if not targets:
        raise _no_targets()
    errors: list[str] = []
    for t in targets:
        content: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        started = False
        try:
            with httpx.stream(
                "POST", f"{t['url']}/chat/completions", headers=_headers(t),
                content=_body(t, messages, tools, max_tokens, temperature, True), timeout=httpx.Timeout(120.0, connect=15.0),
            ) as r:  # fmt: skip
                if r.status_code != 200:
                    reason, _ = _error_message(r.status_code, r.read(), t["provider"])
                    _fail(t, reason, errors)
                    continue
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
                    if isinstance(chunk, dict) and chunk.get("error"):
                        raise LLMUnavailable(f"{t['provider']}: {str(chunk['error'])[:200]}")
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            started = True
                            content.append(delta["content"])
                            yield ("text", delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            started = True
                            slot = calls.setdefault(
                                tc.get("index", len(calls)),
                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            slot["function"]["name"] += fn.get("name") or ""
                            slot["function"]["arguments"] += fn.get("arguments") or ""
        except httpx.HTTPError as exc:
            if started:
                raise LLMUnavailable(f"{t['provider']}: connection lost ({exc.__class__.__name__})", retryable=False) from exc
            _fail(t, f"{t['provider']}: unreachable ({exc.__class__.__name__})", errors)
            continue
        except LLMUnavailable as exc:
            if started:
                raise
            _fail(t, exc.reason, errors)
            continue
        if not content and not calls:
            _fail(t, f"{t['provider']}: empty answer", errors)
            continue
        msg: dict[str, Any] = {"role": "assistant", "content": "".join(content), "_provider": t["provider"], "_model": t["model"]}
        if calls:
            for i, c in calls.items():
                c["id"] = c["id"] or f"call_{i}"
            msg["tool_calls"] = [calls[i] for i in sorted(calls)]
        yield ("message", msg)
        return
    raise LLMUnavailable("; ".join(errors[-3:]))
