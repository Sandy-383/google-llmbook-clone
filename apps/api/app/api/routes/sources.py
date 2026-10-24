import asyncio
import io
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession, RedisDep, SettingsDep, StorageDep, TaskQueueDep
from app.core import ratelimit
from app.db.models import Chunk, Notebook, Source
from app.ingestion.sniff import UnsupportedFileError, detect_kind
from app.retrieval.search import merge_overlapping
from app.schemas.sources import SourceContentOut, SourceOut, SourceSection, TextSourceIn
from app.services.sources import SourceService, title_from_filename
from app.storage import FileTooLargeError, StoredFile
from app.workers.queue import TaskQueue

log = structlog.get_logger(__name__)
router = APIRouter(tags=["sources"])

_MB = 1024 * 1024
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text": "text/plain; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
}


def get_source_service(db: DbSession, user: CurrentUser) -> SourceService:
    return SourceService(db, user.id)


Sources = Annotated[SourceService, Depends(get_source_service)]


async def _owned_notebook(sources: SourceService, notebook_id: uuid.UUID) -> Notebook:
    notebook = await sources.notebook(notebook_id)
    if notebook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")
    return notebook


async def _owned_source(
    sources: SourceService, notebook_id: uuid.UUID, source_id: uuid.UUID
) -> Source:
    source = await sources.get(notebook_id, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return source


async def _check_limits(
    sources: SourceService,
    notebook: Notebook,
    user: CurrentUser,
    redis: RedisDep,
    settings: SettingsDep,
) -> None:
    if await sources.count(notebook.id) >= settings.max_sources_per_notebook:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A notebook can hold up to {settings.max_sources_per_notebook} sources.",
        )
    retry_after = await ratelimit.hit(
        redis,
        f"rl:upload:user:{user.id}",
        limit=settings.upload_rate_limit,
        window_seconds=settings.upload_rate_window_seconds,
    )
    if retry_after:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many uploads. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


async def _enqueue(queue: TaskQueue, source_id: uuid.UUID) -> None:
    try:
        await queue.enqueue_ingest(source_id)
    except Exception:
        # The source stays "queued"; the periodic stale-source sweep will pick it up.
        log.warning("enqueue_failed", source_id=str(source_id), exc_info=True)


async def _create_source(
    db: DbSession,
    storage: StorageDep,
    queue: TaskQueue,
    *,
    source_id: uuid.UUID,
    notebook: Notebook,
    stored: StoredFile,
    kind: str,
    title: str,
    filename: str | None,
) -> Source:
    source = Source(
        id=source_id,
        notebook_id=notebook.id,
        kind=kind,
        title=title,
        filename=filename,
        storage_key=stored.key,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )
    db.add(source)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await asyncio.to_thread(storage.delete, stored.key)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This file is already in the notebook."
        ) from None
    await db.refresh(source)
    await _enqueue(queue, source.id)
    log.info("source_added", source_id=str(source.id), kind=kind, size_bytes=stored.size_bytes)
    return source


@router.get("")
async def list_sources(notebook_id: uuid.UUID, sources: Sources) -> list[SourceOut]:
    await _owned_notebook(sources, notebook_id)
    return [SourceOut.model_validate(s) for s in await sources.list_all(notebook_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_source(
    notebook_id: uuid.UUID,
    file: UploadFile,
    sources: Sources,
    user: CurrentUser,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    storage: StorageDep,
    queue: TaskQueueDep,
) -> SourceOut:
    notebook = await _owned_notebook(sources, notebook_id)
    await _check_limits(sources, notebook, user, redis, settings)

    source_id = uuid.uuid4()
    key = f"{notebook.id}/{source_id}"
    try:
        stored = await asyncio.to_thread(
            storage.save, key, file.file, max_bytes=settings.max_upload_mb * _MB
        )
    except FileTooLargeError:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"File is too large. The limit is {settings.max_upload_mb} MB.",
        ) from None
    if stored.size_bytes == 0:
        await asyncio.to_thread(storage.delete, key)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty.")
    try:
        kind = detect_kind(stored.head, file.filename)
    except UnsupportedFileError as exc:
        await asyncio.to_thread(storage.delete, key)
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None

    source = await _create_source(
        db,
        storage,
        queue,
        source_id=source_id,
        notebook=notebook,
        stored=stored,
        kind=kind,
        title=title_from_filename(file.filename),
        filename=(file.filename or "")[:255] or None,
    )
    return SourceOut.model_validate(source)


@router.post("/text", status_code=status.HTTP_201_CREATED)
async def add_text_source(
    notebook_id: uuid.UUID,
    body: TextSourceIn,
    sources: Sources,
    user: CurrentUser,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    storage: StorageDep,
    queue: TaskQueueDep,
) -> SourceOut:
    """Pasted text, stored as a UTF-8 file so it goes through the same pipeline as uploads."""
    if len(body.text) > settings.max_text_chars:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Text is too long. The limit is {settings.max_text_chars:,} characters.",
        )
    notebook = await _owned_notebook(sources, notebook_id)
    await _check_limits(sources, notebook, user, redis, settings)

    source_id = uuid.uuid4()
    data = io.BytesIO(body.text.encode("utf-8"))
    stored = await asyncio.to_thread(
        storage.save, f"{notebook.id}/{source_id}", data, max_bytes=settings.max_upload_mb * _MB
    )
    source = await _create_source(
        db,
        storage,
        queue,
        source_id=source_id,
        notebook=notebook,
        stored=stored,
        kind="text",
        title=body.title,
        filename=None,
    )
    return SourceOut.model_validate(source)


@router.get("/{source_id}")
async def get_source(notebook_id: uuid.UUID, source_id: uuid.UUID, sources: Sources) -> SourceOut:
    return SourceOut.model_validate(await _owned_source(sources, notebook_id, source_id))


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    notebook_id: uuid.UUID,
    source_id: uuid.UUID,
    sources: Sources,
    db: DbSession,
    storage: StorageDep,
) -> None:
    source = await _owned_source(sources, notebook_id, source_id)
    key = source.storage_key
    await db.delete(source)  # chunks go with it (ON DELETE CASCADE)
    await db.commit()
    await asyncio.to_thread(storage.delete, key)
    log.info("source_deleted", source_id=str(source_id))


@router.post("/{source_id}/retry")
async def retry_source(
    notebook_id: uuid.UUID,
    source_id: uuid.UUID,
    sources: Sources,
    db: DbSession,
    queue: TaskQueueDep,
) -> SourceOut:
    source = await _owned_source(sources, notebook_id, source_id)
    if source.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed sources can be retried.")
    source.status, source.stage, source.progress, source.error = "queued", None, 0, None
    await db.commit()
    await db.refresh(source)
    await _enqueue(queue, source.id)
    return SourceOut.model_validate(source)


@router.post("/{source_id}/guide")
async def regenerate_guide(
    notebook_id: uuid.UUID,
    source_id: uuid.UUID,
    sources: Sources,
    db: DbSession,
    queue: TaskQueueDep,
) -> SourceOut:
    """(Re)write the source guide, e.g. after it failed or for a source from before guides."""
    source = await _owned_source(sources, notebook_id, source_id)
    if source.status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "This source hasn't been processed yet.")
    if source.guide_status == "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "The guide is already being written.")
    source.guide_status, source.guide = "pending", None
    await db.commit()
    await db.refresh(source)
    try:
        await queue.enqueue_guide(source.id)
    except Exception:
        log.warning("enqueue_failed", source_id=str(source.id), exc_info=True)
    return SourceOut.model_validate(source)


@router.get("/{source_id}/content")
async def get_source_content(
    notebook_id: uuid.UUID, source_id: uuid.UUID, sources: Sources, db: DbSession
) -> SourceContentOut:
    """The extracted text, by section, for viewing sources that aren't PDFs. Rebuilt from the
    stored passages, with the overlap between consecutive passages removed."""
    source = await _owned_source(sources, notebook_id, source_id)
    if source.status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "This source hasn't been processed yet.")
    rows = await db.execute(
        select(Chunk.heading, Chunk.page_start, Chunk.text)
        .where(Chunk.source_id == source.id)
        .order_by(Chunk.ord)
    )
    sections: list[SourceSection] = []
    for heading, page, text in rows:
        if sections and sections[-1].heading == heading:
            sections[-1].text = merge_overlapping([sections[-1].text, text])
        else:
            sections.append(SourceSection(heading=heading, page=page, text=text))
    return SourceContentOut(sections=sections)


@router.get("/{source_id}/file")
async def get_source_file(
    notebook_id: uuid.UUID, source_id: uuid.UUID, sources: Sources, storage: StorageDep
) -> FileResponse:
    """The original file, for the in-app viewer. Served inline with a fixed type and a sandbox
    CSP, so an uploaded file can never run as a page on this origin."""
    source = await _owned_source(sources, notebook_id, source_id)
    path = storage.path(source.storage_key)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[source.kind],
        filename=source.filename or f"{source.title}.txt",
        content_disposition_type="inline",
        headers={"Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=300"},
    )
