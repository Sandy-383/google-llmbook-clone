import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.db.models import Note, Source
from app.db.session import build_engine, build_sessionmaker
from app.generation.guide import generate_guide
from app.generation.scheduling import TransientGenerationError
from app.generation.studio import GuidesNotReadyError, generate_studio
from app.ingestion.ocr import Ocr
from app.ingestion.pipeline import TransientIngestError, ingest_source, mark_failed
from app.llm.ollama import OllamaClient
from app.services import auth as auth_service
from app.storage import LocalStorage
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)

_INGEST_SOFT_LIMIT_S = 30 * 60
# A queued source untouched this long probably lost its enqueue (e.g. Redis blipped on upload).
_STALE_QUEUED = timedelta(minutes=15)
# Progress updates touch updated_at every few seconds, so a silent "processing" row is orphaned.
_STALE_PROCESSING = timedelta(hours=1)
# Guides and studio notes take minutes; longer than this, the job was lost.
_STALE_GENERATION = timedelta(minutes=45)


async def cleanup_expired_sessions_async(settings: Settings) -> int:
    # Each task run gets its own event loop, so it can't share a pooled engine.
    engine = build_engine(settings.database_url, null_pool=True)
    try:
        async with build_sessionmaker(engine)() as db:
            deleted = await auth_service.delete_expired_sessions(db)
            await db.commit()
    finally:
        await engine.dispose()
    log.info("expired_sessions_deleted", count=deleted)
    return deleted


@celery_app.task(name="maintenance.cleanup_expired_sessions")
def cleanup_expired_sessions() -> int:
    return asyncio.run(cleanup_expired_sessions_async(get_settings()))


async def ingest_source_async(
    source_id: uuid.UUID, settings: Settings, *, final_attempt: bool
) -> bool:
    engine = build_engine(settings.database_url, null_pool=True)
    http = httpx.AsyncClient(
        base_url=settings.ollama_base_url, timeout=httpx.Timeout(10.0, read=300.0)
    )
    ollama = OllamaClient(http)

    async def embed(texts: list[str]) -> list[list[float]]:
        # Bulk indexing runs on the GPU (~20x faster than CPU); it briefly evicts the chat model.
        return await ollama.embed(
            texts, model=settings.embed_model, keep_alive=settings.embed_keep_alive
        )

    ocr = Ocr(settings.ocr_languages) if settings.ocr_enabled and Ocr.available() else None
    try:
        return await ingest_source(
            source_id,
            sessions=build_sessionmaker(engine),
            storage=LocalStorage(settings.storage_dir),
            embed=embed,
            settings=settings,
            ocr=ocr,
            final_attempt=final_attempt,
        )
    finally:
        await http.aclose()
        await engine.dispose()


async def _mark_failed(source_id: uuid.UUID, settings: Settings, message: str) -> None:
    engine = build_engine(settings.database_url, null_pool=True)
    try:
        await mark_failed(build_sessionmaker(engine), source_id, message)
    finally:
        await engine.dispose()


@celery_app.task(
    name="ingest.source",
    bind=True,
    max_retries=5,
    soft_time_limit=_INGEST_SOFT_LIMIT_S,
    time_limit=_INGEST_SOFT_LIMIT_S + 60,
)
def ingest_source_task(self: Task, source_id: str) -> None:
    settings = get_settings()
    sid = uuid.UUID(source_id)
    try:
        ready = asyncio.run(
            ingest_source_async(
                sid, settings, final_attempt=self.request.retries >= self.max_retries
            )
        )
        if ready:
            _enqueue_guide(sid)
    except TransientIngestError as exc:
        # 30s, 60s, 120s, ... capped at 10 minutes.
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc
    except SoftTimeLimitExceeded:
        asyncio.run(
            _mark_failed(sid, settings, "Processing took longer than 30 minutes and was stopped.")
        )


def _enqueue_ingest(source_id: uuid.UUID) -> None:
    celery_app.send_task("ingest.source", args=[str(source_id)])


def _enqueue_guide(source_id: uuid.UUID) -> None:
    celery_app.send_task("guide.source", args=[str(source_id)])


async def _generation_context(
    settings: Settings,
) -> tuple[AsyncEngine, httpx.AsyncClient, Redis]:
    engine = build_engine(settings.database_url, null_pool=True)
    http = httpx.AsyncClient(
        base_url=settings.ollama_base_url, timeout=httpx.Timeout(10.0, read=600.0)
    )
    return engine, http, Redis.from_url(settings.redis_url)


async def generate_guide_async(
    source_id: uuid.UUID, settings: Settings, *, final_attempt: bool
) -> None:
    engine, http, redis = await _generation_context(settings)
    try:
        await generate_guide(
            source_id,
            sessions=build_sessionmaker(engine),
            ollama=OllamaClient(http),
            redis=redis,
            settings=settings,
            final_attempt=final_attempt,
        )
    finally:
        await redis.aclose()
        await http.aclose()
        await engine.dispose()


@celery_app.task(
    name="guide.source", bind=True, max_retries=5, soft_time_limit=1800, time_limit=1860
)
def generate_guide_task(self: Task, source_id: str) -> None:
    try:
        asyncio.run(
            generate_guide_async(
                uuid.UUID(source_id),
                get_settings(),
                final_attempt=self.request.retries >= self.max_retries,
            )
        )
    except TransientGenerationError as exc:
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc


async def generate_studio_async(
    note_id: uuid.UUID, settings: Settings, *, final_attempt: bool
) -> None:
    engine, http, redis = await _generation_context(settings)
    try:
        await generate_studio(
            note_id,
            sessions=build_sessionmaker(engine),
            ollama=OllamaClient(http),
            redis=redis,
            settings=settings,
            final_attempt=final_attempt,
        )
    finally:
        await redis.aclose()
        await http.aclose()
        await engine.dispose()


@celery_app.task(
    # Up to ~15 minutes of waiting for the sources' guides, at 30 s per retry.
    name="studio.generate",
    bind=True,
    max_retries=30,
    soft_time_limit=900,
    time_limit=960,
)
def generate_studio_task(self: Task, note_id: str) -> None:
    try:
        asyncio.run(
            generate_studio_async(
                uuid.UUID(note_id),
                get_settings(),
                final_attempt=self.request.retries >= self.max_retries,
            )
        )
    except GuidesNotReadyError as exc:
        raise self.retry(exc=exc, countdown=30) from exc
    except TransientGenerationError as exc:
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc


async def requeue_stale_sources_async(
    settings: Settings,
    *,
    enqueue: Callable[[uuid.UUID], None] = _enqueue_ingest,
    enqueue_guide: Callable[[uuid.UUID], None] = _enqueue_guide,
) -> int:
    """Re-enqueue sources stuck in "queued" and fail ones abandoned in "processing"; the same
    for source guides, and fail studio notes whose generation was abandoned."""
    now = datetime.now(UTC)
    engine = build_engine(settings.database_url, null_pool=True)
    try:
        async with build_sessionmaker(engine)() as db:
            stale = (
                await db.scalars(
                    select(Source.id).where(
                        Source.status == "queued", Source.updated_at < now - _STALE_QUEUED
                    )
                )
            ).all()
            stale_guides = (
                await db.scalars(
                    select(Source.id).where(
                        Source.status == "ready",
                        Source.guide_status == "pending",
                        Source.updated_at < now - _STALE_GENERATION,
                    )
                )
            ).all()
            if stale or stale_guides:
                # Touch them, so the next sweep doesn't enqueue them again straight away.
                await db.execute(
                    update(Source)
                    .where(Source.id.in_([*stale, *stale_guides]))
                    .values(updated_at=func.now())
                )
            await db.execute(
                update(Note)
                .where(Note.status == "generating", Note.updated_at < now - _STALE_GENERATION)
                .values(status="failed", error="Generation was interrupted. Try again.")
            )
            abandoned = (
                await db.scalars(
                    update(Source)
                    .where(
                        Source.status == "processing",
                        Source.updated_at < now - _STALE_PROCESSING,
                    )
                    .values(
                        status="failed",
                        stage=None,
                        error="Processing was interrupted. Retry to try again.",
                    )
                    .returning(Source.id)
                )
            ).all()
            await db.commit()
    finally:
        await engine.dispose()

    for source_id in stale:
        enqueue(source_id)
    for source_id in stale_guides:
        enqueue_guide(source_id)
    if stale or abandoned or stale_guides:
        log.info(
            "stale_sources_swept",
            requeued=len(stale),
            failed=len(abandoned),
            guides_requeued=len(stale_guides),
        )
    return len(stale)


@celery_app.task(name="ingest.requeue_stale")
def requeue_stale_sources() -> int:
    return asyncio.run(requeue_stale_sources_async(get_settings()))
