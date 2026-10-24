import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.chat import PassageOut

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Content = Annotated[str, Field(max_length=50_000)]


class NoteCreate(BaseModel):
    title: Title
    content: Content = ""


class NoteFromMessage(BaseModel):
    message_id: uuid.UUID


class NoteUpdate(BaseModel):
    title: Title | None = None
    content: Content | None = None


class StudioIn(BaseModel):
    kind: Literal["study_guide", "faq", "briefing", "timeline"]
    # Sources to build from; omitted means every processed source in the notebook.
    source_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    title: str
    content: str
    status: str
    error: str | None
    passages: list[PassageOut] | None
    meta: dict[str, object] | None
    created_at: datetime
    updated_at: datetime
