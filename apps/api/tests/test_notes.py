import httpx
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Note, Source
from app.ingestion.pipeline import Sessions
from app.storage import LocalStorage
from tests.conftest import ClientFactory
from tests.factories import HANDBOOK_PAGES, FakeOllama, RecordingQueue, make_pdf, parse_sse
from tests.utils import create_notebook, ingest_queued, register, upload


async def test_manual_notes_crud(client: httpx.AsyncClient) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    base = f"/api/notebooks/{notebook_id}/notes"

    created = await client.post(base, json={"title": " Ideas ", "content": "- first"})
    assert created.status_code == 201
    note = created.json()
    assert (note["kind"], note["title"], note["status"]) == ("manual", "Ideas", "ready")

    edited = await client.patch(f"{base}/{note['id']}", json={"content": "- first\n- second"})
    assert edited.json()["content"] == "- first\n- second"
    assert edited.json()["title"] == "Ideas"

    assert [n["id"] for n in (await client.get(base)).json()] == [note["id"]]
    assert (await client.delete(f"{base}/{note['id']}")).status_code == 204
    assert (await client.get(f"{base}/{note['id']}")).status_code == 404


async def test_note_validation(client: httpx.AsyncClient) -> None:
    await register(client)
    base = f"/api/notebooks/{await create_notebook(client)}/notes"
    assert (await client.post(base, json={"title": "   "})).status_code == 422
    assert (
        await client.post(base, json={"title": "x", "content": "y" * 50_001})
    ).status_code == 422


async def test_saving_an_answer_keeps_its_citations(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    app: FastAPI,
) -> None:
    app.state.ollama = FakeOllama()
    await register(client)
    notebook_id = await create_notebook(client)
    await upload(client, notebook_id, make_pdf(HANDBOOK_PAGES))
    await ingest_queued(queue, sessions, storage, settings)
    chat = await client.post(
        f"/api/notebooks/{notebook_id}/chat", json={"message": "How much annual leave?"}
    )
    events = parse_sse(chat.text)
    question = events[0][1]["message"]
    answer = events[-1][1]["message"]
    assert isinstance(question, dict) and isinstance(answer, dict)

    base = f"/api/notebooks/{notebook_id}/notes"
    saved = await client.post(f"{base}/from-message", json={"message_id": answer["id"]})
    assert saved.status_code == 201
    note = saved.json()
    assert note["kind"] == "answer"
    assert note["title"] == "How much annual leave?"
    assert note["content"] == answer["content"]
    assert note["passages"] == answer["passages"]

    # Only assistant answers can be saved this way.
    rejected = await client.post(f"{base}/from-message", json={"message_id": question["id"]})
    assert rejected.status_code == 404


async def test_studio_request_creates_a_generating_note(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    db: AsyncSession,
) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    await upload(client, notebook_id, make_pdf(HANDBOOK_PAGES))
    await ingest_queued(queue, sessions, storage, settings)
    # A source processed before guides existed has no guide yet.
    await db.execute(update(Source).values(guide_status=None))
    await db.commit()

    resp = await client.post(f"/api/notebooks/{notebook_id}/studio", json={"kind": "study_guide"})
    assert resp.status_code == 201
    note = resp.json()
    assert (note["kind"], note["title"], note["status"]) == (
        "study_guide",
        "Study guide",
        "generating",
    )
    assert [str(i) for i in queue.studios] == [note["id"]]
    # Its missing guide was requested first.
    assert queue.guides == queue.ingested
    assert (await db.scalars(select(Source.guide_status))).all() == ["pending"]

    # A note being written can't be edited.
    patch = await client.patch(
        f"/api/notebooks/{notebook_id}/notes/{note['id']}", json={"title": "x"}
    )
    assert patch.status_code == 409


async def test_studio_needs_a_processed_source_and_a_known_kind(client: httpx.AsyncClient) -> None:
    await register(client)
    base = f"/api/notebooks/{await create_notebook(client)}/studio"
    assert (await client.post(base, json={"kind": "faq"})).status_code == 409
    assert (await client.post(base, json={"kind": "poem"})).status_code == 422


async def test_regenerate_guide(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    db: AsyncSession,
) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    source_id = (await upload(client, notebook_id, make_pdf(HANDBOOK_PAGES))).json()["id"]
    await ingest_queued(queue, sessions, storage, settings)
    url = f"/api/notebooks/{notebook_id}/sources/{source_id}/guide"

    assert (await client.post(url)).status_code == 409  # already pending after ingestion
    await db.execute(update(Source).values(guide_status="failed", guide={"error": "boom"}))
    await db.commit()
    resp = await client.post(url)
    assert resp.status_code == 200
    assert (resp.json()["guide_status"], resp.json()["guide"]) == ("pending", None)
    assert [str(i) for i in queue.guides] == [source_id]


async def test_notes_are_private_and_go_with_their_notebook(
    make_client: ClientFactory, db: AsyncSession
) -> None:
    alice, bob = make_client(), make_client()
    await register(alice, email="alice@example.com")
    await register(bob, email="bob@example.com")
    notebook_id = await create_notebook(alice)
    base = f"/api/notebooks/{notebook_id}/notes"
    note_id = (await alice.post(base, json={"title": "Mine"})).json()["id"]

    assert (await bob.get(base)).status_code == 404
    assert (await bob.post(base, json={"title": "x"})).status_code == 404
    assert (await bob.get(f"{base}/{note_id}")).status_code == 404
    assert (await bob.patch(f"{base}/{note_id}", json={"title": "x"})).status_code == 404
    assert (await bob.delete(f"{base}/{note_id}")).status_code == 404
    assert (
        await bob.post(f"/api/notebooks/{notebook_id}/studio", json={"kind": "faq"})
    ).status_code == 404

    assert (await alice.delete(f"/api/notebooks/{notebook_id}")).status_code == 204
    assert (await db.scalars(select(Note))).all() == []
