import uuid

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession, RedisDep, SettingsDep, TaskQueueDep
from app.core import ratelimit
from app.core.config import Settings
from app.db.models import Message, Note, Notebook, Source
from app.generation.studio import STUDIO_KINDS
from app.schemas.notes import NoteCreate, NoteFromMessage, NoteOut, NoteUpdate, StudioIn
from app.services.notebooks import NotebookService

log = structlog.get_logger(__name__)
router = APIRouter(tags=["notes"])


async def _owned_notebook(db: AsyncSession, user: CurrentUser, notebook_id: uuid.UUID) -> Notebook:
    notebook = await NotebookService(db, user.id).get(notebook_id)
    if notebook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")
    return notebook


async def _owned_note(
    db: AsyncSession, user: CurrentUser, notebook_id: uuid.UUID, note_id: uuid.UUID
) -> Note:
    await _owned_notebook(db, user, notebook_id)
    note = await db.scalar(select(Note).where(Note.id == note_id, Note.notebook_id == notebook_id))
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return note


async def _check_capacity(db: AsyncSession, notebook_id: uuid.UUID, settings: Settings) -> None:
    count = await db.scalar(
        select(func.count()).select_from(Note).where(Note.notebook_id == notebook_id)
    )
    if (count or 0) >= settings.max_notes_per_notebook:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A notebook can hold up to {settings.max_notes_per_notebook} notes.",
        )


async def _saved(db: AsyncSession, note: Note) -> NoteOut:
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return NoteOut.model_validate(note)


@router.get("/notes")
async def list_notes(notebook_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[NoteOut]:
    await _owned_notebook(db, user, notebook_id)
    notes = await db.scalars(
        select(Note).where(Note.notebook_id == notebook_id).order_by(Note.created_at.desc())
    )
    return [NoteOut.model_validate(n) for n in notes]


@router.post("/notes", status_code=status.HTTP_201_CREATED)
async def create_note(
    notebook_id: uuid.UUID,
    body: NoteCreate,
    user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
) -> NoteOut:
    await _owned_notebook(db, user, notebook_id)
    await _check_capacity(db, notebook_id, settings)
    note = Note(notebook_id=notebook_id, kind="manual", title=body.title, content=body.content)
    return await _saved(db, note)


@router.post("/notes/from-message", status_code=status.HTTP_201_CREATED)
async def save_answer_as_note(
    notebook_id: uuid.UUID,
    body: NoteFromMessage,
    user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
) -> NoteOut:
    """Save a chat answer, with its cited passages, so it survives clearing the chat."""
    await _owned_notebook(db, user, notebook_id)
    message = await db.scalar(
        select(Message).where(
            Message.id == body.message_id,
            Message.notebook_id == notebook_id,
            Message.role == "assistant",
        )
    )
    if message is None or not message.content:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    await _check_capacity(db, notebook_id, settings)
    # Titled after the question it answered.
    question = await db.scalar(
        select(Message.content)
        .where(
            Message.notebook_id == notebook_id,
            Message.role == "user",
            Message.created_at <= message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    title = " ".join((question or "Saved answer").split())
    note = Note(
        notebook_id=notebook_id,
        kind="answer",
        title=title if len(title) <= 120 else f"{title[:119]}…",
        content=message.content,
        passages=message.passages,
    )
    return await _saved(db, note)


@router.get("/notes/{note_id}")
async def get_note(
    notebook_id: uuid.UUID, note_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> NoteOut:
    return NoteOut.model_validate(await _owned_note(db, user, notebook_id, note_id))


@router.patch("/notes/{note_id}")
async def update_note(
    notebook_id: uuid.UUID,
    note_id: uuid.UUID,
    body: NoteUpdate,
    user: CurrentUser,
    db: DbSession,
) -> NoteOut:
    note = await _owned_note(db, user, notebook_id, note_id)
    if note.status == "generating":
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait until this note has been written.")
    if body.title is not None:
        note.title = body.title
    if body.content is not None:
        note.content = body.content
    return await _saved(db, note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    notebook_id: uuid.UUID, note_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    note = await _owned_note(db, user, notebook_id, note_id)
    await db.delete(note)
    await db.commit()


@router.post("/studio", status_code=status.HTTP_201_CREATED)
async def generate_studio_note(
    notebook_id: uuid.UUID,
    body: StudioIn,
    user: CurrentUser,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    queue: TaskQueueDep,
) -> NoteOut:
    """Start writing a study guide, FAQ, briefing or timeline from the selected sources. The note
    is created right away with status "generating" and filled in by a background job."""
    await _owned_notebook(db, user, notebook_id)
    retry_after = await ratelimit.hit(
        redis,
        f"rl:studio:user:{user.id}",
        limit=settings.studio_rate_limit,
        window_seconds=settings.studio_rate_window_seconds,
    )
    if retry_after:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many studio requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    query = select(Source).where(Source.notebook_id == notebook_id, Source.status == "ready")
    if body.source_ids is not None:
        query = query.where(Source.id.in_(body.source_ids))
    sources = (await db.scalars(query)).all()
    if not sources:
        raise HTTPException(status.HTTP_409_CONFLICT, "Select at least one processed source.")
    await _check_capacity(db, notebook_id, settings)

    # Sources processed before guides existed have none yet: ask for them now.
    missing = [s for s in sources if s.guide_status is None]
    for source in missing:
        source.guide_status = "pending"
    note = Note(
        notebook_id=notebook_id,
        kind=body.kind,
        title=STUDIO_KINDS[body.kind].title,
        status="generating",
        meta={"source_ids": [str(s.id) for s in sources]},
    )
    out = await _saved(db, note)
    try:
        for source in missing:
            await queue.enqueue_guide(source.id)
        await queue.enqueue_studio(note.id)
    except Exception:
        # The note stays "generating"; the periodic sweep fails it if the job never runs.
        log.warning("enqueue_failed", note_id=str(note.id), exc_info=True)
    log.info("studio_requested", kind=body.kind, sources=len(sources))
    return out
