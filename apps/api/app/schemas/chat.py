import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ChatIn(BaseModel):
    message: Question
    # Sources to answer from; omitted means every ready source in the notebook.
    source_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class SearchIn(BaseModel):
    query: Question
    source_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class PassageOut(BaseModel):
    number: int
    source_id: uuid.UUID
    source_title: str
    page_start: int | None
    page_end: int | None
    heading: str | None
    text: str
    similarity: float | None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    status: str
    passages: list[PassageOut] | None
    meta: dict[str, object] | None
    created_at: datetime


class SearchOut(BaseModel):
    top_similarity: float | None
    passages: list[PassageOut]
