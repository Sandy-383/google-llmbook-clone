import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notebook


class NotebookLimitError(Exception):
    pass


class NotebookService:
    """Every notebook query is scoped to one user here, so routes can't forget the filter."""

    def __init__(self, db: AsyncSession, user_id: uuid.UUID) -> None:
        self.db = db
        self.user_id = user_id

    async def list_all(self) -> Sequence[Notebook]:
        result = await self.db.scalars(
            select(Notebook)
            .where(Notebook.user_id == self.user_id)
            .order_by(Notebook.updated_at.desc())
        )
        return result.all()

    async def get(self, notebook_id: uuid.UUID) -> Notebook | None:
        return await self.db.scalar(
            select(Notebook).where(Notebook.id == notebook_id, Notebook.user_id == self.user_id)
        )

    async def create(self, title: str, *, max_per_user: int) -> Notebook:
        count = await self.db.scalar(
            select(func.count()).select_from(Notebook).where(Notebook.user_id == self.user_id)
        )
        if (count or 0) >= max_per_user:
            raise NotebookLimitError
        notebook = Notebook(user_id=self.user_id, title=title)
        self.db.add(notebook)
        await self.db.flush()
        await self.db.refresh(notebook)
        return notebook

    async def rename(self, notebook: Notebook, title: str) -> Notebook:
        notebook.title = title
        await self.db.flush()
        await self.db.refresh(notebook)
        return notebook

    async def delete(self, notebook: Notebook) -> None:
        await self.db.delete(notebook)
