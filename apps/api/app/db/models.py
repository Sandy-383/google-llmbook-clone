import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# bge-m3 output size. Changing the embedding model means a new migration and re-embedding.
EMBED_DIM = 1024

SOURCE_KINDS = ("pdf", "docx", "text", "markdown")
SOURCE_STATUSES = ("queued", "processing", "ready", "failed")
MESSAGE_ROLES = ("user", "assistant")
MESSAGE_STATUSES = ("complete", "stopped", "error")
GUIDE_STATUSES = ("pending", "ready", "failed")
# Studio kinds are generated; "manual" notes are typed by the user, "answer" notes saved from chat.
NOTE_KINDS = ("manual", "answer", "study_guide", "faq", "briefing", "timeline")
NOTE_STATUSES = ("ready", "generating", "failed")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Stored lower-cased; uniqueness is case-insensitive by construction.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    # SHA-256 of the cookie token; the token itself is never stored.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(255))


class Notebook(TimestampMixin, Base):
    __tablename__ = "notebooks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))


class Source(TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        # The same file can't be added to one notebook twice.
        UniqueConstraint("notebook_id", "sha256"),
        CheckConstraint(f"kind IN {SOURCE_KINDS}", name="kind"),
        CheckConstraint(f"status IN {SOURCE_STATUSES}", name="status"),
        CheckConstraint(f"guide_status IN {GUIDE_STATUSES}", name="guide_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Lookups by notebook use the (notebook_id, sha256) unique index.
    notebook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notebooks.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str | None] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    # Human-readable step while processing, e.g. "Reading text (OCR)".
    stage: Mapped[str | None] = mapped_column(String(100))
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(String(500))

    page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Source guide (summary, topics, suggested questions, per-section notes); generated in the
    # background once the source is ready. NULL status: never requested (sources from before).
    guide_status: Mapped[str | None] = mapped_column(String(20))
    guide: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "ord"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Lookups by source use the (source_id, ord) unique index.
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    # Denormalised so retrieval can filter by notebook without a join.
    notebook_id: Mapped[uuid.UUID] = mapped_column(index=True)
    ord: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    heading: Mapped[str | None] = mapped_column(String(500))
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(heading, '') || ' ' || text)",
            persisted=True,
        ),
    )


class Message(Base):
    """One chat per notebook, as in NotebookLM. Assistant messages keep a copy of the passages
    they were given, so their citations still show text after a source is deleted."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_notebook_id_created_at", "notebook_id", "created_at"),
        CheckConstraint(f"role IN {MESSAGE_ROLES}", name="role"),
        CheckConstraint(f"status IN {MESSAGE_STATUSES}", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    notebook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notebooks.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="complete", server_default="complete")
    passages: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    # Search query used, model, token counts and timings: for debugging and evals.
    meta: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Note(TimestampMixin, Base):
    """A note in a notebook: typed by the user, saved from a chat answer (keeping its cited
    passages), or generated by the studio (study guide, FAQ, ...)."""

    __tablename__ = "notes"
    __table_args__ = (
        Index("ix_notes_notebook_id_created_at", "notebook_id", "created_at"),
        CheckConstraint(f"kind IN {NOTE_KINDS}", name="kind"),
        CheckConstraint(f"status IN {NOTE_STATUSES}", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    notebook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notebooks.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), default="ready", server_default="ready")
    error: Mapped[str | None] = mapped_column(String(500))
    passages: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    # Studio: the sources it was made from, model and timings.
    meta: Mapped[dict[str, object] | None] = mapped_column(JSONB)
