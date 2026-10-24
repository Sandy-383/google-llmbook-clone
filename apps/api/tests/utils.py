from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.core.config import Settings
from app.ingestion.pipeline import Sessions, ingest_source
from app.main import create_app
from app.storage import LocalStorage
from tests.factories import RecordingQueue, fake_embed

DEFAULT_PASSWORD = "correct-horse-battery"


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        # Never publish to a real broker from tests.
        app.state.task_queue = RecordingQueue()
        yield app


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def register(
    client: httpx.AsyncClient, email: str = "alice@example.com", password: str = DEFAULT_PASSWORD
) -> httpx.Response:
    return await client.post("/api/auth/register", json={"email": email, "password": password})


async def create_notebook(client: httpx.AsyncClient, title: str = "Notebook") -> str:
    resp = await client.post("/api/notebooks", json={"title": title})
    assert resp.status_code == 201, resp.text
    notebook_id: str = resp.json()["id"]
    return notebook_id


async def ingest_queued(
    queue: RecordingQueue, sessions: Sessions, storage: LocalStorage, settings: Settings
) -> None:
    """Run the ingestion the API queued, as the worker would (with fake embeddings)."""
    for source_id in queue.ingested:
        await ingest_source(
            source_id,
            sessions=sessions,
            storage=storage,
            embed=fake_embed,
            settings=settings,
            ocr=None,
        )


async def upload(
    client: httpx.AsyncClient,
    notebook_id: str,
    data: bytes,
    filename: str = "handbook.pdf",
) -> httpx.Response:
    return await client.post(
        f"/api/notebooks/{notebook_id}/sources", files={"file": (filename, data)}
    )
