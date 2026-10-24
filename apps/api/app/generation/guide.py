"""Source guides: a summary, key topics and suggested questions for each source.

A whole document rarely fits the model's context, so this is map-reduce: consecutive passages
are grouped into windows of a few thousand tokens, each window is condensed into notes (map),
and the notes are combined into the guide (reduce). The notes are kept, because the studio builds
study guides and FAQs from them without rereading every document.
"""

import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Chunk, Source
from app.generation.scheduling import TransientGenerationError, wait_for_chat_idle
from app.llm.ollama import LLMError, LLMUnavailableError, ModelMissingError, OllamaClient
from app.llm.options import chat_options
from app.retrieval.search import merge_overlapping, prompt_tokens

log = structlog.get_logger(__name__)

Sessions = async_sessionmaker[AsyncSession]

MAP_PROMPT = """You are condensing one part of a document into notes that will be used later to describe the whole document.
Write 4 to 8 bullet points with the key facts, claims, names, numbers and dates in this part. Use only the text given. Write in the language of the text. Reply with the bullet points only."""  # noqa: E501

REDUCE_PROMPT = """You write a short guide to a document, from notes on its parts given in order.
Reply in JSON with:
- "summary": 3 to 5 sentences on what the document is and its main points.
- "topics": 4 to 8 key topics, each 1 to 4 words.
- "questions": 4 specific questions a reader could ask that the document answers, each under 15 words.
Use only the notes. Write in the language of the notes."""  # noqa: E501

GUIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "topics", "questions"],
}

_MAP_ANSWER_TOKENS = 350
_REDUCE_ANSWER_TOKENS = 800


class GuideError(Exception):
    """The model's guide was unusable. Permanent; the message is shown to the user."""


@dataclass(frozen=True)
class Window:
    text: str
    page_start: int | None
    page_end: int | None


def build_windows(
    chunks: Sequence[tuple[str, int | None, int | None]], max_tokens: int
) -> list[Window]:
    """Group consecutive chunks (text, page_start, page_end) into windows of at most
    `max_tokens`, dropping the overlap the chunker repeats between neighbours."""
    windows: list[Window] = []
    texts: list[str] = []
    pages: list[int] = []
    size = 0
    for text, page_start, page_end in chunks:
        cost = prompt_tokens(text)
        if texts and size + cost > max_tokens:
            windows.append(_window(texts, pages))
            texts, pages, size = [], [], 0
        texts.append(text)
        pages += [p for p in (page_start, page_end) if p is not None]
        size += cost
    if texts:
        windows.append(_window(texts, pages))
    return windows


def _window(texts: list[str], pages: list[int]) -> Window:
    return Window(merge_overlapping(texts), min(pages, default=None), max(pages, default=None))


def sample_windows(windows: list[Window], limit: int) -> list[Window]:
    """Evenly spread over the document, so a long book's guide covers its whole length."""
    if len(windows) <= limit:
        return windows
    step = len(windows) / limit
    return [windows[int(i * step)] for i in range(limit)]


def parse_guide(raw: str) -> dict[str, Any]:
    """Validate and tidy the model's JSON: trims, de-duplicates and caps each field."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuideError("The model returned an unreadable guide.") from exc
    if not isinstance(data, dict):
        raise GuideError("The model returned an unreadable guide.")

    def clean_list(value: object, limit: int, max_chars: int) -> list[str]:
        items: list[str] = []
        for item in value if isinstance(value, list) else []:
            text = " ".join(str(item).split()).strip(" -•*")[:max_chars]
            if text and text.lower() not in (i.lower() for i in items):
                items.append(text)
        return items[:limit]

    summary = " ".join(str(data.get("summary", "")).split())[:1500]
    topics = clean_list(data.get("topics"), 8, 60)
    questions = [
        q if q.endswith("?") else f"{q}?" for q in clean_list(data.get("questions"), 5, 200)
    ]
    if not summary:
        raise GuideError("The model returned an empty summary.")
    return {"summary": summary, "topics": topics, "questions": questions}


def _pages(window: Window) -> str:
    if window.page_start is None:
        return ""
    if window.page_start == window.page_end:
        return f", page {window.page_start}"
    return f", pages {window.page_start}-{window.page_end}"


async def generate_guide(
    source_id: uuid.UUID,
    *,
    sessions: Sessions,
    ollama: OllamaClient,
    redis: Redis,
    settings: Settings,
    final_attempt: bool = True,
) -> None:
    started = time.perf_counter()
    async with sessions() as db:
        source = await db.get(Source, source_id)
        if source is None or source.status != "ready":
            return
        title = source.title
        rows = [
            (text, page_start, page_end)
            for text, page_start, page_end in await db.execute(
                select(Chunk.text, Chunk.page_start, Chunk.page_end)
                .where(Chunk.source_id == source_id)
                .order_by(Chunk.ord)
            )
        ]

    all_windows = build_windows(rows, settings.guide_window_tokens)
    windows = sample_windows(all_windows, settings.guide_max_windows)
    try:
        sections = []
        for number, window in enumerate(windows, start=1):
            await wait_for_chat_idle(redis)
            notes = await ollama.chat(
                [
                    {"role": "system", "content": MAP_PROMPT},
                    {
                        "role": "user",
                        "content": f"Document: {title}\nPart {number} of {len(windows)}"
                        f"{_pages(window)}\n\n{window.text}",
                    },
                ],
                model=settings.chat_model,
                options=chat_options(settings, num_predict=_MAP_ANSWER_TOKENS, temperature=0.2),
                keep_alive=settings.chat_keep_alive,
            )
            sections.append(
                {
                    "page_start": window.page_start,
                    "page_end": window.page_end,
                    "notes": notes.strip(),
                }
            )

        await wait_for_chat_idle(redis)
        joined = "\n\n".join(
            f"Part {i}{_pages(windows[i - 1])}:\n{s['notes']}" for i, s in enumerate(sections, 1)
        )
        raw = await ollama.chat(
            [
                {"role": "system", "content": REDUCE_PROMPT},
                {"role": "user", "content": f"Document: {title}\n\nNotes:\n\n{joined}"},
            ],
            model=settings.chat_model,
            options=chat_options(settings, num_predict=_REDUCE_ANSWER_TOKENS, temperature=0.2),
            keep_alive=settings.chat_keep_alive,
            format=GUIDE_SCHEMA,
        )
        guide = parse_guide(raw)
    except LLMUnavailableError as exc:
        if not final_attempt:
            raise TransientGenerationError(str(exc)) from exc
        await _fail(sessions, source_id, "The local model server (Ollama) couldn't be reached.")
        return
    except ModelMissingError as exc:
        await _fail(sessions, source_id, f"The model '{exc}' isn't installed in Ollama.")
        return
    except (LLMError, GuideError) as exc:
        log.warning("guide_failed", source_id=str(source_id), exc_info=True)
        await _fail(sessions, source_id, str(exc) or "The model failed to write the guide.")
        return

    guide["sections"] = sections
    guide["model"] = settings.chat_model
    guide["generated_at"] = datetime.now(UTC).isoformat()
    guide["sampled"] = len(windows) < len(all_windows)
    async with sessions() as db:
        await db.execute(
            update(Source)
            .where(Source.id == source_id, Source.status == "ready")
            .values(guide_status="ready", guide=guide)
        )
        await db.commit()
    log.info(
        "guide_ready",
        source_id=str(source_id),
        windows=len(windows),
        of_windows=len(all_windows),
        seconds=round(time.perf_counter() - started, 1),
    )


async def _fail(sessions: Sessions, source_id: uuid.UUID, message: str) -> None:
    async with sessions() as db:
        await db.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(guide_status="failed", guide={"error": message[:500]})
        )
        await db.commit()
