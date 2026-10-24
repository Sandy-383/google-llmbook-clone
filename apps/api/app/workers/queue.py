import asyncio
import uuid
from typing import Protocol

from app.workers.celery_app import celery_app


class TaskQueue(Protocol):
    async def enqueue_ingest(self, source_id: uuid.UUID) -> None: ...

    async def enqueue_guide(self, source_id: uuid.UUID) -> None: ...

    async def enqueue_studio(self, note_id: uuid.UUID) -> None: ...


class CeleryTaskQueue:
    # send_task publishes to Redis synchronously; keep it off the event loop.
    async def enqueue_ingest(self, source_id: uuid.UUID) -> None:
        await asyncio.to_thread(celery_app.send_task, "ingest.source", args=[str(source_id)])

    async def enqueue_guide(self, source_id: uuid.UUID) -> None:
        await asyncio.to_thread(celery_app.send_task, "guide.source", args=[str(source_id)])

    async def enqueue_studio(self, note_id: uuid.UUID) -> None:
        await asyncio.to_thread(celery_app.send_task, "studio.generate", args=[str(note_id)])
