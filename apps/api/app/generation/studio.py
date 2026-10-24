"""Studio outputs: study guide, FAQ, briefing doc and timeline, saved as notes.

Built from the source guides' per-section notes rather than the full text: a notebook's
documents rarely fit in the context, and the notes already condense each part of each source.
"""

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Note, Source
from app.generation.scheduling import TransientGenerationError, wait_for_chat_idle
from app.llm.ollama import LLMError, LLMUnavailableError, ModelMissingError, OllamaClient
from app.llm.options import chat_options
from app.retrieval.search import prompt_tokens

log = structlog.get_logger(__name__)

Sessions = async_sessionmaker[AsyncSession]

STUDIO_PROMPT = """You create study material from a person's sources. Use only the information in the source notes provided; do not add outside knowledge. When a point comes from a particular source, name the source in parentheses, for example (Employee Handbook). Write in the language of the sources. Format the result with Markdown headings and lists."""  # noqa: E501


@dataclass(frozen=True)
class StudioKind:
    title: str
    instructions: str


STUDIO_KINDS: dict[str, StudioKind] = {
    "study_guide": StudioKind(
        "Study guide",
        "Write a study guide with these sections: ## Key concepts (each concept in bold, then a "
        "one or two sentence explanation), ## Glossary (important terms with short definitions), "
        "## Quiz (5 short-answer questions) and ## Answer key (the answers, numbered to match).",
    ),
    "faq": StudioKind(
        "FAQ",
        "Write an FAQ: the 8 questions a newcomer to these sources would most likely ask. Give "
        "each question as a ### heading followed by a 2 to 3 sentence answer.",
    ),
    "briefing": StudioKind(
        "Briefing doc",
        "Write a briefing document with these sections: ## Executive summary, ## Key themes, "
        "## Important facts and figures, ## Open questions. Be concise and factual.",
    ),
    "timeline": StudioKind(
        "Timeline",
        "Write a timeline of the events the sources give an explicit date or time for, in "
        "chronological order, as a Markdown list. Start each item with the date in bold, then "
        "the event, then the source's title in parentheses. Never invent, infer or repeat a date "
        "to fill the list: leave out anything without its own date. Then add a ## People section "
        "listing the people mentioned, one line each. If the sources date no events, say that in "
        "one sentence instead of a list.",
    ),
}

_PROMPT_OVERHEAD_TOKENS = 300

NO_TIMELINE = (
    "The selected sources don't give dates for any events, so there's no timeline to show."
)
# A timeline entry: an optional list marker, then the date in bold.
_DATED_LINE = re.compile(r"^\s*(?:[-*+]\s*)?\*\*(?P<date>[^*\n]{2,40})\*\*")


def keep_grounded_dates(markdown: str, material: str) -> tuple[str, int]:
    """Drop timeline entries whose date doesn't appear in the material the model was given.

    Small models fill a timeline with invented dates rather than say there are none (a policy
    document came back with every rule dated "2023-04-01"), so this is checked, not trusted.
    Conservative: a date reworded by the model ("2013-08" for "August 2013") is dropped too.
    Returns the cleaned text and how many entries were dropped.
    """
    haystack = " ".join(material.lower().split())
    kept_lines: list[str] = []
    kept = dropped = 0
    for line in markdown.splitlines():
        match = _DATED_LINE.match(line)
        if match is None:
            kept_lines.append(line)
            continue
        date = " ".join(match.group("date").strip().rstrip(":").split()).lower()
        if date and date in haystack:
            kept_lines.append(line)
            kept += 1
        else:
            dropped += 1
    if kept == 0:
        # Without real events the model's "People" list is padding too (it listed "Full-time
        # employees" for a policy document), so say plainly that there is no timeline.
        return NO_TIMELINE, dropped
    return "\n".join(kept_lines).strip(), dropped


class GuidesNotReadyError(Exception):
    """Some selected sources are still being summarised; the job should try again shortly."""


def build_material(guides: list[tuple[str, dict[str, Any]]], budget: int) -> tuple[str, list[str]]:
    """The notes to write from, within a token budget, and the titles that made it in.

    Every source's full section notes if they fit. Otherwise each source gets at least its
    summary and topics, and as many as fit (smallest first) get their full notes back.
    """

    def full(title: str, guide: dict[str, Any]) -> str:
        notes = "\n".join(str(s.get("notes", "")) for s in guide.get("sections", []))
        return f"## {title}\n{guide.get('summary', '')}\n\n{notes}".strip()

    def brief(title: str, guide: dict[str, Any]) -> str:
        topics = ", ".join(guide.get("topics", []))
        return f"## {title}\n{guide.get('summary', '')}\nKey topics: {topics}".strip()

    fulls = [full(t, g) for t, g in guides]
    if sum(prompt_tokens(f) for f in fulls) <= budget:
        return "\n\n".join(fulls), [t for t, _ in guides]

    parts = [brief(t, g) for t, g in guides]
    used = sum(prompt_tokens(p) for p in parts)
    for i in sorted(range(len(guides)), key=lambda i: prompt_tokens(fulls[i])):
        extra = prompt_tokens(fulls[i]) - prompt_tokens(parts[i])
        if used + extra <= budget:
            parts[i] = fulls[i]
            used += extra

    # Even the summaries don't all fit (many large sources): keep as many as do.
    kept: list[str] = []
    titles: list[str] = []
    total = 0
    for (title, _), part in zip(guides, parts, strict=True):
        if total + prompt_tokens(part) > budget:
            continue
        kept.append(part)
        titles.append(title)
        total += prompt_tokens(part)
    return "\n\n".join(kept), titles


async def generate_studio(
    note_id: uuid.UUID,
    *,
    sessions: Sessions,
    ollama: OllamaClient,
    redis: Redis,
    settings: Settings,
    final_attempt: bool = True,
) -> None:
    started = time.perf_counter()
    async with sessions() as db:
        note = await db.get(Note, note_id)
        if note is None or note.status != "generating":
            return
        note_kind = note.kind
        kind = STUDIO_KINDS[note_kind]
        meta: dict[str, Any] = dict(note.meta or {})
        requested = [uuid.UUID(str(s)) for s in meta.get("source_ids", [])]
        sources = (
            await db.scalars(
                select(Source)
                .where(
                    Source.notebook_id == note.notebook_id,
                    Source.id.in_(requested),
                    Source.status == "ready",
                )
                .order_by(Source.created_at)
            )
        ).all()

    if any(s.guide_status in (None, "pending") for s in sources):
        if final_attempt:
            await _fail(sessions, note_id, "The sources' guides took too long to prepare. Retry.")
            return
        raise GuidesNotReadyError
    guides = [(s.title, s.guide) for s in sources if s.guide_status == "ready" and s.guide]
    if not guides:
        await _fail(sessions, note_id, "None of the selected sources could be summarised.")
        return

    system = f"{STUDIO_PROMPT}\n\n{kind.instructions}"
    budget = (
        settings.chat_context_tokens
        - settings.studio_answer_tokens
        - prompt_tokens(system)
        - _PROMPT_OVERHEAD_TOKENS
    )
    material, used = build_material(guides, budget)
    try:
        await wait_for_chat_idle(redis)
        content = await ollama.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Source notes:\n\n{material}"},
            ],
            model=settings.chat_model,
            options=chat_options(
                settings, num_predict=settings.studio_answer_tokens, temperature=0.3
            ),
            keep_alive=settings.chat_keep_alive,
        )
    except LLMUnavailableError as exc:
        if not final_attempt:
            raise TransientGenerationError(str(exc)) from exc
        await _fail(sessions, note_id, "The local model server (Ollama) couldn't be reached.")
        return
    except ModelMissingError as exc:
        await _fail(sessions, note_id, f"The model '{exc}' isn't installed in Ollama.")
        return
    except LLMError:
        log.warning("studio_failed", note_id=str(note_id), exc_info=True)
        await _fail(sessions, note_id, "The model failed while writing this. Try again.")
        return

    content = content.strip()[: settings.max_note_chars]
    if not content:
        await _fail(sessions, note_id, "The model returned nothing. Try again.")
        return
    if note_kind == "timeline":
        content, meta["dropped_undated"] = keep_grounded_dates(content, material)
    meta.update(
        sources=used,
        omitted=[t for t, _ in guides if t not in used],
        model=settings.chat_model,
        seconds=round(time.perf_counter() - started, 1),
    )
    async with sessions() as db:
        await db.execute(
            update(Note)
            .where(Note.id == note_id, Note.status == "generating")
            .values(content=content, status="ready", error=None, meta=meta)
        )
        await db.commit()
    log.info("studio_ready", note_id=str(note_id), kind=kind.title, seconds=meta["seconds"])


async def _fail(sessions: Sessions, note_id: uuid.UUID, message: str) -> None:
    async with sessions() as db:
        await db.execute(
            update(Note)
            .where(Note.id == note_id, Note.status == "generating")
            .values(status="failed", error=message[:500])
        )
        await db.commit()
