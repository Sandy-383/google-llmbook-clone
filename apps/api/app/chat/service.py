"""Answers one question: rewrite (for follow-ups) → embed → retrieve → generate → save."""

import asyncio
import time
import uuid
from dataclasses import asdict, dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.citations import normalize_citations, strip_citations
from app.chat.prompts import NOT_FOUND, SYSTEM_PROMPT, Turn, answer_messages, rewrite_messages
from app.chat.stream import Emit
from app.core.config import Settings
from app.db.models import Message
from app.llm.ollama import LLMError, LLMUnavailableError, ModelMissingError, OllamaClient
from app.llm.options import chat_options
from app.retrieval.search import Passage, prompt_tokens, retrieve
from app.schemas.chat import MessageOut

log = structlog.get_logger(__name__)

Sessions = async_sessionmaker[AsyncSession]

_HISTORY_TOKEN_BUDGET = 1500
_HISTORY_MESSAGE_CHARS = 1500
# Source labels, separators and the chat template's own tokens.
_PROMPT_OVERHEAD_TOKENS = 300
_MIN_SOURCES_BUDGET = 800


@dataclass(frozen=True)
class AnswerJob:
    notebook_id: uuid.UUID
    question: str
    source_ids: list[uuid.UUID]
    history: list[Turn]


async def load_history(db: AsyncSession, notebook_id: uuid.UUID, turns: int) -> list[Turn]:
    """The last few completed exchanges, oldest first, trimmed to a token budget."""
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.notebook_id == notebook_id, Message.status == "complete")
            .order_by(Message.created_at.desc())
            .limit(turns * 2)
        )
    ).all()
    history = [
        Turn(m.role, strip_citations(m.content)[:_HISTORY_MESSAGE_CHARS]) for m in reversed(rows)
    ]
    while history and sum(prompt_tokens(t.content) for t in history) > _HISTORY_TOKEN_BUDGET:
        history = history[1:]
    # The model expects the conversation to start with the user.
    while history and history[0].role != "user":
        history = history[1:]
    return history


async def _search_query(job: AnswerJob, ollama: OllamaClient, settings: Settings) -> str:
    """Follow-ups like "what about the second one?" are rewritten into a standalone query, so
    retrieval knows what "the second one" is. Falls back to the question on any failure."""
    if not job.history:
        return job.question
    try:
        async with asyncio.timeout(30):
            rewritten = await ollama.chat(
                rewrite_messages(job.history, job.question),
                model=settings.chat_model,
                options=chat_options(settings, num_predict=64, temperature=0.0),
                keep_alive=settings.chat_keep_alive,
            )
    except (TimeoutError, LLMError, LLMUnavailableError, ModelMissingError):
        log.warning("query_rewrite_failed", exc_info=True)
        return job.question
    lines = [line for line in rewritten.strip().splitlines() if line.strip()]
    query = lines[0].strip().strip("\"'") if lines else ""
    return query[:500] or job.question


def _sources_budget(job: AnswerJob, settings: Settings) -> int:
    used = (
        settings.chat_max_answer_tokens
        + prompt_tokens(SYSTEM_PROMPT)
        + sum(prompt_tokens(t.content) for t in job.history)
        + prompt_tokens(job.question)
        + _PROMPT_OVERHEAD_TOKENS
    )
    return max(settings.chat_context_tokens - used, _MIN_SOURCES_BUDGET)


async def answer(
    job: AnswerJob, *, emit: Emit, sessions: Sessions, ollama: OllamaClient, settings: Settings
) -> None:
    """Never raises except on cancellation (Stop). Whatever happens, the assistant message is
    saved: complete, stopped (with the partial text) or error (with a readable reason)."""
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    meta: dict[str, object] = {"model": settings.chat_model}
    text = ""
    passages: list[Passage] = []
    status, error = "complete", None
    try:
        query = await _search_query(job, ollama, settings)
        meta["search_query"] = query
        await emit("searching", {"query": query})

        (vector,) = await ollama.embed(
            [query],
            model=settings.embed_model,
            keep_alive=settings.query_embed_keep_alive,
            cpu_only=settings.query_embed_on_cpu,
        )
        async with sessions() as db:
            result = await retrieve(
                db,
                notebook_id=job.notebook_id,
                source_ids=job.source_ids,
                query=query,
                query_vector=vector,
                candidates=settings.retrieval_candidates,
                top_k=settings.retrieval_top_k,
                token_budget=_sources_budget(job, settings),
            )
        passages = result.passages
        meta["top_similarity"] = result.top_similarity
        meta["retrieval_ms"] = elapsed_ms()
        await emit("sources", {"passages": [p.to_json() for p in passages]})

        if not passages or (result.top_similarity or 0.0) < settings.retrieval_min_similarity:
            # Nothing relevant: answer without the model, so it can't fill the gap from memory.
            text = NOT_FOUND
            meta["not_found"] = True
            await emit("token", {"text": text})
        else:
            async for chunk in ollama.chat_stream(
                answer_messages(job.history, passages, job.question),
                model=settings.chat_model,
                options=chat_options(
                    settings,
                    num_predict=settings.chat_max_answer_tokens,
                    temperature=settings.chat_temperature,
                ),
                keep_alive=settings.chat_keep_alive,
            ):
                if chunk.text:
                    meta.setdefault("first_token_ms", elapsed_ms())
                    text += chunk.text
                    await emit("token", {"text": chunk.text})
                if chunk.stats:
                    meta.update(asdict(chunk.stats))
                    if chunk.stats.prompt_tokens > settings.chat_context_tokens * 0.95:
                        log.warning("prompt_near_context_limit", tokens=chunk.stats.prompt_tokens)
    except asyncio.CancelledError:
        status = "stopped"
        raise
    except ModelMissingError as exc:
        status, error = "error", f"The model '{exc}' isn't installed. Run `ollama pull {exc}`."
    except LLMUnavailableError:
        status, error = (
            "error",
            "The local model server (Ollama) isn't reachable. Make sure it's running, then retry.",
        )
    except LLMError:
        log.warning("generation_failed", exc_info=True)
        status, error = "error", "The model failed while answering. Try again."
    except Exception:
        log.exception("answer_failed")
        status, error = "error", "Something went wrong while answering. Try again."
    finally:
        content, cited = normalize_citations(text, len(passages))
        meta.update(total_ms=elapsed_ms(), cited=cited, error=error)
        message = Message(
            notebook_id=job.notebook_id,
            role="assistant",
            content=content,
            status=status,
            passages=[p.to_json() for p in passages],
            meta=meta,
        )
        # Shielded: a second cancel must not lose the answer the user already watched appear.
        saved = await asyncio.shield(_save(sessions, message))
        log.info(
            "answer_finished",
            status=status,
            passages=len(passages),
            cited=len(cited),
            first_token_ms=meta.get("first_token_ms"),
            total_ms=meta["total_ms"],
        )
        if status == "complete":
            await emit("done", {"message": saved})
        elif status == "error":
            await emit("error", {"detail": error or "", "message": saved})


async def _save(sessions: Sessions, message: Message) -> dict[str, object]:
    async with sessions() as db:
        db.add(message)
        await db.commit()
        await db.refresh(message)
    return MessageOut.model_validate(message).model_dump(mode="json")
