import uuid

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Source
from app.storage import LocalStorage
from tests.conftest import ClientFactory
from tests.factories import HANDBOOK_PAGES, RecordingQueue, make_docx, make_pdf
from tests.utils import client_for, create_notebook, register, running_app, upload

PDF = make_pdf(HANDBOOK_PAGES)


async def test_upload_queues_a_source(
    client: httpx.AsyncClient, queue: RecordingQueue, storage: LocalStorage
) -> None:
    await register(client)
    notebook_id = await create_notebook(client)

    resp = await upload(client, notebook_id, PDF, filename="C:\\Users\\me\\Team Handbook.pdf")
    assert resp.status_code == 201, resp.text
    source = resp.json()
    assert source["kind"] == "pdf"
    assert source["title"] == "Team Handbook"
    assert source["status"] == "queued"
    assert source["size_bytes"] == len(PDF)
    assert queue.ingested == [uuid.UUID(source["id"])]
    assert storage.path(f"{notebook_id}/{source['id']}").read_bytes() == PDF

    listed = await client.get(f"/api/notebooks/{notebook_id}/sources")
    assert [s["id"] for s in listed.json()] == [source["id"]]


async def test_word_document_upload(client: httpx.AsyncClient) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    resp = await upload(client, notebook_id, make_docx(), filename="policy.docx")
    assert resp.status_code == 201
    assert resp.json()["kind"] == "docx"


async def test_unsupported_file_is_rejected_and_not_kept(
    client: httpx.AsyncClient, storage: LocalStorage, queue: RecordingQueue
) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    resp = await upload(client, notebook_id, b"\x89PNG\r\n\x1a\nxxxx", filename="photo.png")
    assert resp.status_code == 415
    assert "Unsupported file type" in resp.json()["detail"]
    assert queue.ingested == []
    notebook_dir = storage.path(notebook_id)
    assert not notebook_dir.exists() or not any(notebook_dir.iterdir())


async def test_empty_file_is_rejected(client: httpx.AsyncClient) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    assert (await upload(client, notebook_id, b"", filename="empty.txt")).status_code == 422


async def test_too_large_file_is_rejected(settings: Settings) -> None:
    small = settings.model_copy(update={"max_upload_mb": 1})
    async with running_app(small) as app, client_for(app) as client:
        await register(client)
        notebook_id = await create_notebook(client)
        resp = await upload(client, notebook_id, b"a" * (1024 * 1024 + 1), filename="big.txt")
        assert resp.status_code == 413


async def test_duplicate_file_is_rejected(client: httpx.AsyncClient) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    assert (await upload(client, notebook_id, PDF)).status_code == 201
    dup = await upload(client, notebook_id, PDF, filename="renamed.pdf")
    assert dup.status_code == 409
    # The same file in a different notebook is fine.
    other = await create_notebook(client, "Other")
    assert (await upload(client, other, PDF)).status_code == 201


async def test_source_limit_per_notebook(settings: Settings) -> None:
    limited = settings.model_copy(update={"max_sources_per_notebook": 1})
    async with running_app(limited) as app, client_for(app) as client:
        await register(client)
        notebook_id = await create_notebook(client)
        assert (await upload(client, notebook_id, PDF)).status_code == 201
        over = await upload(client, notebook_id, make_docx(), filename="b.docx")
        assert over.status_code == 409


async def test_pasted_text(client: httpx.AsyncClient, queue: RecordingQueue) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    resp = await client.post(
        f"/api/notebooks/{notebook_id}/sources/text",
        json={"title": " Meeting notes ", "text": "We agreed to ship on Friday."},
    )
    assert resp.status_code == 201
    assert resp.json()["kind"] == "text"
    assert resp.json()["title"] == "Meeting notes"
    assert len(queue.ingested) == 1


async def test_sources_are_private(make_client: ClientFactory) -> None:
    alice, bob = make_client(), make_client()
    await register(alice, email="alice@example.com")
    await register(bob, email="bob@example.com")
    notebook_id = await create_notebook(alice)
    source_id = (await upload(alice, notebook_id, PDF)).json()["id"]
    base = f"/api/notebooks/{notebook_id}/sources"

    assert (await bob.get(base)).status_code == 404
    assert (await upload(bob, notebook_id, make_docx(), filename="x.docx")).status_code == 404
    assert (await bob.get(f"{base}/{source_id}")).status_code == 404
    assert (await bob.get(f"{base}/{source_id}/file")).status_code == 404
    assert (await bob.delete(f"{base}/{source_id}")).status_code == 404
    assert (await alice.get(f"{base}/{source_id}")).status_code == 200


async def test_file_is_served_inline_and_sandboxed(client: httpx.AsyncClient) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    source_id = (await upload(client, notebook_id, PDF)).json()["id"]

    resp = await client.get(f"/api/notebooks/{notebook_id}/sources/{source_id}/file")
    assert resp.status_code == 200
    assert resp.content == PDF
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-security-policy"] == "sandbox"
    assert resp.headers["content-disposition"].startswith("inline")


async def test_delete_removes_row_and_file(
    client: httpx.AsyncClient, storage: LocalStorage
) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    source_id = (await upload(client, notebook_id, PDF)).json()["id"]
    path = storage.path(f"{notebook_id}/{source_id}")
    assert path.exists()

    resp = await client.delete(f"/api/notebooks/{notebook_id}/sources/{source_id}")
    assert resp.status_code == 204
    assert not path.exists()
    assert (await client.get(f"/api/notebooks/{notebook_id}/sources")).json() == []


async def test_only_failed_sources_can_be_retried(
    client: httpx.AsyncClient, queue: RecordingQueue, db: AsyncSession
) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    source_id = (await upload(client, notebook_id, PDF)).json()["id"]
    retry_url = f"/api/notebooks/{notebook_id}/sources/{source_id}/retry"

    assert (await client.post(retry_url)).status_code == 409

    await db.execute(
        update(Source)
        .where(Source.id == uuid.UUID(source_id))
        .values(status="failed", error="boom")
    )
    await db.commit()
    resp = await client.post(retry_url)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert resp.json()["error"] is None
    assert queue.ingested == [uuid.UUID(source_id)] * 2


async def test_deleting_a_notebook_deletes_its_sources_and_files(
    client: httpx.AsyncClient, db: AsyncSession, storage: LocalStorage
) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    keep_id = await create_notebook(client, "Keep")
    await upload(client, notebook_id, PDF)
    await upload(client, keep_id, PDF)
    assert storage.path(notebook_id).is_dir()

    assert (await client.delete(f"/api/notebooks/{notebook_id}")).status_code == 204
    titles = (await db.scalars(select(Source.notebook_id))).all()
    assert [str(t) for t in titles] == [keep_id]
    assert not storage.path(notebook_id).exists()
    assert any(storage.path(keep_id).iterdir())


def test_storage_refuses_to_delete_its_root(storage: LocalStorage) -> None:
    with pytest.raises(ValueError, match="storage root"):
        storage.delete_tree("")
    with pytest.raises(ValueError, match="Invalid storage key"):
        storage.delete_tree("../outside")
