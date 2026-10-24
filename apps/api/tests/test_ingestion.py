import uuid

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import EMBED_DIM, Chunk, Source
from app.ingestion.pipeline import Embedder, Sessions, TransientIngestError, ingest_source
from app.storage import LocalStorage
from tests.factories import HANDBOOK_PAGES, fake_embed, make_docx, make_pdf
from tests.utils import create_notebook, register, upload


async def add_source(client: httpx.AsyncClient, data: bytes, filename: str) -> uuid.UUID:
    await register(client)
    notebook_id = await create_notebook(client)
    resp = await upload(client, notebook_id, data, filename=filename)
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


async def run(
    source_id: uuid.UUID,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    embed: Embedder = fake_embed,
    final_attempt: bool = True,
) -> None:
    await ingest_source(
        source_id,
        sessions=sessions,
        storage=storage,
        embed=embed,
        settings=settings,
        ocr=None,
        final_attempt=final_attempt,
    )


async def test_pdf_becomes_searchable_chunks(
    client: httpx.AsyncClient,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    db: AsyncSession,
) -> None:
    source_id = await add_source(client, make_pdf(HANDBOOK_PAGES), "handbook.pdf")
    await run(source_id, sessions, storage, settings)

    source = await db.get(Source, source_id)
    assert source is not None
    assert (source.status, source.progress, source.error, source.stage) == (
        "ready",
        100,
        None,
        None,
    )
    assert source.page_count == 4
    assert source.chunk_count >= 1
    assert source.token_count > 0

    chunks = (await db.scalars(select(Chunk).where(Chunk.source_id == source_id))).all()
    assert len(chunks) == source.chunk_count
    assert all(len(c.embedding) == EMBED_DIM for c in chunks)
    assert {c.page_start for c in chunks} <= {1, 2, 3, 4}
    leave = next(c for c in chunks if "27 days" in c.text)
    assert leave.page_start is not None and leave.page_end is not None
    assert leave.page_start <= 2 <= leave.page_end

    # The full-text index works (stemming: "employee" matches "employees").
    hits = await db.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.tsv.op("@@")(func.plainto_tsquery("english", "employee annual leave")))
    )
    assert hits == 1


async def test_word_document_keeps_section_headings(
    client: httpx.AsyncClient,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    db: AsyncSession,
) -> None:
    source_id = await add_source(client, make_docx(), "policy.docx")
    await run(source_id, sessions, storage, settings)

    chunks = (await db.scalars(select(Chunk).order_by(Chunk.ord))).all()
    assert [c.heading for c in chunks] == [
        "Employee Handbook › Travel",
        "Employee Handbook › Leave",
    ]
    assert all(c.page_start is None for c in chunks)


async def test_damaged_file_fails_with_a_readable_reason(
    client: httpx.AsyncClient,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    db: AsyncSession,
) -> None:
    source_id = await add_source(client, b"%PDF-1.4 truncated garbage", "broken.pdf")
    await run(source_id, sessions, storage, settings)

    source = await db.get(Source, source_id)
    assert source is not None
    assert source.status == "failed"
    assert source.error is not None and "couldn't be read as a PDF" in source.error
    assert source.stage is None


async def test_model_server_outage_is_retried_then_reported(
    client: httpx.AsyncClient,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    db: AsyncSession,
) -> None:
    source_id = await add_source(client, make_pdf(HANDBOOK_PAGES), "handbook.pdf")

    async def offline(texts: list[str]) -> list[list[float]]:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(TransientIngestError):
        await run(source_id, sessions, storage, settings, embed=offline, final_attempt=False)
    source = await db.get(Source, source_id)
    assert source is not None
    assert (source.status, source.stage) == ("queued", "Waiting for model server")

    await run(source_id, sessions, storage, settings, embed=offline, final_attempt=True)
    db.expire_all()
    source = await db.get(Source, source_id)
    assert source is not None
    assert source.status == "failed"
    assert source.error is not None and "Ollama" in source.error


async def test_wrong_embedding_size_fails_clearly(
    client: httpx.AsyncClient,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    db: AsyncSession,
) -> None:
    source_id = await add_source(client, make_pdf(HANDBOOK_PAGES), "handbook.pdf")

    async def small(texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    await run(source_id, sessions, storage, settings, embed=small)
    source = await db.get(Source, source_id)
    assert source is not None
    assert source.status == "failed"
    assert source.error is not None and "1024" in source.error


async def test_reprocessing_does_not_duplicate_chunks(
    client: httpx.AsyncClient,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    db: AsyncSession,
) -> None:
    source_id = await add_source(client, make_pdf(HANDBOOK_PAGES), "handbook.pdf")
    await run(source_id, sessions, storage, settings)
    first = await db.scalar(select(func.count()).select_from(Chunk))

    # A duplicate delivery of a finished job is a no-op...
    await run(source_id, sessions, storage, settings)
    # ...and a forced re-run replaces chunks rather than adding to them.
    await db.execute(text("UPDATE sources SET status = 'queued'"))
    await db.commit()
    await run(source_id, sessions, storage, settings)
    assert await db.scalar(select(func.count()).select_from(Chunk)) == first


async def test_source_deleted_before_processing_is_skipped(
    sessions: Sessions, storage: LocalStorage, settings: Settings
) -> None:
    await run(uuid.uuid4(), sessions, storage, settings)  # no error, nothing to do
