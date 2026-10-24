import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import AuthSession, Note, Notebook, Source, User
from app.workers.tasks import cleanup_expired_sessions_async, requeue_stale_sources_async


async def test_cleanup_deletes_only_expired_sessions(db: AsyncSession, settings: Settings) -> None:
    user = User(email="alice@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    now = datetime.now(UTC)
    db.add_all(
        [
            AuthSession(id="expired", user_id=user.id, expires_at=now - timedelta(minutes=1)),
            AuthSession(id="live", user_id=user.id, expires_at=now + timedelta(days=1)),
        ]
    )
    await db.commit()

    assert await cleanup_expired_sessions_async(settings) == 1
    remaining = (await db.scalars(select(AuthSession.id))).all()
    assert remaining == ["live"]


async def test_stale_sources_are_requeued_or_failed(db: AsyncSession, settings: Settings) -> None:
    user = User(email="alice@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    notebook = Notebook(user_id=user.id, title="n")
    db.add(notebook)
    await db.flush()
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    recent = datetime.now(UTC)

    def source(status: str, updated_at: datetime, n: int) -> Source:
        return Source(
            notebook_id=notebook.id,
            kind="text",
            title=f"s{n}",
            storage_key=f"k{n}",
            size_bytes=1,
            sha256=str(n),
            status=status,
            updated_at=updated_at,
        )

    lost = source("queued", long_ago, 1)
    waiting = source("queued", recent, 2)
    orphaned = source("processing", long_ago, 3)
    working = source("processing", recent, 4)
    db.add_all([lost, waiting, orphaned, working])
    await db.commit()

    lost_guide = source("ready", long_ago, 5)
    lost_guide.guide_status = "pending"
    stuck_note = Note(
        notebook_id=notebook.id, kind="faq", title="FAQ", status="generating", updated_at=long_ago
    )
    db.add_all([lost_guide, stuck_note])
    await db.commit()

    enqueued: list[uuid.UUID] = []
    guides: list[uuid.UUID] = []
    swept = await requeue_stale_sources_async(
        settings, enqueue=enqueued.append, enqueue_guide=guides.append
    )
    assert swept == 1
    assert enqueued == [lost.id]
    assert guides == [lost_guide.id]
    await db.refresh(stuck_note)
    assert (stuck_note.status, stuck_note.error) == (
        "failed",
        "Generation was interrupted. Try again.",
    )

    db.expire_all()
    statuses = {s.id: s.status for s in (await db.scalars(select(Source))).all()}
    assert statuses == {
        lost.id: "queued",
        waiting.id: "queued",
        orphaned.id: "failed",
        working.id: "processing",
        lost_guide.id: "ready",
    }
