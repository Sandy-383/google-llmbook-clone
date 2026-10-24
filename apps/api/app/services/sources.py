import uuid
from collections.abc import Sequence
from pathlib import PurePath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notebook, Source


def title_from_filename(filename: str | None) -> str:
    # Browsers may send a full path (old IE/Edge); keep only the last component.
    name = PurePath((filename or "").replace("\\", "/")).name
    stem = PurePath(name).stem.strip()
    return (stem or "Untitled source")[:300]


class SourceService:
    """Sources are reached only through a notebook the user owns."""

    def __init__(self, db: AsyncSession, user_id: uuid.UUID) -> None:
        self.db = db
        self.user_id = user_id

    async def notebook(self, notebook_id: uuid.UUID) -> Notebook | None:
        return await self.db.scalar(
            select(Notebook).where(Notebook.id == notebook_id, Notebook.user_id == self.user_id)
        )

    async def list_all(self, notebook_id: uuid.UUID) -> Sequence[Source]:
        result = await self.db.scalars(
            select(Source)
            .join(Notebook, Notebook.id == Source.notebook_id)
            .where(Source.notebook_id == notebook_id, Notebook.user_id == self.user_id)
            .order_by(Source.created_at)
        )
        return result.all()

    async def get(self, notebook_id: uuid.UUID, source_id: uuid.UUID) -> Source | None:
        return await self.db.scalar(
            select(Source)
            .join(Notebook, Notebook.id == Source.notebook_id)
            .where(
                Source.id == source_id,
                Source.notebook_id == notebook_id,
                Notebook.user_id == self.user_id,
            )
        )

    async def count(self, notebook_id: uuid.UUID) -> int:
        count = await self.db.scalar(
            select(func.count()).select_from(Source).where(Source.notebook_id == notebook_id)
        )
        return count or 0
