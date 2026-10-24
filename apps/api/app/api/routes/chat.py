import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession, OllamaDep, RedisDep, SettingsDep
from app.chat.lock import chat_lock_key
from app.chat.service import AnswerJob, answer, load_history
from app.chat.stream import Emit, relay
from app.core import ratelimit
from app.core.config import Settings
from app.db.models import Message, Notebook, Source
from app.llm.ollama import LLMUnavailableError, ModelMissingError
from app.retrieval.search import retrieve
from app.schemas.chat import ChatIn, MessageOut, PassageOut, SearchIn, SearchOut
from app.services.notebooks import NotebookService

router = APIRouter(tags=["chat"])

# Safety net only: the lock is released when the answer finishes, stops or fails.
_LOCK_TTL_S = 300
_HISTORY_LIMIT = 200


async def _owned_notebook(db: AsyncSession, user: CurrentUser, notebook_id: uuid.UUID) -> Notebook:
    notebook = await NotebookService(db, user.id).get(notebook_id)
    if notebook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")
    return notebook


async def _ready_sources(
    db: AsyncSession, notebook_id: uuid.UUID, requested: list[uuid.UUID] | None
) -> list[uuid.UUID]:
    query = select(Source.id).where(Source.notebook_id == notebook_id, Source.status == "ready")
    if requested is not None:
        query = query.where(Source.id.in_(requested))
    source_ids = list(await db.scalars(query))
    if not source_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, "Select at least one processed source.")
    return source_ids


async def _check_rate(redis: Redis, user: CurrentUser, settings: Settings, kind: str) -> None:
    retry_after = await ratelimit.hit(
        redis,
        f"rl:{kind}:user:{user.id}",
        limit=settings.chat_rate_limit,
        window_seconds=settings.chat_rate_window_seconds,
    )
    if retry_after:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You're asking questions too quickly. Try again in a little while.",
            headers={"Retry-After": str(retry_after)},
        )


@router.get("/messages")
async def list_messages(
    notebook_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[MessageOut]:
    await _owned_notebook(db, user, notebook_id)
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.notebook_id == notebook_id)
            .order_by(Message.created_at.desc())
            .limit(_HISTORY_LIMIT)
        )
    ).all()
    return [MessageOut.model_validate(m) for m in reversed(rows)]


@router.delete("/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_messages(
    notebook_id: uuid.UUID, user: CurrentUser, db: DbSession, redis: RedisDep
) -> None:
    await _owned_notebook(db, user, notebook_id)
    if await redis.exists(chat_lock_key(notebook_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the current answer to finish.")
    await db.execute(delete(Message).where(Message.notebook_id == notebook_id))
    await db.commit()


@router.post("/search")
async def search(
    notebook_id: uuid.UUID,
    body: SearchIn,
    user: CurrentUser,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    ollama: OllamaDep,
) -> SearchOut:
    """The passages chat would use for a query, without generating an answer."""
    await _owned_notebook(db, user, notebook_id)
    await _check_rate(redis, user, settings, "search")
    source_ids = await _ready_sources(db, notebook_id, body.source_ids)
    try:
        (vector,) = await ollama.embed(
            [body.query],
            model=settings.embed_model,
            keep_alive=settings.query_embed_keep_alive,
            cpu_only=settings.query_embed_on_cpu,
        )
    except (LLMUnavailableError, ModelMissingError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The local model server isn't available."
        ) from exc
    result = await retrieve(
        db,
        notebook_id=notebook_id,
        source_ids=source_ids,
        query=body.query,
        query_vector=vector,
        candidates=settings.retrieval_candidates,
        top_k=settings.retrieval_top_k,
        token_budget=settings.chat_context_tokens,
    )
    return SearchOut(
        top_similarity=result.top_similarity,
        passages=[PassageOut.model_validate(p.to_json()) for p in result.passages],
    )


@router.post("/chat")
async def chat(
    notebook_id: uuid.UUID,
    body: ChatIn,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    ollama: OllamaDep,
) -> StreamingResponse:
    """Answer a question as a stream of Server-Sent Events:

    `question` (the saved user message) → `searching` → `sources` (the numbered passages) →
    `token`… → `done` (the saved answer) or `error`. Closing the connection stops generation;
    the partial answer is kept as "stopped".
    """
    await _owned_notebook(db, user, notebook_id)
    await _check_rate(redis, user, settings, "chat")
    source_ids = await _ready_sources(db, notebook_id, body.source_ids)

    lock = chat_lock_key(notebook_id)
    if not await redis.set(lock, "1", nx=True, ex=_LOCK_TTL_S):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An answer is already being written in this notebook."
        )
    try:
        history = await load_history(db, notebook_id, settings.chat_history_turns)
        question = Message(notebook_id=notebook_id, role="user", content=body.message)
        db.add(question)
        await db.commit()
        await db.refresh(question)
    except BaseException:
        await redis.delete(lock)
        raise

    job = AnswerJob(notebook_id, body.message, source_ids, history)
    sessions = request.app.state.sessionmaker
    question_out = MessageOut.model_validate(question).model_dump(mode="json")

    async def produce(emit: Emit) -> None:
        try:
            await emit("question", {"message": question_out})
            await answer(job, emit=emit, sessions=sessions, ollama=ollama, settings=settings)
        finally:
            await asyncio.shield(redis.delete(lock))

    return StreamingResponse(
        relay(produce),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
