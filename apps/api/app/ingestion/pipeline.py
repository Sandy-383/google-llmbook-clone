"""Turns an uploaded source into searchable chunks: read → parse (+OCR) → chunk → embed → store.

Runs in the Celery worker. Status and progress are written to the `sources` row as it goes, so
the UI can show them. Celery-agnostic so it can be tested directly.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy import delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import EMBED_DIM, Chunk, Source
from app.ingestion.chunking import ChunkDraft, chunk_document
from app.ingestion.document import ParseError
from app.ingestion.ocr import Ocr
from app.ingestion.parsers import parse_document
from app.llm.ollama import LLMUnavailableError, ModelMissingError
from app.storage import LocalStorage

log = structlog.get_logger(__name__)

Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]
Sessions = async_sessionmaker[AsyncSession]

# Share of the progress bar given to each phase.
_PARSE_END = 50
_EMBED_END = 98


class TransientIngestError(Exception):
    """Worth retrying later, e.g. the model server is down or restarting."""


@dataclass
class _Progress:
    stage: str
    percent: int = 0


async def ingest_source(
    source_id: uuid.UUID,
    *,
    sessions: Sessions,
    storage: LocalStorage,
    embed: Embedder,
    settings: Settings,
    ocr: Ocr | None,
    final_attempt: bool = True,
) -> bool:
    """Returns True when the source became ready (so its guide can be generated next)."""
    async with sessions() as db:
        source = await db.get(Source, source_id)
        if source is None:
            log.info("ingest_skipped_deleted", source_id=str(source_id))
            return False
        if source.status == "ready":  # duplicate delivery of an already finished job
            return False
        source.status, source.stage, source.progress, source.error = (
            "processing",
            "Reading file",
            0,
            None,
        )
        await db.commit()
        kind, key, title = source.kind, source.storage_key, source.title
        notebook_id = source.notebook_id

    progress = _Progress("Reading file")
    stop = asyncio.Event()
    publisher = asyncio.create_task(_publish_progress(sessions, source_id, progress, stop))
    log.info("ingest_started", source_id=str(source_id), kind=kind)
    try:
        try:
            ready = await _run(
                source_id,
                notebook_id,
                kind,
                key,
                title,
                progress,
                sessions,
                storage,
                embed,
                settings,
                ocr,
            )
        finally:
            # Stop publishing before any final status write, so it can't be overwritten.
            stop.set()
            await publisher
    except ParseError as exc:
        await mark_failed(sessions, source_id, str(exc))
        return False
    except TransientIngestError:
        if final_attempt:
            await mark_failed(
                sessions,
                source_id,
                "The local model server (Ollama) couldn't be reached. Retry once it's running.",
            )
            return False
        else:
            await _set_status(
                sessions, source_id, status="queued", stage="Waiting for model server"
            )
            raise
    except Exception:
        log.exception("ingest_crashed", source_id=str(source_id))
        await mark_failed(sessions, source_id, "Processing failed unexpectedly. Try again.")
        raise
    return ready


async def _run(
    source_id: uuid.UUID,
    notebook_id: uuid.UUID,
    kind: str,
    key: str,
    title: str,
    progress: _Progress,
    sessions: Sessions,
    storage: LocalStorage,
    embed: Embedder,
    settings: Settings,
    ocr: Ocr | None,
) -> bool:
    try:
        data = await asyncio.to_thread(storage.read_bytes, key)
    except FileNotFoundError as exc:
        raise ParseError(
            "The uploaded file is missing. Delete this source and upload it again."
        ) from exc

    def on_page(done: int, total: int) -> None:
        progress.stage = f"Reading page {done} of {total}"
        progress.percent = int(_PARSE_END * done / total)

    # Parsing and OCR are CPU-bound; a thread keeps the progress publisher running meanwhile.
    document = await asyncio.to_thread(
        parse_document,
        kind,
        data,
        max_pdf_pages=settings.max_pdf_pages,
        max_text_chars=settings.max_text_chars,
        ocr=ocr,
        on_page=on_page,
    )
    del data

    progress.stage, progress.percent = "Splitting into passages", _PARSE_END
    drafts = chunk_document(
        document,
        target_tokens=settings.chunk_target_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    if not drafts:
        hint = (
            " It looks like a scanned document, and text recognition (OCR) isn't available."
            if kind == "pdf" and ocr is None
            else ""
        )
        raise ParseError(f"No readable text was found in this file.{hint}")

    vectors = await _embed_all(drafts, title, embed, settings.embed_batch_size, progress)

    progress.stage, progress.percent = "Saving", _EMBED_END
    rows = [
        {
            "source_id": source_id,
            "notebook_id": notebook_id,
            "ord": ord_,
            "text": draft.text,
            "heading": draft.heading,
            "page_start": draft.page_start,
            "page_end": draft.page_end,
            "token_count": draft.token_count,
            "embedding": vector,
        }
        for ord_, (draft, vector) in enumerate(zip(drafts, vectors, strict=True))
    ]
    async with sessions() as db:
        # Lock the row: a duplicate job for the same source waits instead of interleaving.
        source = await db.get(Source, source_id, with_for_update=True)
        if source is None:  # deleted while we were working
            return False
        await db.execute(delete(Chunk).where(Chunk.source_id == source_id))
        await db.execute(insert(Chunk), rows)
        source.status, source.stage, source.progress, source.error = "ready", None, 100, None
        source.guide_status, source.guide = "pending", None
        source.page_count = document.page_count
        source.ocr_page_count = document.ocr_page_count
        source.chunk_count = len(rows)
        source.token_count = sum(d.token_count for d in drafts)
        await db.commit()
    log.info(
        "ingest_finished",
        source_id=str(source_id),
        chunks=len(rows),
        pages=document.page_count,
        ocr_pages=document.ocr_page_count,
    )
    return True


def embedding_text(title: str, draft: ChunkDraft) -> str:
    """What gets embedded: the passage plus where it sits, so a chunk that says "it" or "the
    policy" can still be found by a question that names the document or section."""
    context = " › ".join(part for part in (title, draft.heading) if part)
    return f"{context}\n\n{draft.text}"


async def _embed_all(
    drafts: list[ChunkDraft],
    title: str,
    embed: Embedder,
    batch_size: int,
    progress: _Progress,
) -> list[list[float]]:
    texts = [embedding_text(title, d) for d in drafts]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        progress.stage = f"Indexing passage {start + 1} of {len(texts)}"
        try:
            batch = await embed(texts[start : start + batch_size])
        except ModelMissingError as exc:
            raise ParseError(
                "The embedding model isn't installed in Ollama (run `ollama pull bge-m3`)."
            ) from exc
        except (LLMUnavailableError, httpx.HTTPError) as exc:
            raise TransientIngestError(str(exc)) from exc
        if len(batch) != len(texts[start : start + batch_size]) or any(
            len(v) != EMBED_DIM for v in batch
        ):
            raise ParseError(
                f"The embedding model returned unexpected vectors (expected {EMBED_DIM} values). "
                "Check that EMBED_MODEL is bge-m3."
            )
        vectors.extend(batch)
        done = start + len(batch)
        progress.percent = _PARSE_END + int((_EMBED_END - _PARSE_END) * done / len(texts))
    return vectors


async def _publish_progress(
    sessions: Sessions, source_id: uuid.UUID, progress: _Progress, stop: asyncio.Event
) -> None:
    """Write progress to the database at most once a second, and only when it changed."""
    last: tuple[str, int] | None = None
    while not stop.is_set():
        current = (progress.stage, progress.percent)
        if current != last:
            try:
                async with sessions() as db:
                    # Conditional, so a late write can never touch a finished or failed source.
                    await db.execute(
                        update(Source)
                        .where(Source.id == source_id, Source.status == "processing")
                        .values(stage=current[0], progress=current[1])
                    )
                    await db.commit()
                last = current
            except Exception:  # progress is cosmetic; never let it kill the job
                log.warning("progress_update_failed", source_id=str(source_id), exc_info=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)


async def _set_status(sessions: Sessions, source_id: uuid.UUID, **values: object) -> None:
    async with sessions() as db:
        await db.execute(
            update(Source).where(Source.id == source_id, Source.status != "ready").values(**values)
        )
        await db.commit()


async def mark_failed(sessions: Sessions, source_id: uuid.UUID, message: str) -> None:
    log.info("ingest_failed", source_id=str(source_id), reason=message)
    await _set_status(sessions, source_id, status="failed", stage=None, error=message[:500])
