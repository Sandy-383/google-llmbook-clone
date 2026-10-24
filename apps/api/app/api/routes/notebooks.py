import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, SettingsDep, StorageDep
from app.db.models import Notebook
from app.schemas.notebooks import NotebookCreate, NotebookOut, NotebookUpdate
from app.services.notebooks import NotebookLimitError, NotebookService

router = APIRouter(tags=["notebooks"])


def get_notebook_service(db: DbSession, user: CurrentUser) -> NotebookService:
    return NotebookService(db, user.id)


Notebooks = Annotated[NotebookService, Depends(get_notebook_service)]


async def _get_owned(notebooks: NotebookService, notebook_id: uuid.UUID) -> Notebook:
    notebook = await notebooks.get(notebook_id)
    if notebook is None:
        # 404 rather than 403, so other users' notebook IDs can't be probed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")
    return notebook


@router.get("")
async def list_notebooks(notebooks: Notebooks) -> list[NotebookOut]:
    return [NotebookOut.model_validate(n) for n in await notebooks.list_all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_notebook(
    body: NotebookCreate, notebooks: Notebooks, db: DbSession, settings: SettingsDep
) -> NotebookOut:
    try:
        notebook = await notebooks.create(body.title, max_per_user=settings.max_notebooks_per_user)
    except NotebookLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Notebook limit reached ({settings.max_notebooks_per_user})",
        ) from None
    await db.commit()
    return NotebookOut.model_validate(notebook)


@router.get("/{notebook_id}")
async def get_notebook(notebook_id: uuid.UUID, notebooks: Notebooks) -> NotebookOut:
    return NotebookOut.model_validate(await _get_owned(notebooks, notebook_id))


@router.patch("/{notebook_id}")
async def rename_notebook(
    notebook_id: uuid.UUID, body: NotebookUpdate, notebooks: Notebooks, db: DbSession
) -> NotebookOut:
    notebook = await notebooks.rename(await _get_owned(notebooks, notebook_id), body.title)
    await db.commit()
    return NotebookOut.model_validate(notebook)


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notebook(
    notebook_id: uuid.UUID, notebooks: Notebooks, db: DbSession, storage: StorageDep
) -> None:
    await notebooks.delete(await _get_owned(notebooks, notebook_id))
    await db.commit()  # sources and chunks go with it (ON DELETE CASCADE)...
    # ...but uploaded files live on disk, under the notebook's id.
    await asyncio.to_thread(storage.delete_tree, str(notebook_id))
