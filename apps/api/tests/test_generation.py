import asyncio
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Note, Source
from app.generation.guide import (
    GUIDE_SCHEMA,
    GuideError,
    build_windows,
    generate_guide,
    parse_guide,
    sample_windows,
)
from app.generation.scheduling import TransientGenerationError, wait_for_chat_idle
from app.generation.studio import (
    NO_TIMELINE,
    GuidesNotReadyError,
    build_material,
    generate_studio,
    keep_grounded_dates,
)
from app.ingestion.pipeline import Sessions
from app.llm.ollama import LLMUnavailableError
from app.storage import LocalStorage
from tests.factories import (
    HANDBOOK_PAGES,
    FakeOllama,
    RecordingQueue,
    as_client,
    make_docx,
    make_pdf,
)
from tests.utils import create_notebook, ingest_queued, register, upload

GUIDE_JSON = json.dumps(
    {
        "summary": "An employee handbook covering travel, leave and security.",
        "topics": ["Travel", "Annual leave", "travel", " Security "],
        "questions": ["How many leave days do employees get", "When is business class allowed?"],
    }
)


# --- pure functions -----------------------------------------------------------------------------


def test_windows_group_chunks_and_drop_overlap() -> None:
    chunks = [
        ("Alpha one. Shared tail sentence here.", 1, 1),
        ("Shared tail sentence here. Beta two.", 1, 2),
        ("Gamma three.", 3, 3),
    ]
    windows = build_windows(chunks, max_tokens=26)
    assert [w.text for w in windows] == [
        "Alpha one. Shared tail sentence here. Beta two.",
        "Gamma three.",
    ]
    assert [(w.page_start, w.page_end) for w in windows] == [(1, 2), (3, 3)]


def test_long_documents_are_sampled_evenly() -> None:
    windows = build_windows([(f"part {i}", i, i) for i in range(100)], max_tokens=3)
    picked = sample_windows(windows, 4)
    assert [w.page_start for w in picked] == [0, 25, 50, 75]
    assert sample_windows(windows[:3], 4) == windows[:3]


def test_parse_guide_tidies_the_model_output() -> None:
    guide = parse_guide(GUIDE_JSON)
    assert guide["topics"] == ["Travel", "Annual leave", "Security"]
    assert guide["questions"] == [
        "How many leave days do employees get?",
        "When is business class allowed?",
    ]


@pytest.mark.parametrize("raw", ["not json", "[]", json.dumps({"summary": "  "})])
def test_parse_guide_rejects_unusable_output(raw: str) -> None:
    with pytest.raises(GuideError):
        parse_guide(raw)


def guide(summary: str, notes: str) -> dict[str, object]:
    return {"summary": summary, "topics": ["t"], "sections": [{"notes": notes}]}


def test_timeline_keeps_only_dates_found_in_the_sources() -> None:
    material = "## Report\nThe plant opened on 12 March 1998. In 2004 it was sold."
    timeline = (
        "## Timeline\n"
        "- **12 March 1998** — The plant opened (Report)\n"
        "- **2001** — Something the model made up (Report)\n"
        "**2004**: The plant was sold (Report)\n"
        "## People\n- Nobody named"
    )
    text, dropped = keep_grounded_dates(timeline, material)
    assert dropped == 1
    assert "1998" in text and "2004" in text and "2001" not in text
    assert "## People" in text


def test_timeline_with_only_invented_dates_says_there_are_none() -> None:
    timeline = (
        "## Timeline\n**2023-04-01**: 27 days of leave (Handbook)\n**2023-04-01**: Laptops lock\n"
    )
    text, dropped = keep_grounded_dates(timeline, "Employees get 27 days of leave.")
    assert dropped == 2
    assert text == NO_TIMELINE


def test_material_uses_full_notes_when_they_fit() -> None:
    material, used = build_material(
        [("A", guide("Sum A", "- note A")), ("B", guide("Sum B", "- note B"))], 1000
    )
    assert used == ["A", "B"]
    assert "- note A" in material and "- note B" in material


def test_material_falls_back_to_summaries_then_upgrades_what_fits() -> None:
    big, small = "- " + "x" * 3000, "- small note"
    material, used = build_material(
        [("Big", guide("Sum big", big)), ("Small", guide("Sum small", small))], 300
    )
    assert used == ["Big", "Small"]
    assert big not in material  # too large: summary and topics only
    assert "- small note" in material  # fits: full notes restored
    assert "Sum big" in material


# --- with a database and the fake model ---------------------------------------------------------


async def handbook_sources(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
) -> tuple[str, list[uuid.UUID]]:
    await register(client)
    notebook_id = await create_notebook(client)
    await upload(client, notebook_id, make_pdf(HANDBOOK_PAGES), filename="handbook.pdf")
    await upload(client, notebook_id, make_docx(), filename="policy.docx")
    await ingest_queued(queue, sessions, storage, settings)
    return notebook_id, list(queue.ingested)


async def test_ingestion_marks_the_guide_pending(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
) -> None:
    notebook_id, _ = await handbook_sources(client, queue, sessions, storage, settings)
    listed = (await client.get(f"/api/notebooks/{notebook_id}/sources")).json()
    assert {s["guide_status"] for s in listed} == {"pending"}


async def test_guide_is_mapped_then_reduced_and_saved(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    app: FastAPI,
    db: AsyncSession,
) -> None:
    notebook_id, (pdf_id, _) = await handbook_sources(client, queue, sessions, storage, settings)
    llm = FakeOllama()
    llm.chat_replies = ["- Travel rules\n- Leave rules", GUIDE_JSON]

    await generate_guide(
        pdf_id,
        sessions=sessions,
        ollama=as_client(llm),
        redis=app.state.redis,
        settings=settings,
    )

    # One window for this short document, then the reduce step constrained to the schema.
    assert llm.chat_formats == [None, GUIDE_SCHEMA]
    assert "Part 1 of 1, pages 1-4" in llm.rewrite_requests[0][1]["content"]
    source = await db.get(Source, pdf_id)
    assert source is not None and source.guide_status == "ready" and source.guide
    assert str(source.guide["summary"]).startswith("An employee handbook")
    assert source.guide["sections"] == [
        {"page_start": 1, "page_end": 4, "notes": "- Travel rules\n- Leave rules"}
    ]

    listed = (await client.get(f"/api/notebooks/{notebook_id}/sources")).json()
    shown = next(s for s in listed if s["id"] == str(pdf_id))["guide"]
    assert shown["topics"] == ["Travel", "Annual leave", "Security"]
    assert "sections" not in shown  # internal notes aren't sent to the browser


async def test_guide_failures(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    app: FastAPI,
    db: AsyncSession,
) -> None:
    _, (pdf_id, docx_id) = await handbook_sources(client, queue, sessions, storage, settings)
    redis = app.state.redis

    offline = FakeOllama(fail=LLMUnavailableError("refused"))
    with pytest.raises(TransientGenerationError):
        await generate_guide(
            pdf_id,
            sessions=sessions,
            ollama=as_client(offline),
            redis=redis,
            settings=settings,
            final_attempt=False,
        )

    garbled = FakeOllama()
    garbled.chat_replies = ["- notes", "this is not json"]
    await generate_guide(
        docx_id, sessions=sessions, ollama=as_client(garbled), redis=redis, settings=settings
    )
    source = await db.get(Source, docx_id)
    assert source is not None and source.guide_status == "failed"
    assert source.guide == {"error": "The model returned an unreadable guide."}


async def test_studio_waits_for_guides_then_writes_from_them(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
    app: FastAPI,
    db: AsyncSession,
) -> None:
    notebook_id, (pdf_id, docx_id) = await handbook_sources(
        client, queue, sessions, storage, settings
    )
    resp = await client.post(f"/api/notebooks/{notebook_id}/studio", json={"kind": "faq"})
    note_id = uuid.UUID(resp.json()["id"])
    redis = app.state.redis
    llm = FakeOllama()

    # Guides still pending: try again later (or give up on the last attempt).
    with pytest.raises(GuidesNotReadyError):
        await generate_studio(
            note_id,
            sessions=sessions,
            ollama=as_client(llm),
            redis=redis,
            settings=settings,
            final_attempt=False,
        )

    for source_id in (pdf_id, docx_id):
        llm.chat_replies = ["- notes about leave", GUIDE_JSON]
        await generate_guide(
            source_id, sessions=sessions, ollama=as_client(llm), redis=redis, settings=settings
        )
    llm.chat_replies = ["### How many leave days?\nTwenty-seven (handbook)."]
    await generate_studio(
        note_id, sessions=sessions, ollama=as_client(llm), redis=redis, settings=settings
    )

    note = await db.get(Note, note_id)
    assert note is not None and note.status == "ready"
    assert note.content.startswith("### How many leave days?")
    assert note.meta is not None and note.meta["sources"] == ["handbook", "policy"]
    system, user = llm.rewrite_requests[-1]
    assert "FAQ" in system["content"] and "### heading" in system["content"]
    assert "## handbook" in user["content"] and "- notes about leave" in user["content"]


async def test_background_work_waits_for_chats(app: FastAPI) -> None:
    redis = app.state.redis
    await wait_for_chat_idle(redis)  # nothing running: returns at once

    await redis.set(f"chat:busy:{uuid.uuid4()}", "1")
    waiter = asyncio.create_task(wait_for_chat_idle(redis, poll_s=0.05))
    await asyncio.sleep(0.2)
    assert not waiter.done()
    await redis.flushdb()
    await asyncio.wait_for(waiter, 1)
