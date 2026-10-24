import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.prompts import NOT_FOUND
from app.chat.service import AnswerJob, answer
from app.chat.stream import Emit, relay
from app.core.config import Settings
from app.db.models import Message
from app.ingestion.pipeline import Sessions, ingest_source
from app.llm.ollama import LLMUnavailableError
from app.retrieval.search import retrieve
from app.storage import LocalStorage
from tests.conftest import ClientFactory
from tests.factories import (
    HANDBOOK_PAGES,
    FakeOllama,
    RecordingQueue,
    fake_embed,
    fake_vector,
    make_docx,
    make_pdf,
    parse_sse,
)
from tests.utils import create_notebook, register, upload


@pytest.fixture
def llm(app: FastAPI) -> FakeOllama:
    fake = FakeOllama()
    app.state.ollama = fake
    return fake


async def ready_notebook(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
) -> tuple[str, dict[str, str]]:
    """A notebook with the handbook PDF and the policy .docx fully ingested."""
    await register(client)
    notebook_id = await create_notebook(client)
    ids = {}
    for name, data in (("handbook.pdf", make_pdf(HANDBOOK_PAGES)), ("policy.docx", make_docx())):
        resp = await upload(client, notebook_id, data, filename=name)
        ids[name] = resp.json()["id"]
    for source_id in queue.ingested:
        await ingest_source(
            source_id,
            sessions=sessions,
            storage=storage,
            embed=fake_embed,
            settings=settings,
            ocr=None,
        )
    return notebook_id, ids


Setup = tuple[httpx.AsyncClient, str, dict[str, str]]


@pytest.fixture
async def setup(
    client: httpx.AsyncClient,
    queue: RecordingQueue,
    sessions: Sessions,
    storage: LocalStorage,
    settings: Settings,
) -> Setup:
    notebook_id, ids = await ready_notebook(client, queue, sessions, storage, settings)
    return client, notebook_id, ids


async def ask(
    client: httpx.AsyncClient, notebook_id: str, question: str, **extra: object
) -> list[tuple[str, dict[str, object]]]:
    resp = await client.post(
        f"/api/notebooks/{notebook_id}/chat", json={"message": question, **extra}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


async def test_answer_streams_with_citations_and_is_saved(setup: Setup, llm: FakeOllama) -> None:
    client, notebook_id, _ = setup
    events = await ask(client, notebook_id, "How many days of annual leave do employees get?")

    names = [name for name, _ in events]
    assert names[:3] == ["question", "searching", "sources"]
    assert names[-1] == "done"
    assert set(names[3:-1]) == {"token"}

    passages = events[2][1]["passages"]
    assert isinstance(passages, list) and passages
    assert any("27 days" in p["text"] for p in passages)
    assert [p["number"] for p in passages] == list(range(1, len(passages) + 1))

    streamed = "".join(str(data["text"]) for name, data in events if name == "token")
    assert streamed == llm.reply

    saved = events[-1][1]["message"]
    assert isinstance(saved, dict)
    assert saved["content"] == llm.reply
    assert saved["status"] == "complete"
    assert saved["meta"]["cited"] == [1]

    # The model got the rules, the numbered sources and the question.
    (prompt,) = llm.answer_requests
    assert prompt[0]["role"] == "system" and "ONLY the numbered sources" in prompt[0]["content"]
    assert prompt[-1]["content"].startswith("Sources:\n\n[1] (")
    assert prompt[-1]["content"].endswith(
        "Question: How many days of annual leave do employees get?"
    )

    history = (await client.get(f"/api/notebooks/{notebook_id}/messages")).json()
    assert [(m["role"], m["status"]) for m in history] == [
        ("user", "complete"),
        ("assistant", "complete"),
    ]


async def test_invented_citations_are_removed(setup: Setup, llm: FakeOllama) -> None:
    client, notebook_id, _ = setup
    llm.reply = "Leave is 27 days [1][42]."
    events = await ask(client, notebook_id, "annual leave days?")
    saved = events[-1][1]["message"]
    assert isinstance(saved, dict)
    assert saved["content"] == "Leave is 27 days [1]."


async def test_unrelated_question_is_answered_without_the_model(
    setup: Setup, llm: FakeOllama, settings: Settings
) -> None:
    client, notebook_id, _ = setup
    events = await ask(client, notebook_id, "Who won the football world cup?")
    tokens = "".join(str(d["text"]) for n, d in events if n == "token")
    assert tokens == NOT_FOUND
    assert llm.answer_requests == []
    saved = events[-1][1]["message"]
    assert isinstance(saved, dict) and saved["meta"]["not_found"] is True


async def test_follow_up_is_rewritten_for_search(setup: Setup, llm: FakeOllama) -> None:
    client, notebook_id, _ = setup
    await ask(client, notebook_id, "How many days of annual leave do employees get?")
    events = await ask(client, notebook_id, "And how many can I carry over?")

    assert events[1] == ("searching", {"query": llm.rewrite})
    assert llm.embedded[-1] == llm.rewrite
    # The second answer sees the first exchange, without its citation markers.
    second = llm.answer_requests[1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[2]["content"] == "Employees get 27 days of paid annual leave."
    assert second[-1]["content"].endswith("Question: And how many can I carry over?")


async def test_only_selected_sources_are_searched(setup: Setup, llm: FakeOllama) -> None:
    client, notebook_id, ids = setup
    events = await ask(client, notebook_id, "annual leave days", source_ids=[ids["policy.docx"]])
    passages = events[2][1]["passages"]
    assert isinstance(passages, list)
    assert {p["source_id"] for p in passages} == {ids["policy.docx"]}


async def test_model_server_down_saves_an_error(setup: Setup, llm: FakeOllama) -> None:
    client, notebook_id, _ = setup
    llm.fail = LLMUnavailableError("connection refused")
    events = await ask(client, notebook_id, "annual leave days?")
    name, data = events[-1]
    assert name == "error"
    assert "Ollama" in str(data["detail"])
    saved = data["message"]
    assert isinstance(saved, dict) and saved["status"] == "error"


async def test_one_answer_at_a_time_per_notebook(
    setup: Setup, llm: FakeOllama, app: FastAPI
) -> None:
    client, notebook_id, _ = setup
    await app.state.redis.set(f"chat:busy:{notebook_id}", "1")
    resp = await client.post(f"/api/notebooks/{notebook_id}/chat", json={"message": "hi"})
    assert resp.status_code == 409
    assert (await client.delete(f"/api/notebooks/{notebook_id}/messages")).status_code == 409

    await app.state.redis.delete(f"chat:busy:{notebook_id}")
    await ask(client, notebook_id, "annual leave?")
    # The lock is released once the answer is done.
    await ask(client, notebook_id, "annual leave again?")


async def test_needs_a_processed_source(client: httpx.AsyncClient, llm: FakeOllama) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    resp = await client.post(f"/api/notebooks/{notebook_id}/chat", json={"message": "hi"})
    assert resp.status_code == 409


async def test_clear_chat(setup: Setup, llm: FakeOllama) -> None:
    client, notebook_id, _ = setup
    await ask(client, notebook_id, "annual leave?")
    assert (await client.delete(f"/api/notebooks/{notebook_id}/messages")).status_code == 204
    assert (await client.get(f"/api/notebooks/{notebook_id}/messages")).json() == []


async def test_search_endpoint(setup: Setup, llm: FakeOllama) -> None:
    client, notebook_id, _ = setup
    resp = await client.post(
        f"/api/notebooks/{notebook_id}/search", json={"query": "laptop disk encryption"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["top_similarity"] > 0
    assert "encryption" in body["passages"][0]["text"]


async def test_source_content_by_section(setup: Setup, llm: FakeOllama) -> None:
    client, notebook_id, ids = setup
    resp = await client.get(f"/api/notebooks/{notebook_id}/sources/{ids['policy.docx']}/content")
    assert resp.status_code == 200
    assert resp.json()["sections"] == [
        {
            "heading": "Employee Handbook › Travel",
            "page": None,
            "text": "Economy class is required for flights under 6 hours.",
        },
        {
            "heading": "Employee Handbook › Leave",
            "page": None,
            "text": "Full-time employees receive 27 days of paid annual leave.\n\n"
            "Leave type | Days\nParental | 112",
        },
    ]


async def test_source_content_needs_a_processed_source(
    client: httpx.AsyncClient, queue: RecordingQueue
) -> None:
    await register(client)
    notebook_id = await create_notebook(client)
    source_id = (await upload(client, notebook_id, make_docx(), filename="p.docx")).json()["id"]
    resp = await client.get(f"/api/notebooks/{notebook_id}/sources/{source_id}/content")
    assert resp.status_code == 409


async def test_chat_is_private(setup: Setup, llm: FakeOllama, make_client: ClientFactory) -> None:
    _, notebook_id, ids = setup
    bob = make_client()
    await register(bob, email="bob@example.com")
    base = f"/api/notebooks/{notebook_id}"
    assert (await bob.post(f"{base}/chat", json={"message": "hi"})).status_code == 404
    assert (await bob.get(f"{base}/messages")).status_code == 404
    assert (await bob.delete(f"{base}/messages")).status_code == 404
    assert (await bob.post(f"{base}/search", json={"query": "hi"})).status_code == 404
    content = f"{base}/sources/{ids['policy.docx']}/content"
    assert (await bob.get(content)).status_code == 404


async def test_hybrid_retrieval_finds_exact_terms(setup: Setup, sessions: Sessions) -> None:
    _, notebook_id, ids = setup
    async with sessions() as db:
        result = await retrieve(
            db,
            notebook_id=uuid.UUID(notebook_id),
            source_ids=[uuid.UUID(i) for i in ids.values()],
            query="receipt 25 euros",
            query_vector=fake_vector("receipt 25 euros"),
            candidates=30,
            top_k=6,
            token_budget=5000,
        )
    assert "receipt is required" in result.passages[0].text
    assert result.top_similarity is not None and result.top_similarity > 0


# --- Stop / disconnect --------------------------------------------------------------------------


async def test_stopping_keeps_the_partial_answer(
    setup: Setup, sessions: Sessions, settings: Settings, db: AsyncSession
) -> None:
    _, notebook_id, ids = setup
    slow = FakeOllama(reply="one two three four five six seven eight nine ten [1].", delay=0.05)
    tokens: list[str] = []
    three_tokens = asyncio.Event()

    async def emit(event: str, data: dict[str, object]) -> None:
        if event == "token":
            tokens.append(str(data["text"]))
            if len(tokens) == 3:
                three_tokens.set()

    job = AnswerJob(
        uuid.UUID(notebook_id), "annual leave days?", [uuid.UUID(i) for i in ids.values()], []
    )
    task = asyncio.create_task(
        answer(job, emit=emit, sessions=sessions, ollama=slow, settings=settings)  # type: ignore[arg-type]
    )
    await asyncio.wait_for(three_tokens.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    saved = (await db.scalars(select(Message).where(Message.role == "assistant"))).one()
    assert saved.status == "stopped"
    assert saved.content == "".join(tokens).strip()
    assert 0 < len(saved.content) < len(slow.reply)


async def test_closing_the_stream_cancels_the_work() -> None:
    cancelled = asyncio.Event()

    async def produce(emit: Emit) -> None:
        try:
            await emit("tick", {})
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    stream = relay(produce)
    assert (await anext(stream)).startswith("event: tick")
    await stream.aclose()
    await asyncio.wait_for(cancelled.wait(), 1)


async def test_work_stops_when_nobody_reads_the_stream() -> None:
    cancelled = asyncio.Event()

    async def produce(emit: Emit) -> None:
        try:
            for i in range(10_000):
                await emit("tick", {"i": i})
        except asyncio.CancelledError:
            cancelled.set()
            raise

    stream = relay(produce, idle_timeout=0.05)
    await anext(stream)  # start it, then stop reading without closing (an abandoned response)
    await asyncio.wait_for(cancelled.wait(), 2)
    await stream.aclose()
