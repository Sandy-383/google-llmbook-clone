import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class TextSourceIn(BaseModel):
    title: Title
    text: str = Field(min_length=1)


class SourceSection(BaseModel):
    heading: str | None
    page: int | None
    text: str


class SourceContentOut(BaseModel):
    sections: list[SourceSection]


class SourceGuideOut(BaseModel):
    """What the UI shows. The per-section notes stay server-side (the studio uses them)."""

    summary: str = ""
    topics: list[str] = []
    questions: list[str] = []
    error: str | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    title: str
    filename: str | None
    size_bytes: int
    status: str
    stage: str | None
    progress: int
    error: str | None
    page_count: int | None
    ocr_page_count: int
    chunk_count: int
    guide_status: str | None
    guide: SourceGuideOut | None
    created_at: datetime
    updated_at: datetime
