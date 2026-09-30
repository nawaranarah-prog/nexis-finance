"""Multi-provider AI client: ordering, failover on quota errors, streaming tool-call reassembly."""

from __future__ import annotations

import contextlib
import json

import httpx
import pytest

from app.services import llm

KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "GITHUB_MODELS_TOKEN",
        "AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN", "GEMINI_MODEL", "GROQ_MODEL")  # fmt: skip


@pytest.fixture(autouse=True)
def _clean(monkeypatch):  # type: ignore[no-untyped-def]
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    llm.set_request_token(None)
    llm._cooldown.clear()
    yield
    llm._cooldown.clear()


class FakeStream:
    def __init__(self, status: int, lines: list[str] | None = None, body: bytes = b"") -> None:
        self.status_code, self._lines, self._body = status, lines or [], body

    def read(self) -> bytes:
        return self._body

    def iter_lines(self):  # type: ignore[no-untyped-def]
        yield from self._lines


def _sse(*chunks: dict) -> list[str]:
    return [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]


def test_providers_are_ordered_and_reported(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    st = llm.status()
    assert st["configured"] and st["provider"] == "Google Gemini" and st["model"] == "gemini-2.5-pro"
    assert st["providers"] == ["Google Gemini", "Groq"]
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3-pro,gemini-2.5-flash")
    assert [t["model"] for t in llm._targets() if t["provider"] == "Google Gemini"] == ["gemini-3-pro", "gemini-2.5-flash"]


def test_stream_fails_over_to_next_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    calls = []

    @contextlib.contextmanager
    def fake_stream(method, url, headers=None, content=None, timeout=None):  # type: ignore[no-untyped-def]
        model = json.loads(content)["model"]
        calls.append((url.split("/")[2], model))
        if "googleapis" in url:
            yield FakeStream(429, body=json.dumps([{"error": {"code": 429, "message": "Resource has been exhausted"}}]).encode())
        else:
            yield FakeStream(
                200, _sse({"choices": [{"delta": {"content": "Hello "}}]}, {"choices": [{"delta": {"content": "there"}}]})
            )

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)
    events = list(llm.chat_stream([{"role": "user", "content": "hi"}]))
    assert [e for e in events if e[0] == "text"] == [("text", "Hello "), ("text", "there")]
    final = events[-1][1]
    assert final["content"] == "Hello there" and final["_provider"] == "Groq"
    assert calls[:2] == [
        ("generativelanguage.googleapis.com", "gemini-2.5-pro"),
        ("generativelanguage.googleapis.com", "gemini-2.5-flash"),
    ]
    # The failed models are cooled down, so the next request goes straight to Groq.
    calls.clear()
    list(llm.chat_stream([{"role": "user", "content": "again"}]))
    assert calls[0][0] == "api.groq.com"


def test_stream_reassembles_tool_calls(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    @contextlib.contextmanager
    def fake_stream(*a, **k):  # type: ignore[no-untyped-def]
        yield FakeStream(200, _sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_instrument", "arguments": '{"sym'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'bol": "EMAAR.AE"}'}}]}}]},
        ))  # fmt: skip

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)
    msg = list(llm.chat_stream([{"role": "user", "content": "emaar"}], tools=[{"type": "function"}]))[-1][1]
    assert msg["tool_calls"][0]["function"] == {"name": "get_instrument", "arguments": '{"symbol": "EMAAR.AE"}'}
    assert msg["tool_calls"][0]["id"] == "c1"


def test_all_providers_failing_raises_with_reasons(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: httpx.Response(401, json={"error": {"message": "Invalid API Key"}}))
    with pytest.raises(llm.LLMUnavailable, match="Groq: Invalid API Key"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_no_keys_means_not_configured():
    assert llm.status()["configured"] is False
    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "hi"}])
