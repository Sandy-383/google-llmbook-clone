import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


def _with_tag(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


class LLMUnavailableError(Exception):
    """Ollama can't be reached (not running, restarting, wrong URL)."""


class ModelMissingError(Exception):
    """The requested model isn't pulled in Ollama."""


class LLMError(Exception):
    """Ollama reported an error while generating."""


@dataclass(frozen=True)
class ChatStats:
    prompt_tokens: int
    output_tokens: int
    load_ms: int
    prompt_ms: int
    generation_ms: int


@dataclass(frozen=True)
class ChatChunk:
    """A piece of a streamed answer; the last one carries `stats`."""

    text: str = ""
    stats: ChatStats | None = None


Message = dict[str, str]


class OllamaClient:
    """Thin client for the local Ollama server."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http
        self._capabilities: dict[str, list[str]] = {}

    async def list_models(self) -> list[str]:
        resp = await self._http.get("/api/tags", timeout=3.0)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    async def missing_models(self, required: list[str]) -> list[str]:
        available = {_with_tag(m) for m in await self.list_models()}
        return [m for m in required if _with_tag(m) not in available]

    async def embed(
        self, texts: list[str], *, model: str, keep_alive: str, cpu_only: bool = False
    ) -> list[list[float]]:
        """Embed a batch. `cpu_only` keeps the model off the GPU: on an 8 GB card the chat model
        and the embedding model don't fit together, so query-time embedding runs on the CPU."""
        payload: dict[str, object] = {"model": model, "input": texts, "keep_alive": keep_alive}
        if cpu_only:
            payload["options"] = {"num_gpu": 0}
        try:
            resp = await self._http.post("/api/embed", json=payload, timeout=300.0)
        except httpx.TransportError as exc:
            raise LLMUnavailableError(str(exc)) from exc
        if resp.status_code == 404:
            raise ModelMissingError(model)
        resp.raise_for_status()
        embeddings: list[list[float]] = resp.json()["embeddings"]
        return embeddings

    async def supports_thinking(self, model: str) -> bool:
        if model not in self._capabilities:
            try:
                resp = await self._http.post("/api/show", json={"model": model}, timeout=10.0)
            except httpx.TransportError as exc:
                raise LLMUnavailableError(str(exc)) from exc
            if resp.status_code == 404:
                raise ModelMissingError(model)
            resp.raise_for_status()
            self._capabilities[model] = resp.json().get("capabilities", [])
        return "thinking" in self._capabilities[model]

    async def _payload(
        self,
        messages: list[Message],
        *,
        model: str,
        options: dict[str, Any],
        keep_alive: str,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": options,
            "keep_alive": keep_alive,
        }
        # Reasoning models (qwen3) think before answering; off, it costs ~3x the wait for no
        # measurable gain on grounded Q&A (see scripts/bench_models.py).
        if await self.supports_thinking(model):
            payload["think"] = False
        return payload

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        options: dict[str, Any],
        keep_alive: str,
        format: dict[str, Any] | None = None,
    ) -> str:
        """One complete reply. With `format` (a JSON Schema), decoding is constrained so the
        reply is always valid JSON of that shape."""
        payload = await self._payload(
            messages, model=model, options=options, keep_alive=keep_alive, stream=False
        )
        if format is not None:
            payload["format"] = format
        try:
            resp = await self._http.post("/api/chat", json=payload)
        except httpx.TransportError as exc:
            raise LLMUnavailableError(str(exc)) from exc
        if resp.status_code == 404:
            raise ModelMissingError(model)
        if resp.is_error:
            raise LLMError(resp.text[:300])
        content: str = resp.json()["message"]["content"]
        return content

    async def chat_stream(
        self, messages: list[Message], *, model: str, options: dict[str, Any], keep_alive: str
    ) -> AsyncIterator[ChatChunk]:
        """Stream an answer. Closing the iterator closes the connection, which makes Ollama stop
        generating, so an abandoned answer doesn't keep the GPU busy."""
        payload = await self._payload(
            messages, model=model, options=options, keep_alive=keep_alive, stream=True
        )
        try:
            async with self._http.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code == 404:
                    raise ModelMissingError(model)
                if resp.is_error:
                    raise LLMError((await resp.aread()).decode(errors="replace")[:300])
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise LLMError(str(chunk["error"])[:300])
                    text = chunk.get("message", {}).get("content", "")
                    if chunk.get("done"):
                        yield ChatChunk(text=text, stats=_stats(chunk))
                    elif text:
                        yield ChatChunk(text=text)
        except httpx.TransportError as exc:
            raise LLMUnavailableError(str(exc)) from exc


def _stats(chunk: dict[str, Any]) -> ChatStats:
    def ms(key: str) -> int:
        return int(chunk.get(key, 0) / 1_000_000)

    return ChatStats(
        prompt_tokens=chunk.get("prompt_eval_count", 0),
        output_tokens=chunk.get("eval_count", 0),
        load_ms=ms("load_duration"),
        prompt_ms=ms("prompt_eval_duration"),
        generation_ms=ms("eval_duration"),
    )
