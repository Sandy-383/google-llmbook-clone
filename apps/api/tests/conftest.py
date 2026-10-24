"""Tests run against real Postgres and Redis (docker compose locally, service containers in CI).

Each test gets a fresh app, a truncated database and an empty Redis DB. The model server is
deliberately unreachable: nothing here may depend on Ollama.
"""

import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.base import Base
from app.db.session import build_engine, build_sessionmaker
from app.ingestion.pipeline import Sessions
from app.storage import LocalStorage
from tests.factories import RecordingQueue
from tests.utils import client_for, running_app

API_DIR = Path(__file__).resolve().parents[1]

ClientFactory = Callable[[], httpx.AsyncClient]


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return Settings(
        storage_dir=tmp_path_factory.mktemp("files"),
        environment="test",
        log_json=False,
        log_level="WARNING",
        database_url=os.environ.get(
            "TEST_DATABASE_URL",
            # 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and stalls.
            "postgresql+asyncpg://llmbook:llmbook@127.0.0.1:5432/llmbook_test",
        ),
        db_null_pool=True,
        redis_url=os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        ollama_base_url="http://127.0.0.1:9",
        login_rate_limit=5,
        login_ip_rate_limit=100,
        # The fake bag-of-words embeddings score lower than a real model does.
        retrieval_min_similarity=0.1,
        register_rate_limit=100,
    )


@pytest.fixture(scope="session", autouse=True)
def _migrated_database(settings: Settings) -> None:
    """Round-trip the migrations once per run, so a broken downgrade fails CI too."""
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
async def _clean_state(settings: Settings) -> None:
    engine = build_engine(settings.database_url, null_pool=True)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await engine.dispose()

    redis = Redis.from_url(settings.redis_url)
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = build_engine(settings.database_url, null_pool=True)
    async with build_sessionmaker(engine)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    async with running_app(settings) as app:
        yield app


@pytest.fixture
async def make_client(app: FastAPI) -> AsyncIterator[ClientFactory]:
    """Each client has its own cookie jar, i.e. acts as a separate browser."""
    clients: list[httpx.AsyncClient] = []

    def factory() -> httpx.AsyncClient:
        client = client_for(app)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


@pytest.fixture
def client(make_client: ClientFactory) -> httpx.AsyncClient:
    return make_client()


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recorder: RecordingQueue = app.state.task_queue
    return recorder


@pytest.fixture
def storage(app: FastAPI) -> LocalStorage:
    local: LocalStorage = app.state.storage
    return local


@pytest.fixture
async def sessions(settings: Settings) -> AsyncIterator[Sessions]:
    engine = build_engine(settings.database_url, null_pool=True)
    yield build_sessionmaker(engine)
    await engine.dispose()
